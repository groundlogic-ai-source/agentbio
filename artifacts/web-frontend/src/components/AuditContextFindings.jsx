const STATUS_META = {
  ok:             { label: "available",      fg: "#3a6ea5", bg: "rgba(58, 110, 165, 0.12)" },
  empty:          { label: "healthy empty",  fg: "var(--ink-muted)", bg: "rgba(110, 110, 110, 0.10)" },
  filtered_empty: { label: "filtered empty", fg: "#8a6116", bg: "rgba(176, 125, 37, 0.12)" },
  degraded:       { label: "degraded",       fg: "#b9412f", bg: "rgba(185, 65, 47, 0.12)" },
  parse_failed:   { label: "parse failed",   fg: "#b9412f", bg: "rgba(185, 65, 47, 0.12)" },
  unavailable:    { label: "unavailable",    fg: "#b9412f", bg: "rgba(185, 65, 47, 0.12)" },
};

const FINDING_META = {
  flagged:    { label: "flagged",    fg: "#b9412f", border: "rgba(185, 65, 47, 0.38)", bg: "rgba(185, 65, 47, 0.06)" },
  review:     { label: "review",     fg: "#8a6116", border: "rgba(176, 125, 37, 0.38)", bg: "rgba(176, 125, 37, 0.06)" },
  unresolved: { label: "unresolved", fg: "var(--ink-muted)", border: "var(--border)", bg: "var(--surface-raised)" },
};

function StatusChip({ status }) {
  const meta = STATUS_META[status] || {
    label: status || "not reported",
    fg: "var(--ink-muted)",
    bg: "rgba(110, 110, 110, 0.10)",
  };
  return (
    <span
      className="inline-flex rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color: meta.fg, backgroundColor: meta.bg }}
    >
      {meta.label}
    </span>
  );
}

function Finding({ finding }) {
  const meta = FINDING_META[finding.status] || FINDING_META.unresolved;
  return (
    <div
      className="rounded-lg border px-3 py-2.5 space-y-1"
      style={{ borderColor: meta.border, backgroundColor: meta.bg }}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-xs font-semibold" style={{ color: meta.fg }}>
          {finding.code}
        </span>
        <span
          className="text-xs font-semibold uppercase tracking-wide"
          style={{ color: meta.fg }}
        >
          {meta.label}
        </span>
        <span className="text-sm font-medium" style={{ color: "var(--ink)" }}>
          {finding.title}
        </span>
      </div>
      <p className="text-xs leading-relaxed" style={{ color: "var(--ink-muted)" }}>
        {finding.rationale}
      </p>
      {finding.action && (
        <p className="text-xs" style={{ color: "var(--ink)" }}>
          <strong>Action:</strong> {finding.action}
        </p>
      )}
    </div>
  );
}

export default function AuditContextFindings({ context, compact = false }) {
  if (!context) return null;
  const sources = context.sources || {};
  const findings = context.findings || [];
  const regulatory = sources.regulatory_label || {};
  const literature = sources.entity_linked_literature || {};

  return (
    <details
      className="rounded-lg border"
      style={{ borderColor: "var(--border)", backgroundColor: "var(--surface)" }}
      open={!compact && findings.some((finding) => finding.status === "flagged")}
    >
      <summary
        className="cursor-pointer px-3 py-2.5 text-sm font-medium"
        style={{ color: "var(--ink)" }}
      >
        Audit evidence · {findings.length} N1–N4 disclosure{findings.length === 1 ? "" : "s"}
      </summary>
      <div className="px-3 pb-3 space-y-3">
        <div className="flex flex-wrap gap-2 text-xs" style={{ color: "var(--ink-muted)" }}>
          <span>Regulatory label <StatusChip status={regulatory.status} /></span>
          <span>Entity-linked literature <StatusChip status={literature.status} /></span>
          <span>cutoff strictly before {context.citation_cutoff || "2026-08-10"}</span>
        </div>

        {findings.length > 0 ? (
          <div className="space-y-2">
            {findings.map((finding, index) => (
              <Finding
                key={`${finding.code}-${finding.status}-${index}`}
                finding={finding}
              />
            ))}
          </div>
        ) : (
          <p className="text-xs" style={{ color: "var(--ink-muted)" }}>
            No N1–N4 detector disclosure was emitted from the available structured evidence.
          </p>
        )}

        <p className="text-xs leading-relaxed" style={{ color: "var(--ink-dim)" }}>
          Research evidence audit only. These disclosures never adjust scores, ranks,
          caps, or verdicts. Empty, filtered, degraded, malformed, and unavailable
          source states remain distinct.
        </p>
      </div>
    </details>
  );
}