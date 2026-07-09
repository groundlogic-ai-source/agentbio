import StatusBadge from "./StatusBadge.jsx";
import { diseaseLabel, formatCost, formatDate } from "../lib/stages.js";

function FolderTab({ job, onOpen, onArchive }) {
  const caseId = (job.job_id || "").slice(-6).toUpperCase();
  const isArchived = Boolean(job.archived);

  return (
    <div
      className="group relative flex shrink-0 flex-col rounded-t-lg border border-b-0 p-4 text-left cursor-pointer"
      style={{
        width: "17rem",
        backgroundColor: isArchived ? "var(--graphite-raised)" : "var(--paper)",
        borderColor: isArchived ? "rgba(199,202,209,0.35)" : "var(--silver)",
        borderLeft: `4px solid ${isArchived ? "rgba(199,202,209,0.4)" : "var(--brass)"}`,
        color: "var(--ink)",
        boxShadow: "0 -1px 0 rgba(0,0,0,0.04), 2px -2px 8px rgba(0,0,0,0.06)",
        transition: "transform 0.18s ease, box-shadow 0.18s ease",
        opacity: isArchived ? 0.65 : 1,
      }}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(job.job_id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(job.job_id);
        }
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "translateY(-5px)";
        e.currentTarget.style.boxShadow =
          "0 -1px 0 rgba(0,0,0,0.06), 2px -6px 18px rgba(0,0,0,0.14)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
        e.currentTarget.style.boxShadow =
          "0 -1px 0 rgba(0,0,0,0.04), 2px -2px 8px rgba(0,0,0,0.06)";
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className="font-mono text-[0.58rem] uppercase tracking-[0.2em]"
          style={{ color: isArchived ? "var(--silver-dim)" : "var(--brass)" }}
        >
          Case #{caseId}
        </span>
        <StatusBadge status={job.status} decision={job.decision} />
      </div>

      <div className="mt-2">
        <span
          className="text-sm font-semibold leading-snug"
          style={{ color: "var(--ink)" }}
        >
          {diseaseLabel(job)}
        </span>
      </div>

      <div
        className="mt-auto pt-3 flex items-center justify-between font-mono text-[0.62rem]"
        style={{ color: "var(--ink-muted)" }}
      >
        <span>{formatDate(job.created_at)}</span>
        <span style={{ color: isArchived ? "var(--ink-muted)" : "var(--brass)" }}>
          {formatCost(job.total_cost_usd)}
        </span>
      </div>

      {/* Archive / unarchive button — appears on hover */}
      {!isArchived && (
        <button
          type="button"
          className="absolute bottom-3 right-3 rounded px-2 py-0.5 font-mono text-[0.55rem] uppercase tracking-[0.12em] opacity-0 group-hover:opacity-100"
          style={{
            color: "var(--ink-muted)",
            border: "1px solid rgba(199,202,209,0.3)",
            backgroundColor: "rgba(28,29,33,0.06)",
            transition: "opacity 0.15s ease, color 0.15s ease",
          }}
          onClick={(e) => {
            e.stopPropagation();
            onArchive(job.job_id);
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--oxide)";
            e.currentTarget.style.borderColor = "var(--oxide)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--ink-muted)";
            e.currentTarget.style.borderColor = "rgba(199,202,209,0.3)";
          }}
          aria-label="Archive this case"
        >
          Archive
        </button>
      )}
    </div>
  );
}

export default function Dashboard({
  runs,
  showArchived,
  onOpenCase,
  onNewCase,
  onArchive,
  onToggleArchived,
}) {
  const archivedCount = runs.filter((j) => j.archived).length;
  const activeCount = runs.filter((j) => !j.archived).length;
  const hasAny = runs.length > 0;
  const noneVisible =
    !showArchived && runs.length > 0 && runs.every((j) => j.archived);

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div
            className="font-mono text-[0.6rem] uppercase tracking-[0.28em] mb-2"
            style={{ color: "var(--brass)" }}
          >
            Drug Repurposing Research System
          </div>
          <h1
            className="text-5xl font-bold tracking-tight leading-none"
            style={{
              fontFamily: "Inter, system-ui, sans-serif",
              color: "var(--paper)",
            }}
          >
            AgentBio
          </h1>
          <p
            className="mt-3 max-w-lg text-sm leading-relaxed"
            style={{ color: "var(--silver)" }}
          >
            A case file for every drug-repurposing hypothesis — evidence,
            citations, and limitations, compiled for human review. Each candidate
            requires wet-lab validation; nothing here is a cure.
          </p>
        </div>
        <button
          type="button"
          onClick={onNewCase}
          className="shrink-0 rounded px-5 py-2.5 text-sm font-semibold"
          style={{
            backgroundColor: "var(--brass)",
            color: "var(--paper)",
            boxShadow: "0 2px 8px rgba(192,138,53,0.3)",
            transition: "background-color 0.15s ease, transform 0.12s ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = "var(--brass-deep)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = "var(--brass)";
          }}
          onMouseDown={(e) => {
            e.currentTarget.style.transform = "scale(0.97)";
          }}
          onMouseUp={(e) => {
            e.currentTarget.style.transform = "scale(1)";
          }}
        >
          Open new case
        </button>
      </header>

      <section className="mt-10">
        {/* Section header row */}
        <div className="mb-3 flex items-center justify-between gap-4">
          <div
            className="font-mono text-[0.62rem] uppercase tracking-[0.22em]"
            style={{ color: "var(--silver-dim)" }}
          >
            Case files
          </div>
          {hasAny && (
            <button
              type="button"
              onClick={onToggleArchived}
              className="font-mono text-[0.58rem] uppercase tracking-[0.15em] rounded px-2.5 py-1"
              style={{
                color: showArchived ? "var(--brass)" : "var(--ink-muted)",
                border: `1px solid ${showArchived ? "rgba(192,138,53,0.4)" : "rgba(199,202,209,0.25)"}`,
                backgroundColor: showArchived
                  ? "rgba(192,138,53,0.08)"
                  : "transparent",
                transition: "all 0.15s ease",
              }}
            >
              {showArchived
                ? `Hide archived (${archivedCount})`
                : `Show archived${archivedCount > 0 ? ` (${archivedCount})` : ""}`}
            </button>
          )}
        </div>

        {/* Empty state: no cases at all */}
        {!hasAny && (
          <div
            className="rounded-lg border border-dashed p-12 text-center fade-in"
            style={{ borderColor: "rgba(199,202,209,0.3)" }}
          >
            <p
              className="text-base font-medium"
              style={{ color: "var(--paper)" }}
            >
              No cases yet — open one to start.
            </p>
            <button
              type="button"
              onClick={onNewCase}
              className="mt-5 rounded px-4 py-2 text-sm font-semibold"
              style={{
                backgroundColor: "var(--brass)",
                color: "var(--paper)",
                transition: "background-color 0.15s ease",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.backgroundColor = "var(--brass-deep)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.backgroundColor = "var(--brass)";
              }}
            >
              Open new case
            </button>
          </div>
        )}

        {/* All cases hidden behind archive filter */}
        {noneVisible && (
          <div
            className="rounded-lg border border-dashed p-8 text-center fade-in"
            style={{ borderColor: "rgba(199,202,209,0.2)" }}
          >
            <p
              className="text-sm"
              style={{ color: "var(--ink-muted)" }}
            >
              All cases are archived.{" "}
              <button
                type="button"
                className="underline"
                style={{ color: "var(--silver)" }}
                onClick={onToggleArchived}
              >
                Show archived
              </button>
            </p>
          </div>
        )}

        {/* Case file tabs */}
        {hasAny && !noneVisible && (
          <div
            className="flex gap-3 overflow-x-auto pb-0"
            style={{
              borderBottom: "3px solid rgba(199,202,209,0.35)",
              scrollSnapType: "x proximity",
            }}
          >
            {runs.map((job) => (
              <div key={job.job_id} style={{ scrollSnapAlign: "start" }}>
                <FolderTab
                  job={job}
                  onOpen={onOpenCase}
                  onArchive={onArchive}
                />
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
