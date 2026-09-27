import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TherapeuticApplicabilityCaveat from "./TherapeuticApplicabilityCaveat.jsx";
import {
  getResearchBenchmarks,
  getBenchmarkStatus,
  getBenchmarkReport,
} from "../api.js";

function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${(Number(v) * 100).toFixed(digits)}%`;
}

function BenchmarkPanel() {
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [runState, setRunState] = useState({ loading: true, error: null, data: null });
  // Frozen-report viewer: null when closed, otherwise { status, id, title, markdown, error }.
  // reportReq guards the async fetch: closing the viewer or opening a second
  // report invalidates any in-flight request, so a late response can never
  // reopen the modal or overwrite a newer report with an older one.
  const [report, setReport] = useState(null);
  const reportReq = useRef(0);
  const openerRef = useRef(null);   // button that opened the dialog (focus restore)
  const closeBtnRef = useRef(null); // initial focus target inside the dialog
  const openReport = async (id, opener) => {
    openerRef.current = opener || null;
    const req = ++reportReq.current;
    setReport({ status: "loading", id });
    try {
      const data = await getBenchmarkReport(id);
      if (req === reportReq.current) setReport({ status: "ok", ...data });
    } catch (err) {
      if (req === reportReq.current) setReport({ status: "error", id, error: err.message });
    }
  };
  const closeReport = useCallback(() => {
    reportReq.current += 1; // invalidate any in-flight fetch
    setReport(null);
  }, []);
  const reportOpen = report !== null;
  useEffect(() => {
    if (!reportOpen) return undefined;
    closeBtnRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") {
        closeReport();
      } else if (e.key === "Tab") {
        // Minimal focus trap: keep Tab/Shift+Tab inside the dialog.
        const dialog = closeBtnRef.current?.closest("[role='dialog']");
        if (!dialog) return;
        const focusable = dialog.querySelectorAll(
          "button, a[href], input, select, textarea, [tabindex]:not([tabindex='-1'])"
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (openerRef.current && document.contains(openerRef.current)) {
        openerRef.current.focus();
      }
    };
  }, [reportOpen, closeReport]);
  useEffect(() => {
    getResearchBenchmarks()
      .then((data) => setState({ loading: false, error: null, data }))
      .catch((error) => setState({ loading: false, error: error.message, data: null }));
    getBenchmarkStatus()
      .then((data) => setRunState({ loading: false, error: null, data }))
      .catch((error) => setRunState({ loading: false, error: error.message, data: null }));
  }, []);
  if (state.loading) return <div className="benchmark-panel"><span className="pool-muted">Loading historical validation artifacts…</span></div>;
  if (state.error) return <div className="benchmark-panel pool-error">Could not load benchmark artifacts: {state.error}</div>;
  const data = state.data || {};
  const frozen = runState.data?.frozen_completion;
  return (
    <section className="benchmark-panel">
      <div className="eyebrow">Validation reporting</div>
      <h3>Historical benchmark artifacts</h3>
      <p className="benchmark-note">{data.pilot_note}</p>
      <TherapeuticApplicabilityCaveat />
      {runState.loading && (
        <div className="benchmark-run-status benchmark-run-status-loading">
          Verifying the frozen benchmark-v2 result…
        </div>
      )}
      {runState.error && (
        <div className="benchmark-run-status benchmark-run-status-warning">
          Frozen benchmark status is temporarily unavailable: {runState.error}
        </div>
      )}
      {frozen && (
        <div className={`benchmark-run-status ${frozen.complete ? "benchmark-run-status-complete" : "benchmark-run-status-warning"}`}>
          <div>
            <span className="benchmark-status-label">
              {frozen.complete ? "Benchmark v2 · frozen complete" : "Benchmark v2 · integrity review required"}
            </span>
            <strong>
              {frozen.screened_primary ?? "—"}/{frozen.selected_primary ?? "—"} selected cases passed screening ({fmtPct(frozen.funnel_feasibility_rate, 0)} funnel-feasible);{" "}
              {frozen.primary_rediscovered ?? "—"}/{frozen.primary_in_scope ?? "—"} in-scope screened cases were rediscovered ({fmtPct(frozen.primary_rediscovery_rate)}).
            </strong>
            <span>
              {frozen.primary_executed ?? "—"} screened primary · {frozen.development_executed ?? "—"} development · completed {frozen.completed_on}
            </span>
          </div>
          <div className="benchmark-status-lock">
            One pre-registered run<br />reruns disabled
          </div>
          <button
            type="button"
            className="btn btn-xs btn-ghost-brass"
            onClick={(e) => openReport("benchmark-v2", e.currentTarget)}
            title="Read the frozen pre-registration that v2 was committed to before any case ran"
          >
            Read full protocol
          </button>
        </div>
      )}
      <div className="benchmark-grid">
        {(data.benchmarks || []).map((bench) => (
          bench.kind === "audit_traps" ? (
            <article className="benchmark-card" key={bench.label}>
              <div className="benchmark-card-title">{bench.label}</div>
              <div className="benchmark-metrics">
                <span><b>{bench.traps_caught}/{bench.traps_total}</b> traps caught</span>
                <span><b>{bench.controls_false_flagged}/{bench.controls_total}</b> false flags</span>
                <span><b>{bench.precision != null ? bench.precision.toFixed(2) : "—"}</b> precision</span>
                <span><b>{bench.verdict}</b></span>
              </div>
              <p>{bench.limitations}</p>
              <details><summary>Trap outcomes (must be caught)</summary>
                <ul>{(bench.traps || []).map((t) => <li key={t.id}>{t.id} {t.class}: {t.caught ? "caught" : "MISSED"}</li>)}</ul>
              </details>
              <details><summary>Control outcomes (must not be flagged)</summary>
                <ul>{(bench.controls || []).map((c) => <li key={c.id}>{c.id} {c.class}: {c.clean ? "clean" : "FALSE-FLAGGED"}</li>)}</ul>
              </details>
              {bench.report_id && (
                <button
                  type="button"
                  className="btn btn-xs btn-ghost-brass"
                  style={{ marginTop: "0.5rem" }}
                  onClick={(e) => openReport(bench.report_id, e.currentTarget)}
                  title="Read the frozen full report for this artifact"
                >
                  Read full report
                </button>
              )}
            </article>
          ) : (
          <article className="benchmark-card" key={bench.label}>
            <div className="benchmark-card-title">{bench.label}</div>
            <div className="benchmark-metrics">
              <span><b>{bench.total_cases}</b> cases</span>
              <span><b>{bench.top10}</b> Top-10</span>
              <span><b>{bench.top25}</b> Top-25</span>
            </div>
            <p>{bench.limitations}</p>
            {Object.keys(bench.miss_reasons || {}).length > 0 && (
              <details><summary>Miss reasons</summary>
                <ul>{Object.entries(bench.miss_reasons).map(([reason, n]) => <li key={reason}>{reason}: {n}</li>)}</ul>
              </details>
            )}
            <details><summary>Fixture results</summary>
              <div className="benchmark-fixture-wrap"><table className="benchmark-fixtures"><thead><tr><th>Disease</th><th>Drug</th><th>Rank</th><th>Outcome</th></tr></thead>
                <tbody>{(bench.fixtures || []).map((row, i) => <tr key={i}><td>{row.disease || "—"}</td><td>{row.drug || "—"}</td><td>{row.rank ?? "—"}</td><td>{row.outcome}</td></tr>)}</tbody>
              </table></div>
            </details>
            {bench.report_id && (
              <button
                type="button"
                className="btn btn-xs btn-ghost-brass"
                style={{ marginTop: "0.5rem" }}
                onClick={(e) => openReport(bench.report_id, e.currentTarget)}
                title="Read the frozen full report for this artifact"
              >
                Read full report
              </button>
            )}
          </article>
          )
        ))}
      </div>
      {report && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 50, display: "flex",
            alignItems: "center", justifyContent: "center", padding: "2rem",
            backgroundColor: "rgba(20,20,24,0.45)",
          }}
          onClick={closeReport}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={report.title || "Benchmark report"}
            style={{
              backgroundColor: "var(--paper, #faf8f3)", width: "100%", maxWidth: "52rem",
              maxHeight: "82vh", overflowY: "auto", borderRadius: "8px",
              border: "1px solid rgba(184,151,90,0.35)", padding: "1.25rem 1.5rem",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "1rem", marginBottom: "0.75rem" }}>
              <strong style={{ fontSize: "0.9rem" }}>{report.title || "Benchmark report"}</strong>
              <button type="button" className="btn btn-xs btn-ghost" ref={closeBtnRef} onClick={closeReport}>Close</button>
            </div>
            {report.status === "loading" && <p className="pool-muted">Loading frozen report…</p>}
            {report.status === "error" && <p className="pool-error">Could not load report: {report.error}</p>}
            {report.status === "ok" && (
              <div style={{ fontSize: "0.78rem", lineHeight: 1.65 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{report.markdown}</ReactMarkdown>
              </div>
            )}
            <p className="benchmark-note" style={{ marginTop: "1rem" }}>
              Frozen artifact — served verbatim from the committed validation file; it never changes.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

const CITATION_TEXT =
  "Britto, E. (2026). AgentBio: an audited drug-repurposing research pipeline for rare and "
  + "neglected diseases (Version benchmark-freeze-v2) [Computer software]. MIT License.";

const CITATION_BIBTEX = `@software{britto2026agentbio,
  author  = {Britto, Evan},
  title   = {AgentBio: an audited drug-repurposing research pipeline for rare and neglected diseases},
  year    = {2026},
  version = {benchmark-freeze-v2},
  license = {MIT}
}`;

function CitePanel() {
  const [copied, setCopied] = useState(null); // "text" | "bibtex" | "error" | null

  const copy = async (kind, value) => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(value);
      ok = true;
    } catch {
      try {
        const ta = document.createElement("textarea");
        ta.value = value;
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch {
        ok = false;
      }
    }
    setCopied(ok ? kind : "error");
    setTimeout(() => setCopied(null), 2500);
  };

  return (
    <section className="benchmark-panel" style={{ marginBottom: "2rem" }}>
      <div className="eyebrow">Cite this work</div>
      <h3>How to cite AgentBio</h3>
      <p className="benchmark-note">
        If you reference this pipeline or its validation results, cite the software below; the results
        of record are documented in <code>validation/validation_campaign_dossier.md</code>. The benchmark
        results hash, row identities, and funnel are independently re-checkable via{" "}
        <code>python3 validation/verify_v2_provenance.py</code>; the missing deployment attestation and
        post-screen list are disclosed provenance limits. The repository also ships a{" "}
        <code>CITATION.cff</code>, so GitHub and reference managers (Zotero, Mendeley) pick this up
        automatically.
      </p>
      <article className="benchmark-card">
        <div className="benchmark-card-title">Suggested citation</div>
        <p style={{ fontStyle: "italic", lineHeight: 1.6 }}>{CITATION_TEXT}</p>
        <pre style={{
          fontFamily: "monospace", fontSize: "0.68rem", lineHeight: 1.55,
          color: "var(--ink-muted)", backgroundColor: "rgba(0,0,0,0.04)",
          padding: "0.75rem 1rem", borderRadius: "5px", overflowX: "auto",
          margin: "0.5rem 0 0.75rem", whiteSpace: "pre",
        }}>{CITATION_BIBTEX}</pre>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn btn-xs btn-ghost-brass" onClick={() => copy("text", CITATION_TEXT)}>
            {copied === "text" ? "✓ Copied" : "Copy citation"}
          </button>
          <button type="button" className="btn btn-xs btn-ghost" onClick={() => copy("bibtex", CITATION_BIBTEX)}>
            {copied === "bibtex" ? "✓ Copied" : "Copy BibTeX"}
          </button>
          {copied === "error" && (
            <span style={{ color: "var(--oxide)", fontSize: "0.72rem" }}>
              Copy failed — select the text above manually
            </span>
          )}
        </div>
      </article>
    </section>
  );
}

export default function ResearchTab() {
  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "2.5rem 1.5rem" }}>
      <header style={{ marginBottom: "2rem" }}>
        <div className="eyebrow" style={{ marginBottom: "0.35rem" }}>Validation evidence</div>
        <h2 style={{ fontSize: "2rem", fontWeight: 700, color: "var(--ink)", margin: 0, lineHeight: 1.1 }}>
          Research
        </h2>
        <p style={{ marginTop: "0.5rem", fontSize: "0.8rem", color: "var(--ink-muted)", lineHeight: 1.6, maxWidth: "52rem" }}>
          The frozen, retrospective benchmark studies AgentBio's validation claims are based on —
          read-only historical evidence, not a live or interactive system.
        </p>
      </header>

      <BenchmarkPanel />
      <CitePanel />
    </div>
  );
}
