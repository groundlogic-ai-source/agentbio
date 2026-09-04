const STYLES = {
  queued:          { label: "Queued",          color: "var(--ink-muted)" },
  running:         { label: "In progress",     color: "var(--ink-muted)" },
  awaiting_review: { label: "Awaiting review", color: "var(--brass)"  },
  error:           { label: "Error",           color: "var(--oxide)"  },
  // Ran correctly, found nothing recommendable — not an error, not a sign-off.
  no_eligible_candidate: { label: "No eligible candidate", color: "var(--ink-muted)" },
  // Required evidence services failed. This attempt is retryable, but did not
  // produce a result and must not be confused with no_eligible_candidate.
  source_unavailable: { label: "Evidence services unavailable", color: "var(--oxide)" },
  degraded_unscorable: {
    label: "Literature checks unavailable",
    color: "var(--oxide)",
  },
};

export default function StatusBadge({ status, decision }) {
  let { label, color } = STYLES[status] || { label: status, color: "var(--ink-muted)" };

  if (status === "completed") {
    if (decision === "approve") {
      label = "Approved";
      color = "var(--brass)";
    } else if (decision === "reject") {
      label = "Rejected";
      color = "var(--oxide)";
    } else {
      label = "Signed off";
      color = "var(--ink-muted)";
    }
  }

  const live = status === "running" || status === "queued";

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 text-[0.7rem] font-medium"
      style={{
        color,
        borderColor: color,
        backgroundColor: `color-mix(in srgb, ${color} 8%, transparent)`,
      }}
    >
      {live && (
        <span
          className="pulse-dot inline-block h-1.5 w-1.5 rounded-full shrink-0"
          style={{ backgroundColor: color }}
          aria-hidden="true"
        />
      )}
      {label}
    </span>
  );
}
