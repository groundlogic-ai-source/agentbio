import StatusBadge from "./StatusBadge.jsx";
import { diseaseLabel, formatCost, formatDate } from "../lib/stages.js";

function FolderTab({ job, onOpen }) {
  const caseId = (job.job_id || "").slice(-6).toUpperCase();

  return (
    <button
      type="button"
      onClick={() => onOpen(job.job_id)}
      className="group flex w-68 shrink-0 flex-col rounded-t-lg border border-b-0 p-4 text-left"
      style={{
        width: "17rem",
        backgroundColor: "var(--paper)",
        borderColor: "var(--silver)",
        borderLeft: "4px solid var(--brass)",
        color: "var(--ink)",
        boxShadow: "0 -1px 0 rgba(0,0,0,0.04), 2px -2px 8px rgba(0,0,0,0.06)",
        transition: "transform 0.18s ease, box-shadow 0.18s ease",
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
          style={{ color: "var(--brass)" }}
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
        <span style={{ color: "var(--brass)" }}>
          {formatCost(job.total_cost_usd)}
        </span>
      </div>
    </button>
  );
}

export default function Dashboard({ runs, onOpenCase, onNewCase }) {
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
        <div
          className="mb-3 font-mono text-[0.62rem] uppercase tracking-[0.22em]"
          style={{ color: "var(--silver-dim)" }}
        >
          Case files
        </div>

        {runs.length === 0 ? (
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
        ) : (
          <div
            className="flex gap-3 overflow-x-auto pb-0"
            style={{
              borderBottom: "3px solid rgba(199,202,209,0.35)",
              scrollSnapType: "x proximity",
            }}
          >
            {runs.map((job) => (
              <div key={job.job_id} style={{ scrollSnapAlign: "start" }}>
                <FolderTab job={job} onOpen={onOpenCase} />
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
