// The REAL pipeline sequence, in order. These keys match the backend's
// current_stage values 1:1 (plus the awaiting_review checkpoint at the end).
export const STAGES = [
  { key: "target_selection", label: "Target Selection" },
  { key: "biologist", label: "Biologist" },
  { key: "chemist", label: "Chemist" },
  { key: "reviewer", label: "Reviewer" },
  { key: "structure_validation", label: "Structure Validation" },
  { key: "writer", label: "Writer" },
  { key: "awaiting_review", label: "Awaiting Review" },
];

export const TERMINAL_STATUSES = new Set(["completed", "error"]);

export function isTerminal(status) {
  return TERMINAL_STATUSES.has(status);
}

// Derive stepper progress from the backend contract. IMPORTANT: while a run is
// in progress the backend sets current_stage to the LAST COMPLETED node (it is
// updated after each node finishes), so the stage actually executing is the one
// AFTER current_stage. We therefore check off current_stage (and everything
// before it) and highlight the next stage as active.
//
// Returns { completedThrough, activeIndex }:
//   stage i is "done"   when i <= completedThrough
//   stage i is "active" when i === activeIndex
//   stage i is "pending" otherwise
export function stepperProgress(status, currentStage) {
  const last = STAGES.length - 1; // index of "Awaiting Review"

  if (status === "completed") {
    return { completedThrough: last, activeIndex: -1 }; // every stage checked
  }
  if (status === "awaiting_review") {
    return { completedThrough: last - 1, activeIndex: last }; // pipeline done
  }
  if (status === "queued") {
    return { completedThrough: -1, activeIndex: 0 }; // nothing run yet
  }

  // status === "running" (or any other live state): current_stage is the last
  // completed node; the next stage is what's executing now.
  const idx = STAGES.findIndex((s) => s.key === currentStage);
  const completedThrough = idx < 0 ? -1 : idx;
  const activeIndex = Math.min(completedThrough + 1, last);
  return { completedThrough, activeIndex };
}

export function diseaseLabel(job) {
  if (job?.disease_name) return job.disease_name;
  if (job && !isTerminal(job.status)) return "Auto-selecting candidate…";
  return "Untitled case";
}

export function formatCost(value) {
  const n = typeof value === "number" ? value : 0;
  return `$${n.toFixed(3)}`;
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
