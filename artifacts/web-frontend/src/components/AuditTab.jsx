import React, { useState } from "react";
import { auditDrug } from "../api";
import TriagePanel from "./TriagePanel";
import DossierPanel from "./DossierPanel";
import DomainFindings from "./DomainFindings";
import AuditContextFindings from "./AuditContextFindings";
import ModalityModeToggle from "./ModalityModeToggle";
import InlineCaseRunner from "./InlineCaseRunner";
import { useModalityMode } from "../modalityMode";

// Modality finding card, hidden when the user disengages the mode. Wrapper
// keeps the hook out of AuditTab's own state list.
function ModalityFindings({ findings }) {
  const [engaged] = useModalityMode();
  if (!engaged) return null;
  return <DomainFindings findings={findings} />;
}

// ── Tiny shared primitives ────────────────────────────────────────────────────

function Pill({ color = "neutral", children }) {
  const map = {
    neutral: { bg: "var(--surface-raised)", text: "var(--ink-muted)", border: "var(--border)" },
    green:   { bg: "var(--success-glow)", text: "var(--success)", border: "rgba(61, 122, 61, 0.3)" },
    red:     { bg: "var(--oxide-glow)", text: "var(--oxide)", border: "var(--oxide-border)" },
    amber:   { bg: "rgba(218, 165, 32, 0.08)", text: "#9b7e1f", border: "rgba(218, 165, 32, 0.3)" },
    blue:    { bg: "var(--steel-glow)", text: "var(--steel-deep)", border: "var(--steel-border)" },
  };
  const style = map[color];
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium"
      style={{
        backgroundColor: style.bg,
        color: style.text,
        border: `1px solid ${style.border}`,
      }}
    >
      {children}
    </span>
  );
}

function Banner({ kind = "info", children }) {
  const map = {
    info:  { bg: "var(--steel-glow)", border: "var(--steel-border)", text: "var(--steel-deep)", marker: "◆" },
    warn:  { bg: "rgba(218, 165, 32, 0.08)", border: "rgba(218, 165, 32, 0.3)", text: "#9b7e1f", marker: "▲" },
    cap:   { bg: "var(--oxide-glow)", border: "var(--oxide-border)", text: "var(--oxide)", marker: "■" },
    note:  { bg: "var(--surface-raised)", border: "var(--border)", text: "var(--ink-muted)", marker: "◇" },
  };
  const style = map[kind];
  return (
    <div
      className="rounded-lg border px-4 py-3 text-sm flex gap-3"
      style={{
        backgroundColor: style.bg,
        borderColor: style.border,
        color: style.text,
      }}
    >
      <span className="shrink-0 text-base font-mono" style={{ color: style.text }}>{style.marker}</span>
      <span>{children}</span>
    </div>
  );
}

function ScoreBar({ value = 0, strong }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.70 ? "var(--success)" :
                value >= 0.50 ? "#daa520" : "var(--border-strong)";
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full" style={{ backgroundColor: "var(--surface-raised)" }}>
        <div
          className="h-2 rounded-full transition-all"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs tabular-nums font-semibold w-10 text-right" style={{ color: "var(--ink-base)" }}>
        {value.toFixed(3)}
      </span>
      {strong && <Pill color="green">Strong match</Pill>}
    </div>
  );
}

// ── Score-breakdown table shared with the dossier ─────────────────────────────

function ScoreRow({ label, value, note }) {
  return (
    <tr style={{ borderTop: "1px solid var(--border-light)" }}>
      <td className="py-2 pr-4 text-sm" style={{ color: "var(--ink-muted)" }}>{label}</td>
      <td className="py-2 pr-4 text-sm font-mono" style={{ color: "var(--ink-base)" }}>{value ?? "—"}</td>
      {note && <td className="py-2 text-xs" style={{ color: "var(--ink-dim)" }}>{note}</td>}
    </tr>
  );
}

function CandidateCard({ cand, rank, total, capReason }) {
  if (!cand) return null;
  return (
    <div
      className="rounded-xl border overflow-hidden"
      style={{
        borderColor: "var(--border)",
        backgroundColor: "var(--surface)",
        boxShadow: "var(--shadow-soft)",
      }}
    >
      {/* Header */}
      <div
        className="px-5 py-4 border-b flex items-start justify-between gap-4"
        style={{ borderColor: "var(--border-light)" }}
      >
        <div>
          <p
            className="text-xs font-medium uppercase tracking-wide mb-0.5"
            style={{ color: "var(--ink-dim)" }}
          >
            Rank {rank} of {total}
          </p>
          <h3 className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
            {cand.drug_name}
          </h3>
          {cand.molecule_chembl_id && (
            <p className="text-xs mt-0.5" style={{ color: "var(--ink-dim)" }}>
              {cand.molecule_chembl_id}
            </p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-xs mb-1" style={{ color: "var(--ink-dim)" }}>Composite score</p>
          <ScoreBar value={cand.composite_score ?? 0} strong={cand.strong_match} />
        </div>
      </div>

      {/* Cap disclosure */}
      {capReason && (
        <div className="px-5 py-3 border-b" style={{ borderColor: "var(--oxide-border)" }}>
          <Banner kind="cap">
            Score capped: {capReason}
          </Banner>
        </div>
      )}
      {cand.black_box_advisory && (
        <div className="px-5 py-3 border-b" style={{ borderColor: "rgba(218, 165, 32, 0.3)" }}>
          <Banner kind="warn">
            Black-box advisory: This drug carries an FDA black-box (boxed) warning. This is a
            disclosure only and does not affect the score.
          </Banner>
        </div>
      )}

      {/* Score breakdown */}
      <div className="px-5 py-4">
        <h4
          className="text-xs font-semibold uppercase tracking-wide mb-3"
          style={{ color: "var(--ink-muted)" }}
        >
          Score breakdown
        </h4>
        <table className="w-full">
          <tbody>
            <ScoreRow
              label="Target"
              value={cand.target_symbol}
              note={cand.target_name}
            />
            <ScoreRow
              label="pChEMBL affinity"
              value={cand.pchembl_value != null ? cand.pchembl_value.toFixed(2) : null}
            />
            <ScoreRow
              label="OT association score"
              value={cand.ot_association_score != null ? cand.ot_association_score.toFixed(3) : null}
            />
            <ScoreRow
              label="Tanimoto similarity"
              value={cand.tanimoto_score != null ? cand.tanimoto_score.toFixed(3) : null}
            />
            <ScoreRow
              label="Mechanism direction"
              value={cand.mechanism_direction?.verdict ?? "—"}
            />
            <ScoreRow
              label="Strong match threshold"
              value="≥ 0.700"
            />
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Result renderers ─────────────────────────────────────────────────────────

function FoundResult({ data }) {
  return (
    <div className="space-y-5">
      <Banner kind="info">
        {data.narration}
      </Banner>

      <CandidateCard
        cand={data.candidate}
        rank={data.rank}
        total={data.total_candidates}
        capReason={data.cap_reason}
      />

      {data.top_candidate?.drug_name !== data.candidate?.drug_name && (
        <div
          className="rounded-xl border px-5 py-4"
          style={{
            borderColor: "var(--border)",
            backgroundColor: "var(--surface-raised)",
          }}
        >
          <p
            className="text-xs font-semibold uppercase tracking-wide mb-2"
            style={{ color: "var(--ink-muted)" }}
          >
            AgentBio's top-ranked candidate
          </p>
          <div className="flex items-center justify-between">
            <p className="font-medium" style={{ color: "var(--ink-base)" }}>
              {data.top_candidate?.drug_name}
            </p>
            <ScoreBar value={data.top_candidate?.composite_score ?? 0} strong={data.top_candidate?.strong_match} />
          </div>
        </div>
      )}

      <Banner kind="note">{data.disclosure}</Banner>
    </div>
  );
}

function AbsentResult({ data }) {
  const agentTarget = data.agentbio_selected_target;
  const drugTargets = data.drug_mechanism_targets ?? [];
  const stableTargets = data.stable_mechanism_identities ?? [];
  const targetOverlap = data.pool_target_overlap ?? [];
  const componentOverlap = data.pool_component_target_overlap ?? [];
  const targetLadder = data.target_coverage_ladder ?? [];
  const suppliedOnly = data.audit_scope_status === "auditable_only_because_supplied";
  const sourceFailure = data.audit_scope_status === "source_failure";
  const missReasonLabel = {
    BIOLOGIC_STRUCTURAL: "Biologic lacks a qualifying structural-assay pool entry",
    ASSAY_POOL_GAP: "Target was covered, but the drug was absent from the assay pool",
    TARGET_NOT_SELECTED: "Drug mechanism target was not represented in the persisted pool",
    NO_MECHANISM_DATA: "No direct human-protein mechanism identity was available",
  }[data.deterministic_miss_reason] || data.deterministic_miss_reason;

  return (
    <div className="space-y-5">
      <Banner kind="warn">
        {data.narration}
      </Banner>

      <Banner kind={sourceFailure ? "warn" : "note"}>
        <strong>
          {sourceFailure
            ? "Mechanism source unavailable"
            : suppliedOnly
              ? "Auditable only because you supplied this drug"
              : "Absent from the discovery pool"}
        </strong>
        <span className="block mt-1">
          {sourceFailure
            ? "AgentBio could not verify stable mechanism identity, so it is not treating missing evidence as biological absence."
            : "This evidence review does not insert the drug into discovery, assign a rank or score, or count it as a rediscovery hit."}
        </span>
      </Banner>

      <div
        className="rounded-xl border overflow-hidden"
        style={{
          borderColor: "var(--border)",
          backgroundColor: "var(--surface)",
          boxShadow: "var(--shadow-soft)",
        }}
      >
        <div className="px-5 py-4 border-b" style={{ borderColor: "var(--border-light)" }}>
          <p className="text-sm font-semibold" style={{ color: "var(--ink)" }}>Stable target-identity audit</p>
          <p className="text-xs mt-0.5" style={{ color: "var(--ink-dim)" }}>
            {data.total_candidates} candidates evaluated by AgentBio for this disease
          </p>
        </div>
        <div style={{ borderTop: "1px solid var(--border-light)" }}>
          <div className="px-5 py-4 flex gap-4" style={{ borderBottom: "1px solid var(--border-light)" }}>
            <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
              Legacy top target
            </div>
            <div>
              <span className="font-mono text-sm" style={{ color: "var(--ink-base)" }}>
                {agentTarget ?? "unknown"}
              </span>
            </div>
          </div>
          <div className="px-5 py-4 flex gap-4">
            <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
              Drug's ChEMBL mechanisms
            </div>
            <div className="space-y-1">
              {drugTargets.length > 0 ? (
                drugTargets.map((t, i) => (
                  <p key={i} className="text-sm" style={{ color: "var(--ink-base)" }}>{t}</p>
                ))
              ) : (
                <p className="text-sm italic" style={{ color: "var(--ink-dim)" }}>
                  {sourceFailure
                    ? "Mechanism evidence unavailable while the ChEMBL source is degraded"
                    : "No mechanism records found in ChEMBL"}
                </p>
              )}
            </div>
          </div>
          <div className="px-5 py-4 flex gap-4" style={{ borderTop: "1px solid var(--border-light)" }}>
            <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
              Stable identities
            </div>
            <div className="space-y-2">
              {stableTargets.length > 0 ? stableTargets.map((target, i) => (
                <div key={`${target.target_chembl_id || "target"}-${i}`}>
                  <p className="text-sm font-medium" style={{ color: "var(--ink-base)" }}>
                    {target.target_name || target.target_chembl_id || "Unmapped mechanism target"}
                  </p>
                  <p className="text-xs font-mono" style={{ color: "var(--ink-dim)" }}>
                    {[
                      ...(target.gene_symbols || []),
                      ...(target.uniprot_ids || []),
                      target.target_chembl_id,
                    ].filter(Boolean).join(" · ") || "No stable protein identifier"}
                    {target.organism ? ` · ${target.organism}` : ""}
                    {target.target_type ? ` · ${target.target_type}` : ""}
                  </p>
                </div>
              )) : (
                <p className="text-sm italic" style={{ color: "var(--ink-dim)" }}>
                  No stable mechanism identity available
                </p>
              )}
            </div>
          </div>
          <div className="px-5 py-4 flex gap-4" style={{ borderTop: "1px solid var(--border-light)" }}>
            <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
              Pool overlap
            </div>
            <div>
              {targetOverlap.length > 0 ? (
                targetOverlap.map((target, i) => (
                  <p key={i} className="text-sm" style={{ color: "var(--ink-base)" }}>
                    Direct human-protein match: <span className="font-mono">{target.target_symbol || target.uniprot_id}</span>
                    {" "}at candidate rank {target.first_candidate_rank}
                  </p>
                ))
              ) : (
                <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
                  No direct human single-protein identity overlap
                </p>
              )}
              {componentOverlap.length > 0 && (
                <p className="text-xs mt-1" style={{ color: "#9b7e1f" }}>
                  A protein-complex component overlaps the pool, but is not counted as an exact target-entity match.
                </p>
              )}
            </div>
          </div>
          <div className="px-5 py-4 flex gap-4" style={{ borderTop: "1px solid var(--border-light)" }}>
            <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
              Deterministic miss reason
            </div>
            <div>
              <p className="text-sm" style={{ color: "var(--ink-base)" }}>
                {missReasonLabel || (sourceFailure ? "Unavailable while source is degraded" : "Unclassified")}
              </p>
              <p className="text-xs mt-1" style={{ color: "var(--ink-dim)" }}>
                Resolution: {data.resolved_identity_route || "unknown"} ·
                ChEMBL mechanism source: {data.mechanism_evidence_status || "unknown"}
              </p>
            </div>
          </div>
          {targetLadder.length > 0 && (
            <div className="px-5 py-4 flex gap-4" style={{ borderTop: "1px solid var(--border-light)" }}>
              <div className="w-44 shrink-0 text-xs pt-0.5" style={{ color: "var(--ink-muted)" }}>
                Persisted target ladder
              </div>
              <div className="flex flex-wrap gap-1.5">
                {targetLadder.map((target, i) => (
                  <span
                    key={`${target.target_symbol || target.uniprot_id}-${i}`}
                    className="audit-chip text-xs px-2 py-1 rounded-full"
                  >
                    #{target.first_candidate_rank} {target.target_symbol || target.uniprot_id || "unknown"}
                    {target.mechanism_identity_overlap ? " · direct match" : ""}
                    {target.component_identity_overlap ? " · component only" : ""}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {data.top_candidate && (
        <div
          className="rounded-xl border px-5 py-4"
          style={{
            borderColor: "var(--border)",
            backgroundColor: "var(--surface-raised)",
          }}
        >
          <p
            className="text-xs font-semibold uppercase tracking-wide mb-2"
            style={{ color: "var(--ink-muted)" }}
          >
            AgentBio's top-ranked candidate
          </p>
          <div className="flex items-center justify-between">
            <p className="font-medium" style={{ color: "var(--ink-base)" }}>
              {data.top_candidate?.drug_name}
            </p>
            <ScoreBar value={data.top_candidate?.composite_score ?? 0} strong={data.top_candidate?.strong_match} />
          </div>
        </div>
      )}

      <Banner kind="note">{data.disclosure}</Banner>
    </div>
  );
}

function NoCaseResult({ diseaseName, onRunHere, onNewCase }) {
  return (
    <div
      className="rounded-xl border px-6 py-6 space-y-4"
      style={{
        borderColor: "var(--border)",
        backgroundColor: "var(--surface)",
        boxShadow: "var(--shadow-soft)",
      }}
    >
      <div>
        <p className="font-semibold" style={{ color: "var(--ink)" }}>No case found for this disease</p>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          AgentBio hasn't built a candidate pool for
          <span className="font-medium" style={{ color: "var(--ink-base)" }}> {diseaseName}</span> yet,
          and an audit can only report against a pool the machine produced independently.
          Run it here and the audit repeats itself the moment the pool exists.
        </p>
      </div>

      <InlineCaseRunner disease={diseaseName} onReady={onRunHere} />

      {onNewCase && (
        <button onClick={onNewCase} className="btn btn-ghost btn-sm">
          Or open it in Case Files instead
        </button>
      )}
    </div>
  );
}

function NoCandidatesResult({ jobId, diseaseName, onRunHere, onNewCase }) {
  return (
    <div
      className="rounded-xl border px-6 py-6 space-y-3"
      style={{
        borderColor: "rgba(218, 165, 32, 0.3)",
        backgroundColor: "rgba(218, 165, 32, 0.08)",
      }}
    >
      <p className="font-medium" style={{ color: "#9b7e1f" }}>Candidates file unavailable</p>
      <p className="text-sm" style={{ color: "#9b7e1f" }}>
        The case <code className="text-xs">{jobId}</code> predates per-job candidate
        persistence, so there is no pool to audit against. A fresh run fixes it.
      </p>

      <InlineCaseRunner
        disease={diseaseName}
        onReady={onRunHere}
        verb="Re-run this case now"
      />

      {onNewCase && (
        <button onClick={onNewCase} className="btn btn-ghost btn-sm">
          Or open it in Case Files instead
        </button>
      )}
    </div>
  );
}

function UnresolvedResult({ data }) {
  return (
    <div className="space-y-5">
      <Banner kind="warn">{data.narration}</Banner>
      <div
        className="rounded-xl border px-5 py-4"
        style={{
          borderColor: "var(--border)",
          backgroundColor: "var(--surface)",
        }}
      >
        <p className="text-sm font-semibold" style={{ color: "var(--ink)" }}>Why this matters</p>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          AgentBio resolves drug names against ChEMBL before checking the pool.
          An unresolvable name means the audit could not run — it does{" "}
          <span className="font-medium" style={{ color: "var(--ink-base)" }}>not</span> mean the drug was
          evaluated and rejected.
        </p>
      </div>
    </div>
  );
}

// ── Single-drug audit pane ────────────────────────────────────────────────────

function SingleDrugAudit({ onNavigate }) {
  const [disease, setDisease] = useState("");
  const [drug, setDrug]       = useState("");
  const [claimedRoute, setClaimedRoute] = useState("");
  const [claimedDose, setClaimedDose] = useState("");
  const [claimedModality, setClaimedModality] = useState("");
  const [claimedContext, setClaimedContext] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState(null);
  const [error, setError]     = useState(null);

  async function runAudit() {
    if (!disease.trim() || !drug.trim()) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const data = await auditDrug(disease.trim(), drug.trim(), null, {
        route: claimedRoute.trim(),
        dose: claimedDose.trim(),
        modality: claimedModality.trim(),
        context: claimedContext.trim(),
      });
      setResult(data);
    } catch (err) {
      setError(err.message || "Unexpected error");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    return runAudit();
  }

  function handleNewCase() {
    // Navigate to the new-case flow (dashboard) pre-populated with the disease name
    if (onNavigate) onNavigate("dashboard", { prefill: disease });
  }

  const examples = [
    { disease: "Idiopathic pulmonary arterial hypertension", drug: "Sildenafil" },
    { disease: "Multiple myeloma", drug: "Thalidomide" },
  ];

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 space-y-8">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-semibold" style={{ color: "var(--ink)" }}>Candidate Audit</h2>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          Look up where a specific drug stands in AgentBio's reasoning for a disease —
          rank, score, cap disclosures, and a target comparison if absent.
        </p>
      </div>

      {/* Examples */}
      <div className="flex flex-wrap gap-2">
        <span className="text-xs self-center" style={{ color: "var(--ink-dim)" }}>Try:</span>
        {examples.map((ex, i) => (
          <button
            key={i}
            onClick={() => { setDisease(ex.disease); setDrug(ex.drug); setResult(null); setError(null); }}
            className="audit-chip text-xs px-3 py-1.5 rounded-full transition-colors"
          >
            {ex.drug} / {ex.disease}
          </button>
        ))}
      </div>

      {/* Form */}
      <form
        onSubmit={handleSubmit}
        className="rounded-xl border p-5 space-y-4"
        style={{
          borderColor: "var(--border)",
          backgroundColor: "var(--surface)",
          boxShadow: "var(--shadow-soft)",
        }}
      >
        <div>
          <label
            className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
            style={{ color: "var(--ink-muted)" }}
          >
            Disease name
          </label>
          <input
            value={disease}
            onChange={e => setDisease(e.target.value)}
            placeholder="e.g. Idiopathic pulmonary arterial hypertension"
            className="audit-input w-full border rounded-lg px-3 py-2 text-sm focus:outline-none"
          />
        </div>
        <div>
          <label
            className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
            style={{ color: "var(--ink-muted)" }}
          >
            Drug name
          </label>
          <input
            value={drug}
            onChange={e => setDrug(e.target.value)}
            placeholder="e.g. Sildenafil"
            className="audit-input w-full border rounded-lg px-3 py-2 text-sm focus:outline-none"
          />
        </div>
        <details
          className="rounded-lg border px-3 py-2"
          style={{ borderColor: "var(--border)" }}
        >
          <summary
            className="cursor-pointer text-sm font-medium"
            style={{ color: "var(--ink)" }}
          >
            Optional claim context for N2/N4
          </summary>
          <div className="grid sm:grid-cols-2 gap-3 mt-3">
            <label className="text-xs" style={{ color: "var(--ink-muted)" }}>
              Claimed route
              <input
                value={claimedRoute}
                onChange={e => setClaimedRoute(e.target.value)}
                placeholder="e.g. oral"
                className="audit-input mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              />
            </label>
            <label className="text-xs" style={{ color: "var(--ink-muted)" }}>
              Claimed dose
              <input
                value={claimedDose}
                onChange={e => setClaimedDose(e.target.value)}
                placeholder="e.g. 10 mg daily"
                className="audit-input mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              />
            </label>
            <label className="text-xs" style={{ color: "var(--ink-muted)" }}>
              Claimed modality
              <input
                value={claimedModality}
                onChange={e => setClaimedModality(e.target.value)}
                placeholder="e.g. small molecule"
                className="audit-input mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              />
            </label>
            <label className="text-xs" style={{ color: "var(--ink-muted)" }}>
              Exposure / experimental context
              <input
                value={claimedContext}
                onChange={e => setClaimedContext(e.target.value)}
                placeholder="e.g. systemic plasma exposure"
                className="audit-input mt-1 w-full border rounded-lg px-3 py-2 text-sm"
              />
            </label>
          </div>
          <p className="text-xs mt-2" style={{ color: "var(--ink-dim)" }}>
            Optional. These fields enable route, dose, modality, and exposure-context
            review; they never change the AgentBio rank or verdict.
          </p>
        </details>
        <button
          type="submit"
          disabled={loading || !disease.trim() || !drug.trim()}
          className="audit-submit w-full py-2.5 text-sm font-semibold rounded-lg transition-colors"
        >
          {loading ? "Looking up…" : "Run Audit"}
        </button>
      </form>

      {/* Error */}
      {error && (
        <Banner kind="cap">Error: {error}</Banner>
      )}

      {/* Results */}
      {result && (
        <div className="space-y-4">
          <DomainFindings findings={result.domain_findings} />
          <ModalityFindings findings={result.modality_findings} />
          <AuditContextFindings context={result.audit_context} />
          <ModalityModeToggle />
          {result.status === "found" && <FoundResult data={result} />}
          {result.status === "absent" && <AbsentResult data={result} />}
          {result.status === "unresolved" && <UnresolvedResult data={result} />}
          {result.status === "no_case" && (
            <NoCaseResult
              diseaseName={disease}
              onRunHere={runAudit}
              onNewCase={handleNewCase}
            />
          )}
          {result.status === "no_candidates" && (
            <NoCandidatesResult
              jobId={result.job_id}
              diseaseName={disease}
              onRunHere={runAudit}
              onNewCase={handleNewCase}
            />
          )}
        </div>
      )}
    </div>
  );
}

// ── Unified Audit mode ────────────────────────────────────────────────────────

const AUDIT_MODES = [
  { id: "triage",  label: "Triage a list" },
  { id: "single",  label: "Single drug" },
  { id: "dossier", label: "Dossiers" },
];

export default function AuditTab({ onNavigate }) {
  const [mode, setMode] = useState("triage");

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      <div>
        <h2 className="text-2xl font-semibold" style={{ color: "var(--ink)" }}>Audit</h2>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          Independent verification, not discovery: triage your own candidate list,
          audit a single drug, or re-verify saved dossiers claim by claim.
        </p>
      </div>

      <div className="flex gap-2 border-b pb-0" style={{ borderColor: "var(--border)" }}>
        {AUDIT_MODES.map((m) => (
          <button
            key={m.id}
            onClick={() => setMode(m.id)}
            className="text-sm px-3 py-2 font-medium transition-colors"
            style={{
              color: mode === m.id ? "var(--ink)" : "var(--ink-muted)",
              borderBottom: mode === m.id ? "2px solid var(--accent, #3a6ea5)" : "2px solid transparent",
              marginBottom: "-1px",
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {mode === "triage" && <TriagePanel onNavigate={onNavigate} />}
      {mode === "single" && <SingleDrugAudit onNavigate={onNavigate} />}
      {mode === "dossier" && <DossierPanel />}
    </div>
  );
}
