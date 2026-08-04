import { useState } from "react";
import { triageCandidates } from "../api";
import DomainFindings from "./DomainFindings";

// ── Flag presentation ─────────────────────────────────────────────────────────
const FLAG_META = {
  SAFETY_CAP:          { label: "Safety cap",        tone: "danger"  },
  MECHANISM_CAP:       { label: "Direction cap",     tone: "danger"  },
  UNAPPROVED_CAP:      { label: "Unapproved cap",    tone: "warning" },
  BLACK_BOX_ADVISORY:  { label: "Black-box advisory",tone: "warning" },
  XLOGP_CAUTION:       { label: "XLogP ≥5 caution",  tone: "warning" },
  XLOGP_UNRESOLVED:    { label: "XLogP unresolved",  tone: "neutral" },
  EVIDENCE_PARTIAL:    { label: "Partial evidence",  tone: "info"    },
  ABSENT_FROM_POOL:    { label: "Absent from pool",  tone: "neutral" },
  UNRESOLVED_NAME:     { label: "Name unresolved",   tone: "neutral" },
  NO_CASE:             { label: "No case",           tone: "neutral" },
};

const TONE_STYLE = {
  danger:  { bg: "rgba(185, 65, 47, 0.14)",  fg: "#b9412f" },
  warning: { bg: "rgba(176, 125, 37, 0.16)", fg: "#8a6116" },
  info:    { bg: "rgba(58, 110, 165, 0.14)", fg: "#3a6ea5" },
  neutral: { bg: "rgba(110, 110, 110, 0.14)",fg: "var(--ink-muted)" },
};

function FlagBadge({ code }) {
  const meta = FLAG_META[code] || { label: code, tone: "neutral" };
  const t = TONE_STYLE[meta.tone];
  return (
    <span
      className="inline-block text-xs px-2 py-0.5 rounded-full mr-1 mb-1 font-medium"
      style={{ backgroundColor: t.bg, color: t.fg }}
    >
      {meta.label}
    </span>
  );
}

const STATUS_LABEL = {
  found: "In pool",
  absent: "Absent",
  unresolved: "Unresolved",
  no_case: "No case",
  no_candidates: "No candidates",
  error: "Error",
};

function SummaryBar({ summary }) {
  if (!summary) return null;
  const byStatus = summary.by_status || {};
  const chips = Object.entries(byStatus).map(([k, v]) => `${STATUS_LABEL[k] || k}: ${v}`);
  return (
    <div
      className="rounded-lg border px-4 py-3 text-sm flex flex-wrap gap-x-4 gap-y-1"
      style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)", color: "var(--ink)" }}
    >
      <span><strong>{summary.total}</strong> audited</span>
      {chips.map((c) => <span key={c} style={{ color: "var(--ink-muted)" }}>{c}</span>)}
      <span style={{ color: "var(--ink-muted)" }}>
        <strong style={{ color: "var(--ink)" }}>{summary.flagged_total}</strong> with audit flags
      </span>
    </div>
  );
}

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function TriagePanel({ onNavigate }) {
  const [disease, setDisease] = useState("");
  const [listText, setListText] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  function parseList(text) {
    return text
      .split(/[\n,;]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  }

  const drugs = parseList(listText);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!disease.trim() || drugs.length === 0) return;
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      const data = await triageCandidates(disease.trim(), drugs);
      setResult(data);
    } catch (err) {
      setError(err.message || "Unexpected error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold" style={{ color: "var(--ink)" }}>Triage a candidate list</h3>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          Paste your organization's candidate list (up to 25 drugs). Each drug is
          adversarially audited against the persisted pool of one completed case —
          safety caps, direction caps, black-box advisories, XLogP cautions, and
          evidence coverage — with every verdict retrievable later by run id.
        </p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="rounded-xl border p-5 space-y-4"
        style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)", boxShadow: "var(--shadow-soft)" }}
      >
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--ink-muted)" }}>
            Disease context (completed case)
          </label>
          <input
            value={disease}
            onChange={(e) => setDisease(e.target.value)}
            placeholder="e.g. Idiopathic pulmonary arterial hypertension"
            className="audit-input w-full border rounded-lg px-3 py-2 text-sm focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--ink-muted)" }}>
            Candidate drugs — one per line (or comma-separated)
          </label>
          <textarea
            value={listText}
            onChange={(e) => setListText(e.target.value)}
            rows={7}
            placeholder={"Sildenafil\nTadalafil\nPhenobarbital"}
            className="audit-input w-full border rounded-lg px-3 py-2 text-sm focus:outline-none font-mono"
          />
          <div className="text-xs mt-1" style={{ color: "var(--ink-dim)" }}>
            {drugs.length} drug{drugs.length === 1 ? "" : "s"} parsed{drugs.length > 25 ? " — cap is 25 per run" : ""}
          </div>
        </div>
        <button
          type="submit"
          disabled={loading || !disease.trim() || drugs.length === 0 || drugs.length > 25}
          className="audit-submit w-full py-2.5 text-sm font-semibold rounded-lg transition-colors"
        >
          {loading ? `Auditing ${drugs.length} candidates…` : "Run triage audit"}
        </button>
      </form>

      {error && (
        <div className="rounded-lg border px-4 py-3 text-sm" style={{ borderColor: "#b9412f", color: "#b9412f" }}>
          Error: {error}
        </div>
      )}

      {result && result.status === "no_case" && (
        <div className="rounded-lg border px-4 py-4 text-sm space-y-2" style={{ borderColor: "var(--border)", color: "var(--ink)" }}>
          <p>No completed case exists for <strong>{disease}</strong>. Triage audits against a
          completed case's persisted pool — run the case first.</p>
          {onNavigate && (
            <button
              onClick={() => onNavigate("dashboard", { prefill: disease })}
              className="audit-chip text-xs px-3 py-1.5 rounded-full"
            >
              Start a case for {disease}
            </button>
          )}
        </div>
      )}

      {result && result.status === "no_candidates" && (
        <div className="rounded-lg border px-4 py-4 text-sm space-y-2" style={{ borderColor: "var(--border)", color: "var(--ink)" }}>
          <p>The case for <strong>{disease}</strong> predates per-job candidate persistence, so its
          pool cannot be audited. Re-run the case to generate a fresh candidates file.</p>
          {onNavigate && (
            <button
              onClick={() => onNavigate("dashboard", { prefill: disease })}
              className="audit-chip text-xs px-3 py-1.5 rounded-full"
            >
              Re-run {disease}
            </button>
          )}
        </div>
      )}

      {result && result.status === "ok" && (
        <div className="space-y-4">
          <DomainFindings findings={result.domain_findings || result.summary?.domain_findings} />
          <SummaryBar summary={result.summary} />

          <div className="rounded-xl border overflow-x-auto" style={{ borderColor: "var(--border)" }}>
            <table className="w-full text-sm">
              <thead>
                <tr style={{ color: "var(--ink-muted)" }} className="text-left text-xs uppercase tracking-wide">
                  <th className="px-3 py-2">Candidate</th>
                  <th className="px-3 py-2">Verdict</th>
                  <th className="px-3 py-2">Rank</th>
                  <th className="px-3 py-2">Score</th>
                  <th className="px-3 py-2">Audit flags</th>
                </tr>
              </thead>
              <tbody>
                {(result.verdicts || []).map((v) => (
                  <tr key={v.drug_name} className="border-t align-top" style={{ borderColor: "var(--border)" }}>
                    <td className="px-3 py-2 font-medium" style={{ color: "var(--ink)" }}>
                      {v.drug_name}
                      {v.resolved_chembl_id && (
                        <div className="text-xs font-normal" style={{ color: "var(--ink-dim)" }}>{v.resolved_chembl_id}</div>
                      )}
                    </td>
                    <td className="px-3 py-2" style={{ color: "var(--ink)" }}>{STATUS_LABEL[v.status] || v.status}</td>
                    <td className="px-3 py-2" style={{ color: "var(--ink)" }}>
                      {v.rank != null ? `${v.rank} / ${v.total_candidates}` : "—"}
                    </td>
                    <td className="px-3 py-2" style={{ color: "var(--ink)" }}>
                      {v.composite_score != null ? Number(v.composite_score).toFixed(3) : "—"}
                      {v.pre_cap_score != null && v.pre_cap_score !== v.composite_score && (
                        <div className="text-xs" style={{ color: "var(--ink-dim)" }}>
                          pre-cap {Number(v.pre_cap_score).toFixed(3)}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {(v.flags || []).length === 0
                        ? <span className="text-xs" style={{ color: "var(--ink-dim)" }}>none</span>
                        : v.flags.map((f) => <FlagBadge key={f} code={f} />)}
                      {v.cap_reason && (
                        <div className="text-xs mt-1" style={{ color: "var(--ink-muted)" }}>{v.cap_reason}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <button
              onClick={() => downloadJson(`agentbio-triage-${result.run_id || "run"}.json`, result)}
              className="audit-chip text-xs px-3 py-1.5 rounded-full"
            >
              Download audit pack (JSON)
            </button>
            {result.run_id && (
              <span className="text-xs" style={{ color: "var(--ink-dim)" }}>
                Run id <code>{result.run_id}</code> — this exact verdict set is retrievable later.
              </span>
            )}
          </div>

          <p className="text-xs leading-relaxed" style={{ color: "var(--ink-dim)" }}>{result.disclosure}</p>
        </div>
      )}
    </div>
  );
}
