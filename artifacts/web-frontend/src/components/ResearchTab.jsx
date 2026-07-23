import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  getResearchHypotheses,
  archiveHypothesis,
  archiveAllHypotheses,
  submitResearchHypothesis,
  getResearchJob,
  runDiscoveryBatch,
  runContinuousDiscovery,
  stopContinuousDiscovery,
  generateHypothesisReport,
  saveReport,
} from "../api.js";

const POLL_MS = 3000;

function isTrue(value) {
  const v = String(value ?? "").toLowerCase();
  return v === "true" || v === "1";
}

function fmtP(v) {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return "—";
  return n.toExponential(2);
}

// Full auditable write-up for a doubly-passing hypothesis. Renders the audit
// numbers straight from the registry `facts` (never the LLM) alongside the
// Opus 4.8 narrative, which is grounded strictly in those same numbers.
function ReportPanel({ hypothesisId, onSaved }) {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [saveState, setSaveState] = useState({ busy: false, saved: false, error: null });
  // Ref guard: prevents double-submission even if state update hasn't flushed yet
  // (e.g. double-click before React re-renders the disabled button).
  const saveInFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    setSaveState({ busy: false, saved: false, error: null });
    saveInFlight.current = false;
    generateHypothesisReport(hypothesisId)
      .then((data) => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch((err) => { if (!cancelled) setState({ loading: false, error: err.message, data: null }); });
    return () => { cancelled = true; };
  }, [hypothesisId]);

  const handleSave = async () => {
    if (!state.data || saveInFlight.current) return;
    saveInFlight.current = true;
    setSaveState({ busy: true, saved: false, error: null });
    try {
      await saveReport({
        hypothesis_id: hypothesisId,
        hypothesis_text: state.data.facts?.hypothesis_text ?? null,
        report_markdown: state.data.report_markdown,
        facts: state.data.facts ?? null,
        generated_at: state.data.generated_at ?? null,
      });
      setSaveState({ busy: false, saved: true, error: null });
      onSaved?.();
    } catch (err) {
      saveInFlight.current = false;
      setSaveState({ busy: false, saved: false, error: err.message });
    }
  };

  if (state.loading)
    return (
      <div style={{ padding: "1rem 1.25rem", fontSize: "0.75rem", color: "var(--silver)", fontFamily: "monospace" }}>
        ⟳ Opus 4.8 is writing the full report from the stored statistics…
      </div>
    );
  if (state.error)
    return (
      <div style={{ padding: "1rem 1.25rem", fontSize: "0.75rem", color: "var(--oxide)" }}>
        Could not generate report: {state.error}
      </div>
    );

  const { facts, report_markdown, generated_at } = state.data;
  const checks = facts?.confound_check?.checks ?? [];

  const cell = { padding: "4px 8px", fontFamily: "monospace", fontSize: "0.62rem", whiteSpace: "nowrap" };
  const hcell = { ...cell, color: "var(--silver-dim)", textTransform: "uppercase", letterSpacing: "0.1em", fontSize: "0.55rem", textAlign: "left" };

  return (
    <div style={{ padding: "1.25rem 1.5rem", backgroundColor: "rgba(199,202,209,0.03)" }}>
      {/* ── Audit numbers, rendered directly from the registry facts ── */}
      <div style={{
        fontFamily: "monospace", fontSize: "0.55rem", textTransform: "uppercase",
        letterSpacing: "0.2em", color: "var(--brass)", marginBottom: "0.6rem",
      }}>
        Audit numbers — from the registry
      </div>

      <div style={{ overflowX: "auto", marginBottom: "1rem" }}>
        <table style={{ borderCollapse: "collapse", color: "var(--silver)" }}>
          <thead>
            <tr>
              {["Frame", "Test", "OR", "95% CI", "n", "Discovery raw p", "FDR q", "Disc.", "Confirm raw p", "Conf."].map((h, i) => (
                <th key={i} style={hcell}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(facts?.framings ?? []).map((f, i) => (
              <tr key={i} style={{ borderTop: "1px solid rgba(199,202,209,0.1)" }}>
                <td style={{ ...cell, color: "var(--silver)" }}>{f.framing ?? "—"}</td>
                <td style={{ ...cell, color: "var(--silver)" }}>{f.test_type ?? "—"}</td>
                <td style={{ ...cell, color: "var(--paper)" }}>{f.effect_size ? f.effect_size.odds_ratio : "—"}</td>
                <td style={{ ...cell, color: "var(--silver)" }}>
                  {f.effect_size ? `[${f.effect_size.ci_low}, ${f.effect_size.ci_high}]` : "—"}
                </td>
                <td style={cell}>{f.effect_size ? f.effect_size.n : "—"}</td>
                <td style={cell}>{fmtP(f.discovery_raw_p)}</td>
                <td style={cell}>{fmtP(f.discovery_fdr_q)}</td>
                <td style={cell}><PassBadge value={f.discovery_pass} /></td>
                <td style={cell}>{fmtP(f.confirmation_raw_p)}</td>
                <td style={cell}><PassBadge value={f.confirmation_pass} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {checks.length > 0 && (
        <div style={{ overflowX: "auto", marginBottom: "1.25rem" }}>
          <div style={{
            fontFamily: "monospace", fontSize: "0.55rem", textTransform: "uppercase",
            letterSpacing: "0.2em", color: "var(--brass)", marginBottom: "0.5rem",
          }}>
            Confound checks
          </div>
          <table style={{ borderCollapse: "collapse", color: "var(--silver)" }}>
            <thead>
              <tr>
                {["Confound", "Unadj. OR", "Adj. OR", "Adj. 95% CI", "Adj. p", "Survives?"].map((h, i) => (
                  <th key={i} style={hcell}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {checks.map((c, i) => {
                const a = c.adjustment_result;
                const survives = a == null ? null : a.survives_adjustment;
                return (
                  <tr key={i} style={{ borderTop: "1px solid rgba(199,202,209,0.1)" }}>
                    <td style={{ ...cell, whiteSpace: "normal", maxWidth: "16rem", color: "var(--silver)" }}>{c.confound_name}</td>
                    <td style={cell}>{a?.or_unadjusted != null ? Number(a.or_unadjusted).toPrecision(3) : "—"}</td>
                    <td style={{ ...cell, color: "var(--paper)" }}>{a?.or_adjusted != null ? Number(a.or_adjusted).toPrecision(3) : "—"}</td>
                    <td style={cell}>
                      {a?.ci_low_adjusted != null ? `[${a.ci_low_adjusted}, ${a.ci_high_adjusted}]` : "—"}
                    </td>
                    <td style={cell}>{a?.p_adjusted != null ? Number(a.p_adjusted).toExponential(2) : "—"}</td>
                    <td style={cell}>
                      {survives === true
                        ? <span style={{ color: "#7ec97e" }}>survives</span>
                        : survives === false
                          ? <span style={{ color: "var(--oxide)" }}>DOES NOT survive</span>
                          : <span style={{ color: "var(--silver-dim)" }} title={a?.note || "not testable"}>not testable</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Opus narrative, grounded strictly in the numbers above ── */}
      <div className="eyebrow" style={{ marginBottom: "0.5rem" }}>Narrative — Claude Opus 4.8</div>
      <div className="report-markdown" style={{ fontSize: "0.8rem", color: "var(--paper)", lineHeight: 1.6 }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{report_markdown}</ReactMarkdown>
      </div>

      <div style={{ marginTop: "1rem", display: "flex", alignItems: "center", gap: "1rem", flexWrap: "wrap" }}>
        <button
          onClick={handleSave}
          disabled={saveState.busy || saveState.saved}
          title="Save a permanent snapshot of this report to the Saved Reports tab"
          className={`btn btn-sm ${saveState.saved ? "btn-saved" : "btn-ghost-brass"}`}
        >
          {saveState.busy ? "saving…" : saveState.saved ? "✓ saved to Saved Reports" : "Save report"}
        </button>
        {saveState.error && (
          <span style={{ fontSize: "0.62rem", color: "var(--oxide)", fontFamily: "monospace" }}>
            {saveState.error}
          </span>
        )}
      </div>

      {generated_at && (
        <div style={{ marginTop: "0.75rem", fontSize: "0.55rem", fontFamily: "monospace", color: "var(--silver-dim)" }}>
          generated {new Date(generated_at).toLocaleString()} · numbers read directly from the registry
        </div>
      )}
    </div>
  );
}

function PassBadge({ value }) {
  const v = String(value ?? "");
  if (v === "True" || v === "true" || v === "1")
    return badgePass;
  if (v === "False" || v === "false" || v === "0")
    return badgeFail;
  return <span style={{ color: "var(--silver-dim)", fontSize: "0.62rem" }}>—</span>;
}

const badgePass = (
  <span style={{
    background: "rgba(76,175,80,0.13)", color: "#7ec97e",
    borderRadius: "3px", padding: "1px 7px",
    fontSize: "0.62rem", fontFamily: "monospace", letterSpacing: "0.05em",
  }}>PASS</span>
);
const badgeFail = (
  <span style={{
    background: "rgba(155,74,63,0.13)", color: "var(--oxide)",
    borderRadius: "3px", padding: "1px 7px",
    fontSize: "0.62rem", fontFamily: "monospace", letterSpacing: "0.05em",
  }}>FAIL</span>
);

function HypothesisTable({ hypotheses, loading, onArchive }) {
  const [archiving, setArchiving] = useState(null); // hypothesis_id being toggled
  const [expanded, setExpanded] = useState(null); // hypothesis_id whose report is open

  if (loading)
    return <p style={{ color: "var(--silver)", fontSize: "0.8rem", padding: "1.5rem 0" }}>Loading registry…</p>;
  if (!hypotheses.length)
    return (
      <p style={{ color: "var(--silver)", fontSize: "0.8rem", padding: "1.5rem 0", lineHeight: 1.6 }}>
        No hypotheses in the registry yet.<br />
        Run <code style={{ color: "var(--brass)" }}>python -m data_prep.run_discovery</code> or submit one below.
      </p>
    );

  const handleArchive = async (h) => {
    const newVal = h.archived !== true;
    setArchiving(h.hypothesis_id);
    try {
      await archiveHypothesis(h.hypothesis_id, newVal);
      onArchive?.();
    } finally {
      setArchiving(null);
    }
  };

  return (
    <div style={{ overflowX: "auto", borderRadius: "6px", border: "1px solid rgba(199,202,209,0.14)" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.72rem", color: "var(--ink)" }}>
        <thead>
          <tr style={{ backgroundColor: "rgba(199,202,209,0.05)", borderBottom: "1px solid rgba(199,202,209,0.18)" }}>
            {[
              "Hypothesis", "Test", "Frame", "Raw p", "FDR q",
              "Discovery", "Confirm", "Confound", "Domain", "Report", "",
            ].map((h, i) => (
              <th key={i} style={{
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
            const isArchived = h.archived === true;
            const confoundSummary = (() => {
              if (!h.confound_check_summary) return null;
              try { return JSON.parse(h.confound_check_summary); }
              catch { return null; }
            })();
            const hasConfound = confoundSummary?.status === "completed";
            const busy = archiving === h.hypothesis_id;
            const passesBoth = isTrue(h.discovery_pass) && isTrue(h.confirmation_pass);
            const isOpen = expanded === h.hypothesis_id;
            return (
              <Fragment key={i}>
              <tr
                style={{
                  borderBottom: isOpen ? "none" : "1px solid rgba(199,202,209,0.09)",
                  opacity: isArchived ? 0.45 : 1,
                  transition: "opacity 0.15s ease",
                }}
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
                <td style={{ padding: "7px 8px", whiteSpace: "nowrap" }}>
                  {passesBoth ? (
                    <button
                      onClick={() => setExpanded(isOpen ? null : h.hypothesis_id)}
                      title="Full auditable write-up (Opus 4.8, grounded in the stored numbers)"
                      className={`btn btn-xs btn-ghost-brass${isOpen ? " active" : ""}`}
                    >
                      {isOpen ? "hide report" : "full report"}
                    </button>
                  ) : (
                    <span style={{ color: "var(--silver-dim)", fontSize: "0.58rem" }}>—</span>
                  )}
                </td>
                <td style={{ padding: "7px 8px", whiteSpace: "nowrap" }}>
                  <button
                    onClick={() => handleArchive(h)}
                    disabled={busy}
                    title={isArchived ? "Restore to active view" : "Archive this entry"}
                    className={`btn btn-xs ${isArchived ? "btn-ghost-brass" : "btn-ghost"}`}
                  >
                    {busy ? "…" : isArchived ? "restore" : "archive"}
                  </button>
                </td>
              </tr>
              {isOpen && (
                <tr style={{ borderBottom: "1px solid rgba(199,202,209,0.09)" }}>
                  <td colSpan={11} style={{ padding: 0 }}>
                    <ReportPanel hypothesisId={h.hypothesis_id} />
                  </td>
                </tr>
              )}
              </Fragment>
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
      <div className="eyebrow" style={{ marginBottom: "0.5rem" }}>Submit your own hypothesis</div>
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
            className="btn btn-primary btn-sm"
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

function DiscoveryBatch({ onCompleted }) {
  const [busy, setBusy] = useState(false);
  const [jobState, setJobState] = useState(null);
  const [error, setError] = useState(null);
  const [mode, setMode] = useState("single"); // "single" | "continuous"
  const [activeJobId, setActiveJobId] = useState(null);
  const pollRef = useRef(null);

  const stopPoll = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  useEffect(() => stopPoll, [stopPoll]);

  const handleRun = useCallback(async () => {
    setBusy(true);
    setJobState(null);
    setError(null);
    setActiveJobId(null);
    stopPoll();
    try {
      const starter = mode === "continuous" ? runContinuousDiscovery : runDiscoveryBatch;
      const { job_id } = await starter();
      setActiveJobId(job_id);
      setJobState({ job_id, status: "pending" });
      pollRef.current = setInterval(async () => {
        try {
          const job = await getResearchJob(job_id);
          setJobState(job);
          if (job.status === "completed" || job.status === "error") {
            stopPoll();
            setActiveJobId(null);
            setBusy(false);
            if (job.status === "completed") onCompleted?.();
          }
        } catch (err) {
          setError(err.message);
          stopPoll();
          setActiveJobId(null);
          setBusy(false);
        }
      }, POLL_MS);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }, [mode, onCompleted, stopPoll]);

  const handleStop = useCallback(async () => {
    if (!activeJobId) return;
    try {
      await stopContinuousDiscovery(activeJobId);
    } catch (err) {
      setError(`Stop request failed: ${err.message}`);
    }
  }, [activeJobId]);

  const statusMsg = (() => {
    if (!jobState) return null;
    if (jobState.status === "pending") return "⟳ Queued…";
    if (jobState.status === "running") {
      const prog = jobState.result?.progress;
      if (prog)
        return `⟳ Batch ${prog.batch_num} — ${prog.domains_explored} domain(s), ${prog.hypotheses_reviewed} hypothesis(es) reviewed…`;
      return "⟳ Opus 4.8 + GPT-5.6 Sol generating domains → lead review → testing…";
    }
    if (jobState.status === "completed") {
      const s = jobState.result?.summary;
      if (s?.mode === "continuous")
        return `✓ Done — ${s.batches_run} batch(es), ${s.domains_explored} domain(s), `
          + `${s.hypotheses_reviewed} hypothesis(es)${s.confirmed ? `; ${s.confirmed} confirmed` : ""}`;
      if (s)
        return `✓ Done — ${s.tests_run} test(s) across ${s.domains?.length ?? 0} domain(s); `
          + `${s.surviving_discovery} passed discovery, ${s.confirmed} confirmed`;
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
      marginBottom: "2rem", padding: "1.5rem 1.75rem",
      border: "1px solid rgba(184,151,90,0.35)", borderRadius: "8px",
      backgroundColor: "rgba(184,151,90,0.05)",
    }}>
      <div className="eyebrow" style={{ marginBottom: "0.5rem" }}>Autonomous discovery</div>
      <p style={{ fontSize: "0.75rem", color: "var(--silver)", lineHeight: 1.6, marginBottom: "1rem", maxWidth: "52rem" }}>
        Runs the full three-model pipeline with <strong style={{ color: "var(--paper)" }}>no hypothesis from you</strong>:
        two generators (<strong style={{ color: "var(--paper)" }}>Claude Opus 4.8</strong> and{" "}
        <strong style={{ color: "var(--paper)" }}>GPT-5.6 Sol</strong>) each propose their own bisociative domains, a lead
        reviewer consolidates them, and every ready hypothesis is tested on the discovery split, FDR-corrected over the
        whole cumulative log, then confirmed on the holdout half and confound-checked. Results append to the same registry
        below. This takes several minutes and makes many model calls.
      </p>

      {/* Mode selector */}
      <div className="mode-selector">
        {[
          { value: "single", label: "Single batch" },
          { value: "continuous", label: "Continuous — until double-pass" },
        ].map(({ value, label }) => (
          <button
            key={value}
            type="button"
            onClick={() => !busy && setMode(value)}
            className={`mode-option${mode === value ? " mode-option--active" : ""}${busy ? " mode-option--disabled" : ""}`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p style={{ color: "var(--oxide)", fontSize: "0.72rem", marginBottom: "0.5rem" }}>{error}</p>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "1.25rem", flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={handleRun}
          disabled={busy}
          className="btn btn-primary"
        >
          {busy
            ? (mode === "continuous" ? "Continuous discovery running…" : "Discovery batch running…")
            : (mode === "continuous" ? "Run until found" : "Run new discovery batch")}
        </button>

        {/* Stop button — only shown during a continuous run */}
        {busy && mode === "continuous" && activeJobId && (
          <button
            type="button"
            onClick={handleStop}
            className="btn btn-danger btn-sm"
          >
            Stop after this batch
          </button>
        )}

        {statusMsg && (
          <span style={{ fontSize: "0.72rem", fontFamily: "monospace", color: statusColor }}>
            {statusMsg}
          </span>
        )}
      </div>
    </div>
  );
}

export default function ResearchTab({ onRefresh }) {
  const [allHypotheses, setAllHypotheses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showArchived, setShowArchived] = useState(false);
  const [bulkArchiving, setBulkArchiving] = useState(false);

  const fetchHypotheses = useCallback(async () => {
    try {
      const data = await getResearchHypotheses();
      setAllHypotheses(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHypotheses(); }, [fetchHypotheses]);

  const handleArchiveAll = useCallback(async () => {
    if (!window.confirm(`Archive all ${allHypotheses.length} hypotheses? You can restore them individually or use "Restore all".`)) return;
    setBulkArchiving(true);
    try {
      await archiveAllHypotheses(true);
      await fetchHypotheses();
    } finally {
      setBulkArchiving(false);
    }
  }, [allHypotheses.length, fetchHypotheses]);

  const archivedCount = allHypotheses.filter((h) => h.archived === true).length;
  const visibleHypotheses = showArchived
    ? allHypotheses
    : allHypotheses.filter((h) => h.archived !== true);

  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "2.5rem 1.5rem" }}>
      <header style={{ marginBottom: "2rem" }}>
        <div className="eyebrow" style={{ marginBottom: "0.35rem" }}>Bisociation Registry</div>
        <h2 style={{ fontSize: "2rem", fontWeight: 700, color: "var(--paper)", margin: 0, lineHeight: 1.1 }}>
          Research Hypotheses
        </h2>
        <p style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--silver)", lineHeight: 1.6, maxWidth: "52rem" }}>
          Every hypothesis ever tested across all pipeline runs and user submissions. FDR correction is
          cumulative — adding a new hypothesis updates the adjusted q-values for all prior entries.
          Methodology (test type, threshold, correction method) is locked at submission time, before any
          result is computed. Re-testing under different methodology creates a new log entry, never an overwrite.
        </p>
        <div style={{
          marginTop: "0.75rem", padding: "0.6rem 0.85rem",
          borderLeft: "2px solid var(--brass)", backgroundColor: "rgba(180,140,60,0.07)",
          borderRadius: "0 4px 4px 0", maxWidth: "52rem",
        }}>
          <span style={{ fontFamily: "monospace", fontSize: "0.65rem", textTransform: "uppercase", letterSpacing: "0.14em", color: "var(--brass)", display: "block", marginBottom: "0.25rem" }}>
            Registry reset notice
          </span>
          <span style={{ fontSize: "0.75rem", color: "var(--silver)", lineHeight: 1.55 }}>
            Cumulative testing history reset on July 21, 2026. 79 hypothesis-test pairs recorded prior to
            this date used indication/drug-name keyword matching only — before real molecular and
            bioactivity features (PubChem, ChEMBL) were added — and are excluded from the current
            cumulative FDR count.
          </span>
        </div>
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

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem", gap: "0.75rem", flexWrap: "wrap" }}>
        <span style={{
          fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
          letterSpacing: "0.16em", color: "var(--silver-dim)",
        }}>
          {loading ? "…" : `${visibleHypotheses.length} of ${allHypotheses.length} test${allHypotheses.length !== 1 ? "s" : ""} shown`}
        </span>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          {visibleHypotheses.length > 0 && (
            <button
              onClick={handleArchiveAll}
              disabled={bulkArchiving}
              title="Archive all hypotheses at once — none are deleted, use 'show archived' to restore individually"
              className="btn btn-xs btn-ghost"
            >
              {bulkArchiving ? "archiving…" : "Archive all"}
            </button>
          )}
          {archivedCount > 0 && (
            <button
              onClick={() => setShowArchived((v) => !v)}
              className={`btn btn-xs ${showArchived ? "btn-ghost-brass" : "btn-ghost"}`}
            >
              {showArchived ? `hide archived (${archivedCount})` : `show archived (${archivedCount})`}
            </button>
          )}
          <button
            onClick={fetchHypotheses}
            className="btn btn-xs btn-ghost"
          >
            Refresh
          </button>
        </div>
      </div>

      <DiscoveryBatch onCompleted={fetchHypotheses} />
      <HypothesisTable hypotheses={visibleHypotheses} loading={loading} onArchive={fetchHypotheses} />
      <SubmitHypothesis onSubmitted={fetchHypotheses} />
    </div>
  );
}
