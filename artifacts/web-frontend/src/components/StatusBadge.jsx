const STYLES = {
  queued:          { label: "Queued",          color: "var(--silver)" },
  running:         { label: "In progress",     color: "var(--silver)" },
  awaiting_review: { label: "Awaiting review", color: "var(--brass)"  },
  error:           { label: "Error",           color: "var(--oxide)"  },
};

export default function StatusBadge({ status, decision }) {
  let { label, color } = STYLES[status] || { label: status, color: "var(--silver)" };

  if (status === "completed") {
    if (decision === "approve") {
      label = "Approved";
      color = "var(--brass)";
    } else if (decision === "reject") {
      label = "Rejected";
      color = "var(--oxide)";
    } else {
      label = "Signed off";
      color = "var(--silver)";
    }
  }

  const live = status === "running" || status === "queued";

  return (
    <span
      className="inline-flex shrink-0 items-center gap-1.5 rounded border px-2 py-0.5 font-mono text-[0.6rem] uppercase tracking-[0.14em]"
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
