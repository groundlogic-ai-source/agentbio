import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listSavedReports, getSavedReport, deleteSavedReport } from "../api.js";

function fmtP(v) {
  if (v == null || v === "") return "—";
  return Number(v).toExponential(2);
}

function AuditTables({ facts }) {
  if (!facts) return null;
  const framings = facts.framings ?? [];
  const checks = facts.confound_check?.checks ?? [];
  const cell = { padding: "4px 8px", fontFamily: "monospace", fontSize: "0.62rem", whiteSpace: "nowrap" };
  const hcell = { ...cell, color: "var(--silver-dim)", textTransform: "uppercase", letterSpacing: "0.1em", fontSize: "0.55rem", textAlign: "left" };

  return (
    <>
      <div style={{ overflowX: "auto", marginBottom: "1rem" }}>
        <table style={{ borderCollapse: "collapse", color: "var(--silver)" }}>
          <thead>
            <tr>
              {["Frame", "Test", "OR", "95% CI", "n", "Discovery raw p", "FDR q", "Confirm raw p"].map((h, i) => (
                <th key={i} style={hcell}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {framings.map((f, i) => (
              <tr key={i} style={{ borderTop: "1px solid rgba(199,202,209,0.1)" }}>
                <td style={{ ...cell, color: "var(--silver)" }}>{f.framing ?? "—"}</td>
                <td style={{ ...cell, color: "var(--silver)" }}>{f.test_type ?? "—"}</td>
                <td style={{ ...cell, color: "var(--paper)" }}>{f.effect_size ? f.effect_size.odds_ratio : "—"}</td>
                <td style={cell}>{f.effect_size ? `[${f.effect_size.ci_low}, ${f.effect_size.ci_high}]` : "—"}</td>
                <td style={cell}>{f.effect_size ? f.effect_size.n : "—"}</td>
                <td style={cell}>{fmtP(f.discovery_raw_p)}</td>
                <td style={cell}>{fmtP(f.discovery_fdr_q)}</td>
                <td style={cell}>{fmtP(f.confirmation_raw_p)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {checks.length > 0 && (
        <div style={{ overflowX: "auto", marginBottom: "1.25rem" }}>
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
                    <td style={cell}>{a?.ci_low_adjusted != null ? `[${a.ci_low_adjusted}, ${a.ci_high_adjusted}]` : "—"}</td>
                    <td style={cell}>{a?.p_adjusted != null ? Number(a.p_adjusted).toExponential(2) : "—"}</td>
                    <td style={cell}>
                      {survives === true
                        ? <span style={{ color: "#7ec97e" }}>survives</span>
                        : survives === false
                          ? <span style={{ color: "var(--oxide)" }}>DOES NOT survive</span>
                          : <span style={{ color: "var(--silver-dim)" }}>not testable</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function sectionLabel(text) {
  return (
    <div style={{
      fontFamily: "monospace", fontSize: "0.55rem", textTransform: "uppercase",
      letterSpacing: "0.2em", color: "var(--brass)", marginBottom: "0.6rem",
    }}>{text}</div>
  );
}

function ReportDetail({ report, onBack, onDeleted }) {
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await deleteSavedReport(report.id);
      onDeleted?.();
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      <div className="no-print" style={{ display: "flex", gap: "0.75rem", marginBottom: "1.5rem", flexWrap: "wrap" }}>
        <button onClick={onBack} className="btn btn-ghost btn-sm">← back to saved</button>
        <button onClick={() => window.print()} className="btn btn-primary btn-sm">
          Print / Save as PDF
        </button>
        <button onClick={handleDelete} disabled={deleting} className="btn btn-danger btn-sm">
          {deleting ? "deleting…" : "delete"}
        </button>
      </div>

      <div className="printable-report" style={{ backgroundColor: "rgba(199,202,209,0.03)", padding: "1.75rem 2rem", borderRadius: "8px", border: "1px solid rgba(199,202,209,0.14)" }}>
        <div style={{
          fontFamily: "monospace", fontSize: "0.58rem", textTransform: "uppercase",
          letterSpacing: "0.24em", color: "var(--brass)", marginBottom: "0.4rem",
        }}>
          AgentBio — Saved Hypothesis Report
        </div>
        <h2 style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--paper)", margin: "0 0 1.5rem", lineHeight: 1.35 }}>
          {report.hypothesis_text || report.hypothesis_id}
        </h2>

        {sectionLabel("Audit numbers — from the registry at save time")}
        <AuditTables facts={report.facts} />

        {sectionLabel("Narrative — Claude Opus 4.8")}
        <div className="report-markdown" style={{ fontSize: "0.8rem", color: "var(--paper)", lineHeight: 1.6 }}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.report_markdown}</ReactMarkdown>
        </div>

        <div style={{ marginTop: "1rem", fontSize: "0.55rem", fontFamily: "monospace", color: "var(--silver-dim)" }}>
          {report.generated_at ? `generated ${new Date(report.generated_at).toLocaleString()} · ` : ""}
          saved {new Date(report.saved_at).toLocaleString()}
        </div>
      </div>
    </div>
  );
}

export default function SavedReportsTab() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);

  const fetchReports = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listSavedReports();
      setReports(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReports(); }, [fetchReports]);

  const openReport = async (id) => {
    try {
      const full = await getSavedReport(id);
      setSelected(full);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "2.5rem 1.5rem" }}>
      <header className="no-print" style={{ marginBottom: "2rem" }}>
        <div className="eyebrow" style={{ marginBottom: "0.35rem" }}>Hypothesis archive</div>
        <h2 style={{ fontSize: "2rem", fontWeight: 700, color: "var(--paper)", margin: 0, lineHeight: 1.1 }}>
          Saved Reports
        </h2>
        <p style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--silver)", lineHeight: 1.6, maxWidth: "52rem" }}>
          Frozen snapshots of full hypothesis write-ups you chose to keep. Each one preserves the exact
          audit numbers and narrative at save time, so it never changes even as cumulative FDR shifts.
          Open one to read it or print it to PDF.
        </p>
      </header>

      {error && (
        <div className="no-print" style={{
          padding: "0.75rem 1rem", borderRadius: "5px",
          backgroundColor: "rgba(155,74,63,0.12)", color: "var(--oxide)",
          fontSize: "0.78rem", marginBottom: "1.25rem",
        }}>
          {error}
        </div>
      )}

      {selected ? (
        <ReportDetail
          report={selected}
          onBack={() => setSelected(null)}
          onDeleted={() => { setSelected(null); fetchReports(); }}
        />
      ) : loading ? (
        <p style={{ color: "var(--silver)", fontSize: "0.8rem" }}>Loading saved reports…</p>
      ) : reports.length === 0 ? (
        <p style={{ color: "var(--silver)", fontSize: "0.8rem", lineHeight: 1.6 }}>
          No saved reports yet. Open a full report in the <strong style={{ color: "var(--paper)" }}>Research</strong> tab
          and click <strong style={{ color: "var(--paper)" }}>Save report</strong> to keep it here.
        </p>
      ) : (
        <div style={{ display: "grid", gap: "0.75rem" }}>
          {reports.map((r) => (
            <button
              key={r.id}
              onClick={() => openReport(r.id)}
              className="report-card"
            >
              <div style={{ fontSize: "0.85rem", lineHeight: 1.4, marginBottom: "0.4rem" }}>
                {r.hypothesis_text || r.hypothesis_id}
              </div>
              <div style={{ fontSize: "0.6rem", fontFamily: "monospace", color: "var(--silver-dim)" }}>
                saved {new Date(r.saved_at).toLocaleString()}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
