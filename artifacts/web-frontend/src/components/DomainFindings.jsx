// Renders confirmed research findings that apply to the audited indication.
// Base-rate context only — findings never adjust scores, ranks, or verdicts.

function StatChip({ label, value }) {
  return (
    <span
      className="inline-block text-xs px-2 py-1 rounded-md mr-2 mb-1"
      style={{ backgroundColor: "rgba(176, 125, 37, 0.12)", color: "var(--ink)" }}
    >
      <span style={{ color: "var(--ink-muted)" }}>{label}: </span>
      <strong>{value}</strong>
    </span>
  );
}

const CONFOUND_TONE = {
  survives:     { label: "survives adjustment", fg: "#3a6ea5" },
  not_testable: { label: "not testable",        fg: "#8a6116" },
  fails:        { label: "does not survive",    fg: "#b9412f" },
};

function FindingCard({ finding }) {
  const s = finding.stats || {};
  return (
    <div
      className="rounded-lg border px-4 py-3 text-sm space-y-2"
      style={{
        borderColor: "rgba(176, 125, 37, 0.45)",
        backgroundColor: "rgba(176, 125, 37, 0.07)",
        color: "var(--ink)",
      }}
    >
      <div className="flex items-baseline flex-wrap gap-x-2">
        <span
          className="text-xs font-semibold uppercase tracking-wide"
          style={{ color: "#8a6116" }}
        >
          Research finding · {finding.domain}
        </span>
        <span className="text-xs" style={{ color: "var(--ink-dim)" }}>
          matched on “{finding.matched_term}”
        </span>
      </div>

      <p className="font-medium">{finding.title}.</p>

      <div>
        {s.odds_ratio != null && (
          <StatChip label="Odds ratio" value={`${s.odds_ratio} [${(s.ci95 || []).join(", ")}]`} />
        )}
        {s.n != null && <StatChip label="n" value={s.n.toLocaleString()} />}
        {s.discovery_fdr_q != null && <StatChip label="Discovery FDR q" value={s.discovery_fdr_q.toExponential(1)} />}
        {s.confirmation_raw_p != null && <StatChip label="Holdout confirmation p" value={s.confirmation_raw_p.toExponential(1)} />}
        {s.framing && <StatChip label="Framing" value={s.framing} />}
      </div>

      <p className="leading-relaxed" style={{ color: "var(--ink)" }}>{finding.implication}</p>

      {(finding.confounds || []).length > 0 && (
        <ul className="space-y-1">
          {finding.confounds.map((c) => {
            const tone = CONFOUND_TONE[c.status] || CONFOUND_TONE.not_testable;
            return (
              <li key={c.name} className="text-xs leading-relaxed" style={{ color: "var(--ink-muted)" }}>
                <strong style={{ color: tone.fg }}>{tone.label}</strong>
                {" — "}{c.name}: {c.detail}
              </li>
            );
          })}
        </ul>
      )}

      {(finding.cautions || []).map((c, i) => (
        <p key={i} className="text-xs leading-relaxed" style={{ color: "#8a6116" }}>
          Caution: {c}
        </p>
      ))}

      <p className="text-xs" style={{ color: "var(--ink-dim)" }}>
        Base-rate context only — this finding never adjusts scores, ranks, caps, or verdicts.
        {finding.provenance && (
          <> Provenance: hypothesis {finding.provenance.hypothesis_id} · {finding.provenance.registry}.</>
        )}
      </p>
    </div>
  );
}

export default function DomainFindings({ findings }) {
  if (!findings || findings.length === 0) return null;
  return (
    <div className="space-y-3">
      {findings.map((f) => <FindingCard key={f.id} finding={f} />)}
    </div>
  );
}
