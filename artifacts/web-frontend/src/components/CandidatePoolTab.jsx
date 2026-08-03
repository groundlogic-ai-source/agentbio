import { useEffect, useMemo, useState } from "react";
import { getCandidateEvidence, getCandidatePool, listRuns } from "../api.js";

const labelStyle = {
  display: "block", fontFamily: "monospace", fontSize: "0.61rem",
  textTransform: "uppercase", letterSpacing: "0.11em", color: "var(--ink-dim)",
  marginBottom: "0.3rem",
};

function Badge({ tone = "neutral", children }) {
  const tones = {
    neutral: ["var(--surface-raised)", "var(--ink-muted)", "var(--border)"],
    safe: ["var(--success-glow)", "var(--success)", "rgba(61,122,61,.3)"],
    warning: ["rgba(218,165,32,.1)", "#8a6c11", "rgba(218,165,32,.3)"],
    danger: ["var(--oxide-glow)", "var(--oxide)", "var(--oxide-border)"],
    info: ["var(--steel-glow)", "var(--steel-deep)", "var(--steel-border)"],
  };
  const [bg, color, border] = tones[tone];
  return <span style={{
    display: "inline-block", padding: "2px 6px", borderRadius: 999,
    background: bg, color, border: `1px solid ${border}`, fontSize: "0.62rem",
    fontFamily: "monospace", whiteSpace: "nowrap",
  }}>{children}</span>;
}

function EvidenceDrawer({ disease, candidate, onClose }) {
  const [state, setState] = useState({ loading: true, error: null, evidence: [] });
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, evidence: [] });
    getCandidateEvidence({ disease_name: disease, drug_name: candidate.drug_name })
      .then((data) => !cancelled && setState({ loading: false, error: null, evidence: data.evidence || [] }))
      .catch((error) => !cancelled && setState({ loading: false, error: error.message, evidence: [] }));
    return () => { cancelled = true; };
  }, [candidate.drug_name, disease]);

  return (
    <section className="candidate-evidence-panel">
      <div style={{ display: "flex", alignItems: "start", justifyContent: "space-between", gap: "1rem" }}>
        <div>
          <div className="eyebrow">Evidence ledger</div>
          <h3 style={{ margin: "0.25rem 0 0", color: "var(--ink)", fontSize: "1.15rem" }}>{candidate.drug_name}</h3>
          <p style={{ color: "var(--ink-muted)", margin: "0.35rem 0 0", fontSize: "0.76rem" }}>
            Source records are evidence inputs, not proof of clinical benefit.
          </p>
        </div>
        <button className="btn btn-xs btn-ghost" onClick={onClose}>Close</button>
      </div>
      {state.loading && <p className="pool-muted">Loading normalized source records…</p>}
      {state.error && <p className="pool-error">Could not load evidence: {state.error}</p>}
      {!state.loading && !state.error && state.evidence.length === 0 && (
        <p className="pool-muted">No normalized evidence records were persisted for this historical candidate.</p>
      )}
      <div className="evidence-card-grid">
        {state.evidence.map((record, i) => (
          <article className="evidence-card" key={`${record.identifier}-${i}`}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "start" }}>
              <Badge tone="info">{record.source}</Badge>
              <Badge tone={record.confidence === "qualified" ? "safe" : "warning"}>{record.confidence || "unrated"}</Badge>
            </div>
            <p className="evidence-claim">{record.claim || "Evidence record"}</p>
            {record.measurement_value != null && (
              <p className="evidence-measurement">
                {record.measurement_type || "measurement"}: {record.measurement_value} {record.measurement_unit || ""}
              </p>
            )}
            <dl className="evidence-meta">
              <dt>Identifier</dt><dd>{record.identifier || "—"}</dd>
              <dt>Action</dt><dd>{record.action || record.direction || "—"}</dd>
              <dt>Limitations</dt><dd>{record.limitations || "—"}</dd>
            </dl>
            {record.url && <a href={record.url} target="_blank" rel="noreferrer">Open source record</a>}
          </article>
        ))}
      </div>
    </section>
  );
}

export default function CandidatePoolTab() {
  const [runs, setRuns] = useState([]);
  const [disease, setDisease] = useState("");
  const [filters, setFilters] = useState({ query: "", safety: "", evidence: "", xlogp: "", sort: "rank", order: "asc" });
  const [pool, setPool] = useState(null);
  const [state, setState] = useState({ loading: false, error: null });
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    listRuns().then((data) => {
      const available = (data || []).filter((run) => ["awaiting_review", "completed"].includes(run.status));
      setRuns(available);
      if (available[0]?.disease_name) setDisease(available[0].disease_name);
    }).catch((error) => setState((s) => ({ ...s, error: error.message })));
  }, []);

  const selectedRun = useMemo(
    () => runs.find((run) => run.disease_name === disease),
    [runs, disease],
  );

  const load = async (page = 1) => {
    if (!disease) return;
    setState({ loading: true, error: null });
    setSelected(null);
    try {
      const result = await getCandidatePool({ disease_name: disease, job_id: selectedRun?.job_id, ...filters, page, page_size: 20 });
      setPool(result);
    } catch (error) {
      setState({ loading: false, error: error.message });
      return;
    }
    setState({ loading: false, error: null });
  };

  useEffect(() => { if (disease) load(1); }, [disease]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateFilter = (key, value) => setFilters((current) => ({ ...current, [key]: value }));
  const totalPages = Math.max(1, Math.ceil((pool?.total || 0) / (pool?.page_size || 20)));

  return (
    <div className="pool-page">
      <header className="pool-header">
        <div>
          <div className="eyebrow">Reviewed candidate pool</div>
          <h2>Candidate evidence, safety, and coverage</h2>
          <p>
            Browse persisted reviewed candidates from completed case files. Scores are historical run outputs;
            XLogP is a caution flag only and does not change rank.
          </p>
        </div>
      </header>

      {runs.length === 0 ? (
        <div className="pool-empty">No completed case with a persisted candidate pool is available yet. Complete a case first.</div>
      ) : (
        <>
          <section className="pool-controls">
            <label><span style={labelStyle}>Case</span>
              <select value={disease} onChange={(e) => setDisease(e.target.value)}>
                {runs.map((run) => <option key={run.job_id} value={run.disease_name}>{run.disease_name}</option>)}
              </select>
            </label>
            <label><span style={labelStyle}>Search</span>
              <input value={filters.query} onChange={(e) => updateFilter("query", e.target.value)} placeholder="Drug, ChEMBL ID, or target" />
            </label>
            <label><span style={labelStyle}>Safety</span>
              <select value={filters.safety} onChange={(e) => updateFilter("safety", e.target.value)}>
                <option value="">All</option><option value="clear">No flag</option><option value="advisory">Black-box advisory</option><option value="capped">Withdrawal cap</option>
              </select>
            </label>
            <label><span style={labelStyle}>Evidence coverage</span>
              <select value={filters.evidence} onChange={(e) => updateFilter("evidence", e.target.value)}>
                <option value="">All</option><option value="complete">Complete</option><option value="partial">Partial</option>
              </select>
            </label>
            <label><span style={labelStyle}>XLogP</span>
              <select value={filters.xlogp} onChange={(e) => updateFilter("xlogp", e.target.value)}>
                <option value="">All</option><option value="flagged">Caution ≥5</option><option value="unresolved">PubChem unresolved</option>
              </select>
            </label>
            <label><span style={labelStyle}>Sort</span>
              <select value={filters.sort} onChange={(e) => updateFilter("sort", e.target.value)}>
                <option value="rank">Rank</option><option value="score">Score</option><option value="coverage">Coverage</option><option value="drug">Drug</option><option value="xlogp">XLogP</option>
              </select>
            </label>
            <button className="btn btn-primary btn-sm pool-apply" onClick={() => load(1)} disabled={state.loading}>
              {state.loading ? "Loading…" : "Apply filters"}
            </button>
          </section>
          {state.error && <div className="pool-error">Could not load candidates: {state.error}</div>}
          {pool?.status === "no_candidates" && <div className="pool-empty">This case predates persisted candidate pools. Re-run it for a complete auditable list.</div>}
          {pool?.status === "ok" && (
            <>
              <div className="pool-summary">
                <span>{pool.total} candidate{pool.total === 1 ? "" : "s"} match the current filters</span>
                <span>Page {pool.page} of {totalPages}</span>
              </div>
              <div className="candidate-table-wrap">
                <table className="candidate-table">
                  <thead><tr><th>Rank</th><th>Candidate</th><th>Target</th><th>Score</th><th>Evidence</th><th>Safety</th><th>XLogP</th><th>Provenance</th><th /></tr></thead>
                  <tbody>
                    {pool.candidates.map((candidate) => (
                      <tr key={`${candidate.rank}-${candidate.drug_name}`}>
                        <td className="pool-mono">#{candidate.rank}</td>
                        <td><strong>{candidate.drug_name}</strong><small>{candidate.molecule_chembl_id || "No ChEMBL ID"}</small></td>
                        <td className="pool-mono">{candidate.target_symbol || "—"}</td>
                        <td className="pool-mono">{Number(candidate.composite_score || 0).toFixed(3)}</td>
                        <td><Badge tone={Number(candidate.evidence_weight_coverage) >= .999 ? "safe" : "warning"}>{candidate.evidence_weight_coverage == null ? "not observed" : `${Math.round(candidate.evidence_weight_coverage * 100)}% covered`}</Badge></td>
                        <td>{candidate.safety_cap_applied ? <Badge tone="danger">withdrawal cap</Badge> : candidate.black_box_advisory ? <Badge tone="warning">black-box advisory</Badge> : candidate.mechanism_cap_applied ? <Badge tone="danger">mechanism cap</Badge> : <Badge tone="safe">no cap</Badge>}</td>
                        <td>{candidate.xlogp_status === "flagged" ? <Badge tone="warning">caution {candidate.pubchem_xlogp}</Badge> : candidate.xlogp_status === "unresolved" ? <Badge tone="neutral">unresolved</Badge> : <Badge tone="neutral">{candidate.pubchem_xlogp ?? "—"}</Badge>}</td>
                        <td>{(candidate.source_types || []).slice(0, 3).map((source) => <Badge key={source} tone="info">{source}</Badge>)}</td>
                        <td><button className="btn btn-xs btn-ghost-brass" onClick={() => setSelected(candidate)}>Evidence</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!pool.candidates.length && <div className="pool-empty">No candidate matches these filters.</div>}
              <div className="pool-pagination">
                <button className="btn btn-xs btn-ghost" disabled={pool.page <= 1 || state.loading} onClick={() => load(pool.page - 1)}>Previous</button>
                <button className="btn btn-xs btn-ghost" disabled={pool.page >= totalPages || state.loading} onClick={() => load(pool.page + 1)}>Next</button>
              </div>
              <p className="pool-disclosure">XLogP ≥5 is disclosed as a lipophilicity caution. If PubChem did not resolve a compound, the list says “unresolved” rather than silently treating it as low lipophilicity. No XLogP score adjustment is applied.</p>
            </>
          )}
        </>
      )}
      {selected && <EvidenceDrawer disease={disease} candidate={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}