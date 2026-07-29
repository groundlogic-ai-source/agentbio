import React, { useState } from "react";
import { auditDrug } from "../api";

// ── Tiny shared primitives ────────────────────────────────────────────────────

function Pill({ color = "slate", children }) {
  const map = {
    slate:  "bg-slate-100 text-slate-700",
    green:  "bg-emerald-50 text-emerald-700 border border-emerald-200",
    red:    "bg-red-50 text-red-700 border border-red-200",
    amber:  "bg-amber-50 text-amber-700 border border-amber-200",
    blue:   "bg-blue-50 text-blue-700 border border-blue-200",
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${map[color]}`}>
      {children}
    </span>
  );
}

function Banner({ kind = "info", children }) {
  const map = {
    info:  "bg-blue-50 border-blue-200 text-blue-800",
    warn:  "bg-amber-50 border-amber-200 text-amber-800",
    cap:   "bg-red-50 border-red-200 text-red-800",
    note:  "bg-slate-50 border-slate-200 text-slate-600",
  };
  const icons = { info: "ℹ️", warn: "⚠️", cap: "🔒", note: "📋" };
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm flex gap-3 ${map[kind]}`}>
      <span className="shrink-0 text-base">{icons[kind]}</span>
      <span>{children}</span>
    </div>
  );
}

function ScoreBar({ value = 0, strong }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.70 ? "bg-emerald-500" :
                value >= 0.50 ? "bg-amber-400" : "bg-slate-300";
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full bg-slate-100">
        <div className={`h-2 rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums font-semibold text-slate-700 w-10 text-right">
        {value.toFixed(3)}
      </span>
      {strong && <Pill color="green">Strong match</Pill>}
    </div>
  );
}

// ── Score-breakdown table shared with the dossier ─────────────────────────────

function ScoreRow({ label, value, note }) {
  return (
    <tr className="border-t border-slate-100">
      <td className="py-2 pr-4 text-slate-500 text-sm">{label}</td>
      <td className="py-2 pr-4 text-slate-800 text-sm font-mono">{value ?? "—"}</td>
      {note && <td className="py-2 text-xs text-slate-400">{note}</td>}
    </tr>
  );
}

function CandidateCard({ cand, rank, total, capReason }) {
  if (!cand) return null;
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-100 flex items-start justify-between gap-4">
        <div>
          <p className="text-xs text-slate-400 font-medium uppercase tracking-wide mb-0.5">
            Rank {rank} of {total}
          </p>
          <h3 className="text-lg font-semibold text-slate-900">
            {cand.drug_name}
          </h3>
          {cand.molecule_chembl_id && (
            <p className="text-xs text-slate-400 mt-0.5">{cand.molecule_chembl_id}</p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-xs text-slate-400 mb-1">Composite score</p>
          <ScoreBar value={cand.composite_score ?? 0} strong={cand.strong_match} />
        </div>
      </div>

      {/* Cap disclosure */}
      {capReason && (
        <div className="px-5 py-3 border-b border-red-100">
          <Banner kind="cap">
            Score capped: {capReason}
          </Banner>
        </div>
      )}
      {cand.black_box_advisory && (
        <div className="px-5 py-3 border-b border-amber-100">
          <Banner kind="warn">
            Black-box advisory: This drug carries an FDA black-box (boxed) warning. This is a
            disclosure only and does not affect the score.
          </Banner>
        </div>
      )}

      {/* Score breakdown */}
      <div className="px-5 py-4">
        <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">
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
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-4">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            AgentBio's top-ranked candidate
          </p>
          <div className="flex items-center justify-between">
            <p className="text-slate-800 font-medium">{data.top_candidate?.drug_name}</p>
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

  const overlap = agentTarget
    ? drugTargets.some(t => t.toUpperCase().includes((agentTarget || "").toUpperCase()))
    : false;

  return (
    <div className="space-y-5">
      <Banner kind="warn">
        {data.narration}
      </Banner>

      <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="px-5 py-4 border-b border-slate-100">
          <p className="text-sm font-semibold text-slate-700">Target comparison</p>
          <p className="text-xs text-slate-400 mt-0.5">
            {data.total_candidates} candidates evaluated by AgentBio for this disease
          </p>
        </div>
        <div className="divide-y divide-slate-100">
          <div className="px-5 py-4 flex gap-4">
            <div className="w-44 shrink-0 text-xs text-slate-500 pt-0.5">
              AgentBio selected target
            </div>
            <div>
              <span className="font-mono text-sm text-slate-800">
                {agentTarget ?? "unknown"}
              </span>
            </div>
          </div>
          <div className="px-5 py-4 flex gap-4">
            <div className="w-44 shrink-0 text-xs text-slate-500 pt-0.5">
              Drug's ChEMBL mechanisms
            </div>
            <div className="space-y-1">
              {drugTargets.length > 0 ? (
                drugTargets.map((t, i) => (
                  <p key={i} className="text-sm text-slate-700">{t}</p>
                ))
              ) : (
                <p className="text-sm text-slate-400 italic">
                  No mechanism records found in ChEMBL
                </p>
              )}
            </div>
          </div>
          {!overlap && drugTargets.length > 0 && agentTarget && (
            <div className="px-5 py-3 bg-amber-50">
              <p className="text-xs text-amber-700">
                The drug's recorded ChEMBL mechanism targets don't match AgentBio's
                selected target for this disease — a likely explanation for its absence
                from the pool.
              </p>
            </div>
          )}
        </div>
      </div>

      {data.top_candidate && (
        <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-4">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
            AgentBio's top-ranked candidate
          </p>
          <div className="flex items-center justify-between">
            <p className="text-slate-800 font-medium">{data.top_candidate?.drug_name}</p>
            <ScoreBar value={data.top_candidate?.composite_score ?? 0} strong={data.top_candidate?.strong_match} />
          </div>
        </div>
      )}

      <Banner kind="note">{data.disclosure}</Banner>
    </div>
  );
}

function NoCaseResult({ diseaseName, drugName, onNewCase }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm px-6 py-8 text-center space-y-4">
      <div className="text-4xl">🔬</div>
      <div>
        <p className="text-slate-800 font-semibold">No case found for this disease</p>
        <p className="text-sm text-slate-500 mt-1">
          AgentBio hasn't run a drug-repurposing analysis for
          <span className="font-medium text-slate-700"> {diseaseName}</span> yet.
          Submit a new case to generate a candidate pool, then re-run the audit.
        </p>
      </div>
      <button
        onClick={onNewCase}
        className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm font-medium rounded-lg transition-colors"
      >
        Submit new case
      </button>
    </div>
  );
}

function NoCandidatesResult({ jobId }) {
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-6 py-6 space-y-2">
      <p className="text-amber-800 font-medium">Candidates file unavailable</p>
      <p className="text-sm text-amber-700">
        The case <code className="text-xs">{jobId}</code> predates per-job candidate
        persistence. Re-run the disease to generate a fresh candidates file, then
        repeat the audit.
      </p>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function AuditTab({ onNavigate }) {
  const [disease, setDisease] = useState("");
  const [drug, setDrug]       = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult]   = useState(null);
  const [error, setError]     = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!disease.trim() || !drug.trim()) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const data = await auditDrug(disease.trim(), drug.trim());
      setResult(data);
    } catch (err) {
      setError(err.message || "Unexpected error");
    } finally {
      setLoading(false);
    }
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
        <h2 className="text-2xl font-semibold text-slate-900">Candidate Audit</h2>
        <p className="text-sm text-slate-500 mt-1">
          Look up where a specific drug stands in AgentBio's reasoning for a disease —
          rank, score, cap disclosures, and a target comparison if absent.
        </p>
      </div>

      {/* Examples */}
      <div className="flex flex-wrap gap-2">
        <span className="text-xs text-slate-400 self-center">Try:</span>
        {examples.map((ex, i) => (
          <button
            key={i}
            onClick={() => { setDisease(ex.disease); setDrug(ex.drug); setResult(null); setError(null); }}
            className="text-xs px-3 py-1.5 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-600 transition-colors"
          >
            {ex.drug} / {ex.disease}
          </button>
        ))}
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 space-y-4">
        <div>
          <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wide mb-1.5">
            Disease name
          </label>
          <input
            value={disease}
            onChange={e => setDisease(e.target.value)}
            placeholder="e.g. Idiopathic pulmonary arterial hypertension"
            className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 placeholder-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold text-slate-600 uppercase tracking-wide mb-1.5">
            Drug name
          </label>
          <input
            value={drug}
            onChange={e => setDrug(e.target.value)}
            placeholder="e.g. Sildenafil"
            className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm text-slate-800 placeholder-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-400 focus:border-transparent"
          />
        </div>
        <button
          type="submit"
          disabled={loading || !disease.trim() || !drug.trim()}
          className="w-full py-2.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white text-sm font-semibold rounded-lg transition-colors"
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
        <div>
          {result.status === "found" && <FoundResult data={result} />}
          {result.status === "absent" && <AbsentResult data={result} />}
          {result.status === "no_case" && (
            <NoCaseResult
              diseaseName={disease}
              drugName={drug}
              onNewCase={handleNewCase}
            />
          )}
          {result.status === "no_candidates" && (
            <NoCandidatesResult jobId={result.job_id} />
          )}
        </div>
      )}
    </div>
  );
}
