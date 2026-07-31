import { useState } from "react";
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
        backgroundColor: isArchived ? "var(--surface-raised)" : "var(--surface)",
        borderColor: isArchived ? "var(--border-light)" : "var(--border)",
        borderLeft: `4px solid ${isArchived ? "var(--border)" : "var(--brass)"}`,
        color: "var(--ink-base)",
        boxShadow: "0 -1px 0 rgba(0,0,0,0.02), 2px -2px 8px rgba(0,0,0,0.04)",
        transition: "transform 0.18s ease, box-shadow 0.18s ease",
        opacity: isArchived ? 0.6 : 1,
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
          "0 -1px 0 rgba(0,0,0,0.04), 2px -6px 18px rgba(0,0,0,0.1)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "translateY(0)";
        e.currentTarget.style.boxShadow =
          "0 -1px 0 rgba(0,0,0,0.02), 2px -2px 8px rgba(0,0,0,0.04)";
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5">
          {!isArchived && (
            <button
              type="button"
              aria-label="Archive this case"
              onClick={(e) => {
                e.stopPropagation();
                onArchive(job.job_id);
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = "var(--oxide)";
                e.currentTarget.style.borderColor = "var(--oxide)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = "var(--ink-dim)";
                e.currentTarget.style.borderColor = "var(--border)";
              }}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                width: "1.1rem",
                height: "1.1rem",
                borderRadius: "3px",
                border: "1px solid var(--border)",
                background: "transparent",
                color: "var(--ink-dim)",
                fontSize: "0.65rem",
                lineHeight: 1,
                cursor: "pointer",
                flexShrink: 0,
                transition: "color 0.15s ease, border-color 0.15s ease",
              }}
            >
              ✕
            </button>
          )}
          <span
            className="font-mono text-[0.6rem] uppercase tracking-[0.14em]"
            style={{ color: isArchived ? "var(--ink-dim)" : "var(--brass-deep)" }}
          >
            Case #{caseId}
          </span>
        </div>
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
        <span style={{ color: isArchived ? "var(--ink-muted)" : "var(--brass-deep)" }}>
          {formatCost(job.total_cost_usd)}
        </span>
      </div>

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
  onBatchScan,
  batchState,
}) {
  const [batchN, setBatchN] = useState(3);
  const [batchBusy, setBatchBusy] = useState(false);

  const archivedCount = runs.filter((j) => j.archived).length;
  const hasAny = runs.length > 0;
  const noneVisible =
    !showArchived && runs.length > 0 && runs.every((j) => j.archived);

  const reviewQueue = runs.filter((j) => !j.archived && j.status === "awaiting_review");
  const batchRunning = batchState && batchState.status === "running";
  const batchDone = batchState && batchState.status === "done";

  async function handleBatch() {
    if (batchBusy || batchRunning) return;
    setBatchBusy(true);
    try {
      await onBatchScan(batchN);
    } finally {
      setBatchBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
      <header className="flex flex-col gap-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <div
            className="font-mono text-[0.62rem] uppercase tracking-[0.14em] mb-2"
            style={{ color: "var(--brass-deep)" }}
          >
            Drug Repurposing Research System
          </div>
          <h1
            className="text-5xl font-bold tracking-tight leading-none"
            style={{
              fontFamily: "Inter, system-ui, sans-serif",
              color: "var(--ink)",
            }}
          >
            AgentBio
          </h1>
          <p
            className="mt-3 max-w-lg text-sm leading-relaxed"
            style={{ color: "var(--ink-muted)" }}
          >
            A case file for every drug-repurposing hypothesis — evidence,
            citations, and limitations, compiled for human review. Each candidate
            requires wet-lab validation; nothing here is a cure.
          </p>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap items-center gap-3 shrink-0">
          {/* Batch scan controls */}
          <div className="flex items-center gap-2">
            <select
              value={batchN}
              onChange={(e) => setBatchN(Number(e.target.value))}
              disabled={batchBusy || batchRunning}
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: "6px",
                color: "var(--ink-base)",
                fontFamily: "var(--font-mono, monospace)",
                fontSize: "0.72rem",
                padding: "0.3rem 0.5rem",
                cursor: batchBusy || batchRunning ? "not-allowed" : "pointer",
                opacity: batchBusy || batchRunning ? 0.5 : 1,
              }}
            >
              {[3, 5, 10].map((n) => (
                <option key={n} value={n}>{n} cases</option>
              ))}
            </select>
            <button
              type="button"
              onClick={handleBatch}
              disabled={batchBusy || batchRunning}
              className="btn btn-ghost"
            >
              {batchRunning
                ? `Scanning ${batchState.completed}/${batchState.n}…`
                : "Batch scan"}
            </button>
          </div>

          <button
            type="button"
            onClick={onNewCase}
            className="btn btn-primary"
          >
            Open new case
          </button>
        </div>
      </header>

      {/* Batch progress banner */}
      {batchRunning && (
        <div
          className="mt-6 rounded-lg border px-4 py-3 font-mono text-xs fade-in"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-raised)",
            color: "var(--ink-muted)",
          }}
        >
          <span style={{ color: "var(--ink-base)" }}>Batch scan in progress — </span>
          {batchState.completed} of {batchState.n} cases explored.
          New cases appear below as they complete.
        </div>
      )}
      {batchDone && (
        <div
          className="mt-6 rounded-lg border px-4 py-3 font-mono text-xs fade-in"
          style={{
            borderColor: "var(--brass-border)",
            background: "var(--brass-glow)",
            color: "var(--brass-deep)",
          }}
        >
          Batch scan complete — {batchState.n} case{batchState.n !== 1 ? "s" : ""} explored.
        </div>
      )}

      {/* Review queue — prominent band when cases need human sign-off */}
      {reviewQueue.length > 0 && (
        <section
          className="mt-8 rounded-lg border p-5 fade-in"
          style={{
            borderColor: "var(--brass)",
            background: "var(--brass-glow)",
          }}
        >
          <div className="flex items-center gap-3 mb-3">
            <span
              className="font-mono text-[0.62rem] uppercase tracking-[0.14em]"
              style={{ color: "var(--brass-deep)" }}
            >
              Review queue
            </span>
            <span
              className="font-mono text-[0.62rem]"
              style={{ color: "var(--ink-muted)" }}
            >
              {reviewQueue.length} case{reviewQueue.length !== 1 ? "s" : ""} awaiting sign-off
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {reviewQueue.map((job) => (
              <button
                key={job.job_id}
                type="button"
                onClick={() => onOpenCase(job.job_id)}
                className="btn btn-sm btn-ghost-brass"
              >
                {diseaseLabel(job)}&nbsp;→
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="mt-10">
        {/* Section header row */}
        <div className="mb-3 flex items-center justify-between gap-4">
          <div
            className="font-mono text-[0.62rem] uppercase tracking-[0.14em]"
            style={{ color: "var(--ink-dim)" }}
          >
            Case files
          </div>
          {hasAny && (
            <button
              type="button"
              onClick={onToggleArchived}
              className={`btn btn-xs ${showArchived ? "btn-ghost-brass" : "btn-ghost"}`}
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
            style={{ borderColor: "var(--border)" }}
          >
            <p
              className="text-base font-medium"
              style={{ color: "var(--ink)" }}
            >
              No cases yet — open one to start.
            </p>
            <button
              type="button"
              onClick={onNewCase}
              className="btn btn-primary mt-5"
            >
              Open new case
            </button>
          </div>
        )}

        {/* All cases hidden behind archive filter */}
        {noneVisible && (
          <div
            className="rounded-lg border border-dashed p-8 text-center fade-in"
            style={{ borderColor: "var(--border-light)" }}
          >
            <p
              className="text-sm"
              style={{ color: "var(--ink-muted)" }}
            >
              All cases are archived.{" "}
              <button
                type="button"
                className="underline"
                style={{ color: "var(--ink-base)" }}
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
              borderBottom: "3px solid var(--border)",
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
