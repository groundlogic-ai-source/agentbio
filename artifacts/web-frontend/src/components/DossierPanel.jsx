import { useEffect, useState } from "react";
import { getAuditDossiers, getDossierClaims } from "../api";

// ── Audit status presentation ─────────────────────────────────────────────────
const STATUS_META = {
  verified:               { label: "Verified",                tone: "safe"    },
  verified_with_gaps:     { label: "Verified — open confounds", tone: "info"  },
  not_confirmed:          { label: "Not confirmed",           tone: "warning" },
  confound_fail:          { label: "Confound fail",           tone: "danger"  },
  label_artifact_suspect: { label: "Label artifact suspect",  tone: "danger"  },
  not_tested:             { label: "Not tested",              tone: "neutral" },
  unverifiable:           { label: "Unverifiable snapshot",   tone: "neutral" },
};

const TONE_STYLE = {
  safe:    { bg: "rgba(46, 125, 78, 0.15)",  fg: "#2e7d4e" },
  info:    { bg: "rgba(58, 110, 165, 0.14)", fg: "#3a6ea5" },
  warning: { bg: "rgba(176, 125, 37, 0.16)", fg: "#8a6116" },
  danger:  { bg: "rgba(185, 65, 47, 0.14)",  fg: "#b9412f" },
  neutral: { bg: "rgba(110, 110, 110, 0.14)",fg: "var(--ink-muted)" },
};

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || { label: status, tone: "neutral" };
  const t = TONE_STYLE[meta.tone];
  return (
    <span
      className="inline-block text-xs px-2.5 py-1 rounded-full font-semibold"
      style={{ backgroundColor: t.bg, color: t.fg }}
    >
      {meta.label}
    </span>
  );
}

function fmtP(p) {
  if (p === null || p === undefined) return "—";
  const n = Number(p);
  if (!Number.isFinite(n)) return "—";
  if (n !== 0 && Math.abs(n) < 1e-3) return n.toExponential(2);
  return String(Math.round(n * 1e6) / 1e6);
}

function PassMark({ value }) {
  if (value === true) return <span style={{ color: "#2e7d4e" }}>pass</span>;
  if (value === false) return <span style={{ color: "#b9412f" }}>fail</span>;
  return <span style={{ color: "var(--ink-dim)" }}>—</span>;
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

function ClaimLedger({ hypothesisId, onClose }) {
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    getDossierClaims(hypothesisId)
      .then((data) => { if (!cancelled) setState({ loading: false, error: null, data }); })
      .catch((err) => { if (!cancelled) setState({ loading: false, error: err.message, data: null }); });
    return () => { cancelled = true; };
  }, [hypothesisId]);

  if (state.loading) return <div className="text-sm py-6" style={{ color: "var(--ink-muted)" }}>Loading claim ledger…</div>;
  if (state.error) return <div className="text-sm py-6" style={{ color: "#b9412f" }}>Error: {state.error}</div>;
  const d = state.data;

  return (
    <div className="space-y-5 mt-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <StatusBadge status={d.audit_status} />
          <h4 className="text-base font-semibold mt-2" style={{ color: "var(--ink)" }}>{d.hypothesis_text}</h4>
          <div className="text-xs mt-1" style={{ color: "var(--ink-dim)" }}>
            {d.hypothesis_id} · facts fingerprint <code>{(d.fingerprint || "").slice(0, 12)}…</code>
          </div>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => downloadJson(`agentbio-audit-pack-${d.hypothesis_id}.json`, d)}
            className="audit-chip text-xs px-3 py-1.5 rounded-full"
          >
            Download audit pack (JSON)
          </button>
          <button onClick={onClose} className="audit-chip text-xs px-3 py-1.5 rounded-full">Close</button>
        </div>
      </div>

      {(d.status_reasons || []).map((r, i) => (
        <p key={i} className="text-sm" style={{ color: "var(--ink-muted)" }}>{r}</p>
      ))}

      {/* Framings */}
      <div className="rounded-xl border overflow-x-auto" style={{ borderColor: "var(--border)" }}>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide" style={{ color: "var(--ink-muted)" }}>
              <th className="px-3 py-2">Framing</th>
              <th className="px-3 py-2">Test</th>
              <th className="px-3 py-2">Discovery p</th>
              <th className="px-3 py-2">FDR q</th>
              <th className="px-3 py-2">Discovery</th>
              <th className="px-3 py-2">Confirm p</th>
              <th className="px-3 py-2">Confirmed</th>
            </tr>
          </thead>
          <tbody>
            {(d.framings || []).map((f, i) => (
              <tr key={i} className="border-t" style={{ borderColor: "var(--border)", color: "var(--ink)" }}>
                <td className="px-3 py-2">{f.framing || "—"}</td>
                <td className="px-3 py-2">{f.test_type || "—"}</td>
                <td className="px-3 py-2">{fmtP(f.discovery_raw_p)}</td>
                <td className="px-3 py-2">{fmtP(f.discovery_fdr_q)}</td>
                <td className="px-3 py-2"><PassMark value={f.discovery_pass} /></td>
                <td className="px-3 py-2">{fmtP(f.confirmation_raw_p)}</td>
                <td className="px-3 py-2"><PassMark value={f.confirmation_pass} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Confounds */}
      {(d.confounds || []).length > 0 && (
        <div>
          <h5 className="text-xs font-semibold uppercase tracking-wide mb-2" style={{ color: "var(--ink-muted)" }}>
            Confound checks
          </h5>
          <div className="space-y-2">
            {d.confounds.map((c, i) => (
              <div key={i} className="rounded-lg border px-3 py-2 text-sm" style={{ borderColor: "var(--border)" }}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium" style={{ color: "var(--ink)" }}>{c.name}</span>
                  {c.computable
                    ? (c.survives_adjustment
                        ? <span className="text-xs" style={{ color: "#2e7d4e" }}>survives adjustment</span>
                        : <span className="text-xs" style={{ color: "#b9412f" }}>does not survive</span>)
                    : <span className="text-xs" style={{ color: "#8a6116" }}>not testable from dataset — disclosed, not adjusted</span>}
                </div>
                {c.rationale && <p className="text-xs mt-1" style={{ color: "var(--ink-muted)" }}>{c.rationale}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reviewer tags + provenance */}
      {(d.reviewer_tags || []).length > 0 && (
        <div className="text-sm" style={{ color: "var(--ink-muted)" }}>
          <span className="text-xs font-semibold uppercase tracking-wide">Reviewer tags: </span>
          {d.reviewer_tags.join(", ")}
        </div>
      )}

      {/* Verification notes */}
      <div className="rounded-lg border px-4 py-3" style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)" }}>
        <h5 className="text-xs font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--ink-muted)" }}>
          How this was verified
        </h5>
        <ul className="text-xs space-y-1 list-disc pl-4" style={{ color: "var(--ink-muted)" }}>
          {(d.verification_notes || []).map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      </div>
    </div>
  );
}

export default function DossierPanel() {
  const [state, setState] = useState({ loading: true, error: null, dossiers: [] });
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    getAuditDossiers()
      .then((rows) => setState({ loading: false, error: null, dossiers: rows || [] }))
      .catch((err) => setState({ loading: false, error: err.message, dossiers: [] }));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold" style={{ color: "var(--ink)" }}>Dossier audit workspace</h3>
        <p className="text-sm mt-1" style={{ color: "var(--ink-muted)" }}>
          Saved hypothesis reports re-verified against the live registry on every
          load — discovery FDR, holdout confirmation, confound survival, and
          labeling-artifact screens. The saved narrative stays frozen; the audit
          status does not.
        </p>
      </div>

      {state.loading && <div className="text-sm" style={{ color: "var(--ink-muted)" }}>Loading dossiers…</div>}
      {state.error && <div className="text-sm" style={{ color: "#b9412f" }}>Error: {state.error}</div>}

      {!state.loading && !state.error && state.dossiers.length === 0 && (
        <div className="rounded-lg border px-4 py-4 text-sm" style={{ borderColor: "var(--border)", color: "var(--ink-muted)" }}>
          No saved reports yet. Generate and save a hypothesis report from the Research
          tab; it then appears here as an auditable dossier.
        </div>
      )}

      <div className="space-y-2">
        {state.dossiers.map((d) => (
          <button
            key={d.id}
            onClick={() => setSelected(selected === d.hypothesis_id ? null : d.hypothesis_id)}
            className="w-full text-left rounded-xl border px-4 py-3 transition-colors"
            style={{
              borderColor: selected === d.hypothesis_id ? "var(--accent, #3a6ea5)" : "var(--border)",
              backgroundColor: "var(--surface)",
            }}
          >
            <div className="flex items-center gap-3 flex-wrap">
              <StatusBadge status={d.audit_status} />
              <span className="text-sm font-medium flex-1" style={{ color: "var(--ink)" }}>
                {d.hypothesis_text || d.hypothesis_id}
              </span>
              <span className="text-xs" style={{ color: "var(--ink-dim)" }}>
                saved {d.saved_at ? new Date(d.saved_at).toLocaleDateString() : "—"}
              </span>
            </div>
            {selected === d.hypothesis_id && (
              <div onClick={(e) => e.stopPropagation()}>
                <ClaimLedger hypothesisId={d.hypothesis_id} onClose={() => setSelected(null)} />
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
