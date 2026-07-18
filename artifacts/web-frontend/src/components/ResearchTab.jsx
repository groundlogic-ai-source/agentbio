import { useCallback, useEffect, useRef, useState } from "react";
import {
  getResearchHypotheses,
  submitResearchHypothesis,
  getResearchJob,
} from "../api.js";

const POLL_MS = 3000;

function PassBadge({ value }) {
  const v = String(value ?? "");
  if (v === "True" || v === "true" || v === "1")
    return (
      <span style={{
        background: "rgba(76,175,80,0.13)", color: "#7ec97e",
        borderRadius: "3px", padding: "1px 7px",
        fontSize: "0.62rem", fontFamily: "monospace", letterSpacing: "0.05em",
      }}>PASS</span>
    );
  if (v === "False" || v === "false" || v === "0")
    return (
      <span style={{
        background: "rgba(155,74,63,0.13)", color: "var(--oxide)",
        borderRadius: "3px", padding: "1px 7px",
        fontSize: "0.62rem", fontFamily: "monospace",
      }}>fail</span>
    );
  return <span style={{ color: "var(--silver-dim)", fontSize: "0.62rem" }}>—</span>;
}

function HypothesisTable({ hypotheses, loading }) {
  if (loading)
    return <p style={{ color: "var(--silver)", fontSize: "0.8rem", padding: "1.5rem 0" }}>Loading registry…</p>;
  if (!hypotheses.length)
    return (
      <p style={{ color: "var(--silver)", fontSize: "0.8rem", padding: "1.5rem 0", lineHeight: 1.6 }}>
        No hypotheses in the registry yet.<br />
        Run <code style={{ color: "var(--brass)" }}>python -m data_prep.run_discovery</code> or submit one below.
      </p>
    );

  return (
    <div style={{ overflowX: "auto", borderRadius: "6px", border: "1px solid rgba(199,202,209,0.14)" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.72rem", color: "var(--ink)" }}>
        <thead>
          <tr style={{ backgroundColor: "rgba(199,202,209,0.05)", borderBottom: "1px solid rgba(199,202,209,0.18)" }}>
            {[
              "Hypothesis", "Test", "Frame", "Raw p", "FDR q",
              "Discovery", "Confirm", "Confound", "Domain",
            ].map((h) => (
              <th key={h} style={{
                padding: "6px 10px", textAlign: "left", whiteSpace: "nowrap",
                fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
                letterSpacing: "0.15em", fontWeight: 400, color: "var(--silver-dim)",
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {hypotheses.map((h, i) => {
            const txt = h.resulting_hypothesis_text || "";
            const confoundSummary = (() => {
              if (!h.confound_check_summary) return null;
              try { return JSON.parse(h.confound_check_summary); }
              catch { return null; }
            })();
            const hasConfound = confoundSummary?.status === "completed";
            return (
              <tr key={i} style={{ borderBottom: "1px solid rgba(199,202,209,0.09)" }}
                onMouseEnter={(e) => { e.currentTarget.style.backgroundColor = "rgba(199,202,209,0.04)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.backgroundColor = "transparent"; }}>
                <td style={{ padding: "7px 10px", maxWidth: "22rem", color: "var(--paper)" }}>
                  <span title={txt}>{txt.length > 90 ? txt.slice(0, 90) + "…" : txt}</span>
                </td>
                <td style={{ padding: "7px 10px", fontFamily: "monospace", color: "var(--silver)", whiteSpace: "nowrap" }}>
                  {h.discovery_test_type || "—"}
                </td>
                <td style={{ padding: "7px 10px", fontFamily: "monospace", color: "var(--silver-dim)", whiteSpace: "nowrap" }}>
                  {h.outcome_framing || "—"}
                </td>
                <td style={{ padding: "7px 10px", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                  {h.discovery_raw_p != null && h.discovery_raw_p !== ""
                    ? Number(h.discovery_raw_p).toExponential(2) : "—"}
                </td>
                <td style={{ padding: "7px 10px", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                  {h.discovery_fdr_p != null && h.discovery_fdr_p !== ""
                    ? Number(h.discovery_fdr_p).toExponential(2) : "—"}
                </td>
                <td style={{ padding: "7px 10px", whiteSpace: "nowrap" }}>
                  <PassBadge value={h.discovery_pass} />
                </td>
                <td style={{ padding: "7px 10px", whiteSpace: "nowrap" }}>
                  {h.confirmation_pass != null && h.confirmation_pass !== ""
                    ? <PassBadge value={h.confirmation_pass} />
                    : <span style={{ color: "var(--silver-dim)", fontSize: "0.62rem" }}>—</span>}
                </td>
                <td style={{ padding: "7px 10px", whiteSpace: "nowrap" }}>
                  {hasConfound
                    ? <span style={{ color: "var(--silver)", fontSize: "0.62rem", fontFamily: "monospace" }}
                        title={JSON.stringify(confoundSummary?.checks, null, 2)}>
                        {confoundSummary.checks?.length ?? 0} checked
                      </span>
                    : <span style={{ color: "var(--silver-dim)", fontSize: "0.62rem" }}>—</span>}
                </td>
                <td style={{ padding: "7px 10px", color: "var(--silver-dim)", maxWidth: "14rem" }}>
                  <span title={h.domain_description}>
                    {(h.domain_description || "").length > 45
                      ? h.domain_description.slice(0, 45) + "…"
                      : h.domain_description || "—"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SubmitHypothesis({ onSubmitted }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [jobState, setJobState] = useState(null);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const handleSubmit = useCallback(async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setJobState(null);
    setError(null);
    stopPoll();
    try {
      const { job_id } = await submitResearchHypothesis(text.trim());
      setJobState({ job_id, status: "pending" });
      pollRef.current = setInterval(async () => {
        try {
          const job = await getResearchJob(job_id);
          setJobState(job);
          if (job.status === "completed" || job.status === "error") {
            stopPoll();
            setBusy(false);
            if (job.status === "completed") onSubmitted?.();
          }
        } catch (err) {
          setError(err.message);
          stopPoll();
          setBusy(false);
        }
      }, POLL_MS);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }, [text, onSubmitted, stopPoll]);

  useEffect(() => stopPoll, [stopPoll]);

  const statusMsg = (() => {
    if (!jobState) return null;
    if (jobState.status === "pending") return "⟳ Queued…";
    if (jobState.status === "running") return "⟳ Opus parsing + testing on discovery split…";
    if (jobState.status === "completed") {
      const res = jobState.result;
      if (res?.tag === "NEEDS_ENRICHMENT") return "✓ Done — hypothesis needs enrichment (see table)";
      if (res?.tag === "DISCARDED") return `✓ Done — discarded: ${res.message}`;
      return "✓ Done — results in table above";
    }
    if (jobState.status === "error") return `✕ ${jobState.error_message}`;
    return null;
  })();

  const statusColor = jobState?.status === "error" ? "var(--oxide)"
    : jobState?.status === "completed" ? "var(--brass)"
    : "var(--silver)";

  return (
    <div style={{
      marginTop: "2.5rem", padding: "1.5rem 1.75rem",
      border: "1px solid rgba(199,202,209,0.2)", borderRadius: "8px",
      backgroundColor: "rgba(199,202,209,0.03)",
    }}>
      <div style={{
        fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
        letterSpacing: "0.22em", color: "var(--brass)", marginBottom: "0.5rem",
      }}>
        Submit your own hypothesis
      </div>
      <p style={{ fontSize: "0.75rem", color: "var(--silver)", lineHeight: 1.6, marginBottom: "1rem" }}>
        Write a testable claim in plain English. Claude Opus will parse it into the dataset DSL and test it on
        the <strong style={{ color: "var(--paper)" }}>same discovery split</strong> as pipeline runs, appending to
        the <strong style={{ color: "var(--paper)" }}>same cumulative FDR log</strong> — no separate accounting.
        Re-submission under different wording creates a new log entry; nothing is overwritten.
      </p>
      <form onSubmit={handleSubmit}>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
          rows={3}
          placeholder={'e.g. "Drugs whose generic names contain \'mab\' (monoclonal antibodies) show higher repurposing success in inflammatory indications."'}
          style={{
            width: "100%", boxSizing: "border-box",
            backgroundColor: "rgba(199,202,209,0.06)",
            border: "1px solid rgba(199,202,209,0.22)", borderRadius: "5px",
            color: "var(--paper)", fontSize: "0.8rem", padding: "0.65rem 0.8rem",
            resize: "vertical", fontFamily: "inherit", lineHeight: 1.5,
          }}
        />
        {error && (
          <p style={{ color: "var(--oxide)", fontSize: "0.72rem", marginTop: "0.4rem" }}>{error}</p>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "1.25rem", marginTop: "0.75rem", flexWrap: "wrap" }}>
          <button
            type="submit"
            disabled={busy || !text.trim()}
            style={{
              padding: "0.5rem 1.4rem", borderRadius: "4px", border: "none",
              backgroundColor: busy || !text.trim() ? "rgba(199,202,209,0.2)" : "var(--brass)",
              color: busy || !text.trim() ? "var(--silver)" : "var(--paper)",
              fontSize: "0.8rem", fontWeight: 600,
              cursor: busy || !text.trim() ? "default" : "pointer",
              transition: "background-color 0.15s ease",
            }}
            onMouseEnter={(e) => { if (!busy && text.trim()) e.currentTarget.style.backgroundColor = "var(--brass-deep)"; }}
            onMouseLeave={(e) => { if (!busy && text.trim()) e.currentTarget.style.backgroundColor = "var(--brass)"; }}
          >
            {busy ? "Processing…" : "Submit hypothesis"}
          </button>
          {statusMsg && (
            <span style={{ fontSize: "0.72rem", fontFamily: "monospace", color: statusColor }}>
              {statusMsg}
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

export default function ResearchTab({ onRefresh }) {
  const [hypotheses, setHypotheses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchHypotheses = useCallback(async () => {
    try {
      const data = await getResearchHypotheses();
      setHypotheses(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHypotheses(); }, [fetchHypotheses]);

  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "2.5rem 1.5rem" }}>
      <header style={{ marginBottom: "2rem" }}>
        <div style={{
          fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
          letterSpacing: "0.26em", color: "var(--brass)", marginBottom: "0.35rem",
        }}>
          Bisociation Registry
        </div>
        <h2 style={{ fontSize: "2rem", fontWeight: 700, color: "var(--paper)", margin: 0, lineHeight: 1.1 }}>
          Research Hypotheses
        </h2>
        <p style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--silver)", lineHeight: 1.6, maxWidth: "52rem" }}>
          Every hypothesis ever tested across all pipeline runs and user submissions. FDR correction is
          cumulative — adding a new hypothesis updates the adjusted q-values for all prior entries.
          Methodology (test type, threshold, correction method) is locked at submission time, before any
          result is computed. Re-testing under different methodology creates a new log entry, never an overwrite.
        </p>
      </header>

      {error && (
        <div style={{
          padding: "0.75rem 1rem", borderRadius: "5px",
          backgroundColor: "rgba(155,74,63,0.12)", color: "var(--oxide)",
          fontSize: "0.78rem", marginBottom: "1.25rem",
        }}>
          {error}
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <span style={{
          fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
          letterSpacing: "0.16em", color: "var(--silver-dim)",
        }}>
          {loading ? "…" : `${hypotheses.length} test${hypotheses.length !== 1 ? "s" : ""} in cumulative log`}
        </span>
        <button
          onClick={fetchHypotheses}
          style={{
            fontSize: "0.65rem", fontFamily: "monospace", color: "var(--silver)",
            background: "transparent", border: "1px solid rgba(199,202,209,0.22)",
            borderRadius: "3px", padding: "3px 10px", cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>

      <HypothesisTable hypotheses={hypotheses} loading={loading} />
      <SubmitHypothesis onSubmitted={fetchHypotheses} />
    </div>
  );
}
