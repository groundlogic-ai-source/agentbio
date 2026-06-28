import StatusBadge from "./StatusBadge.jsx";
import Stepper from "./Stepper.jsx";
import ReportView from "./ReportView.jsx";
import SignOff from "./SignOff.jsx";
import Stamp from "./Stamp.jsx";
import ErrorPanel from "./ErrorPanel.jsx";
import { diseaseLabel, formatCost, formatDate } from "../lib/stages.js";

function Paper({ children, className = "" }) {
  return (
    <div
      className={`rounded-md border p-6 ${className}`}
      style={{
        backgroundColor: "var(--paper)",
        borderColor: "var(--silver)",
        color: "var(--ink)",
      }}
    >
      {children}
    </div>
  );
}

export default function CaseView({ job, cost, onBack, onResume, resuming }) {
  if (!job) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10">
        <p style={{ color: "var(--silver)" }}>Loading case…</p>
      </div>
    );
  }

  const status = job.status;
  const liveCost = typeof cost === "number" ? cost : job.total_cost_usd;

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <button
        type="button"
        onClick={onBack}
        className="mb-6 font-mono text-xs uppercase tracking-wider"
        style={{ color: "var(--silver)" }}
      >
        ← All case files
      </button>

      <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div
            className="font-mono text-[0.68rem] uppercase tracking-[0.2em]"
            style={{ color: "var(--silver)" }}
          >
            Case dossier
          </div>
          <h1
            className="mt-1 font-display text-3xl font-semibold tracking-tight"
            style={{ color: "var(--paper)" }}
          >
            {diseaseLabel(job)}
          </h1>
          <div
            className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[0.7rem]"
            style={{ color: "var(--silver)" }}
          >
            <span>Opened {formatDate(job.created_at)}</span>
            <span style={{ color: "var(--brass)" }}>
              Run cost {formatCost(liveCost)}
            </span>
          </div>
        </div>
        <StatusBadge status={status} decision={job.decision} />
      </header>

      {status === "error" && <ErrorPanel message={job.error_message} />}

      {(status === "queued" || status === "running") && (
        <Paper>
          <div className="font-display text-lg font-semibold">
            Pipeline in progress
          </div>
          <p
            className="mb-5 mt-1 text-sm"
            style={{ color: "rgba(42,43,46,0.7)" }}
          >
            Each stage builds the evidence base for a falsifiable hypothesis.
            This view refreshes automatically.
          </p>
          <Stepper status={status} currentStage={job.current_stage} />
        </Paper>
      )}

      {status === "awaiting_review" && (
        <div className="flex flex-col gap-6">
          <Paper>
            <div className="font-display text-lg font-semibold">
              Pipeline complete — awaiting your sign-off
            </div>
            <div className="mt-4">
              <Stepper status={status} currentStage={job.current_stage} />
            </div>
          </Paper>
          <Paper>
            <ReportView report={job.report} />
          </Paper>
          <SignOff onResume={onResume} busy={resuming} />
        </div>
      )}

      {status === "completed" && (
        <Paper className="relative">
          <div className="pointer-events-none absolute right-5 top-5">
            <Stamp decision={job.decision} />
          </div>
          <ReportView report={job.report} />
          {job.review_notes ? (
            <div
              className="mt-6 border-t pt-4"
              style={{ borderColor: "rgba(42,43,46,0.2)" }}
            >
              <div
                className="font-mono text-[0.68rem] uppercase tracking-wider"
                style={{ color: "var(--ink)" }}
              >
                Reviewer sign-off note
              </div>
              <p className="mt-1 text-sm" style={{ color: "var(--ink)" }}>
                {job.review_notes}
              </p>
            </div>
          ) : null}
        </Paper>
      )}
    </div>
  );
}
