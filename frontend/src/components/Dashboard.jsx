import StatusBadge from "./StatusBadge.jsx";
import { diseaseLabel, formatCost, formatDate } from "../lib/stages.js";

function FolderTab({ job, onOpen }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(job.job_id)}
      className="group flex w-64 shrink-0 flex-col rounded-t-lg border border-b-0 p-4 text-left transition-transform hover:-translate-y-1"
      style={{
        backgroundColor: "var(--paper)",
        borderColor: "var(--silver)",
        borderLeft: "4px solid var(--brass)",
        color: "var(--ink)",
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-display text-base font-semibold leading-tight">
          {diseaseLabel(job)}
        </span>
      </div>
      <div className="mt-3">
        <StatusBadge status={job.status} decision={job.decision} />
      </div>
      <div
        className="mt-3 flex items-center justify-between font-mono text-[0.68rem]"
        style={{ color: "rgba(42,43,46,0.65)" }}
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
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1
            className="font-display text-4xl font-semibold tracking-tight"
            style={{ color: "var(--paper)" }}
          >
            AgentBio
          </h1>
          <p className="mt-2 max-w-xl text-sm" style={{ color: "var(--silver)" }}>
            A case file for every drug-repurposing hypothesis — evidence,
            citations, and limitations, compiled for human review. Each candidate
            requires wet-lab validation; nothing here is a cure.
          </p>
        </div>
        <button
          type="button"
          onClick={onNewCase}
          className="shrink-0 rounded px-5 py-2.5 text-sm font-semibold"
          style={{ backgroundColor: "var(--brass)", color: "var(--paper)" }}
        >
          Open new case
        </button>
      </header>

      <section className="mt-10">
        <div
          className="mb-3 font-mono text-[0.7rem] uppercase tracking-[0.2em]"
          style={{ color: "var(--silver)" }}
        >
          Case files
        </div>

        {runs.length === 0 ? (
          <div
            className="rounded-lg border border-dashed p-10 text-center"
            style={{ borderColor: "var(--silver)" }}
          >
            <p
              className="font-display text-lg"
              style={{ color: "var(--paper)" }}
            >
              No cases yet — open one to start.
            </p>
            <button
              type="button"
              onClick={onNewCase}
              className="mt-4 rounded px-4 py-2 text-sm font-semibold"
              style={{ backgroundColor: "var(--brass)", color: "var(--paper)" }}
            >
              Open new case
            </button>
          </div>
        ) : (
          <div
            className="flex gap-3 overflow-x-auto pb-1"
            style={{
              borderBottom: "2px solid var(--silver)",
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
