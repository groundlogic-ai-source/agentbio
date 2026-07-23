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
      className={`rounded-lg border ${className}`}
      style={{
        backgroundColor: "var(--paper)",
        borderColor: "rgba(199,202,209,0.6)",
        color: "var(--ink)",
        boxShadow: "var(--shadow-paper)",
      }}
    >
      {children}
    </div>
  );
}

function CaseHeader({ job, cost }) {
  const caseId = (job.job_id || "").slice(-8).toUpperCase();
  const liveCost = typeof cost === "number" ? cost : job.total_cost_usd;

  return (
    <header className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3 mb-1">
            <div
              className="font-mono text-[0.58rem] uppercase tracking-[0.22em]"
              style={{ color: "var(--brass)" }}
            >
              Case dossier
            </div>
            <div
              className="font-mono text-[0.58rem] tracking-wider"
              style={{ color: "var(--silver-dim)" }}
            >
              #{caseId}
            </div>
          </div>
          <h1
            className="text-3xl font-semibold leading-tight tracking-tight"
            style={{ color: "var(--paper)" }}
          >
            {diseaseLabel(job)}
          </h1>
          <div
            className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[0.65rem]"
            style={{ color: "var(--silver)" }}
          >
            <span>Opened {formatDate(job.created_at)}</span>
            {liveCost != null && (
              <span style={{ color: "var(--brass)" }}>
                Run cost {formatCost(liveCost)}
              </span>
            )}
          </div>
        </div>
        <div className="shrink-0 pt-1">
          <StatusBadge status={job.status} decision={job.decision} />
        </div>
      </div>
    </header>
  );
}

export default function CaseView({ job, cost, onBack, onResume, resuming }) {
  if (!job) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-10">
        <p
          className="font-mono text-sm"
          style={{ color: "var(--silver-dim)" }}
        >
          Loading case…
        </p>
      </div>
    );
  }

  const status = job.status;
  const canPrint = status === "completed" || status === "awaiting_review";

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 fade-in">
      {/* Nav bar */}
      <div className="no-print mb-6 flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="btn btn-ghost btn-sm"
        >
          ← All case files
        </button>
        {canPrint && (
          <button
            type="button"
            onClick={() => window.print()}
            className="btn btn-ghost btn-sm ml-auto"
          >
            Download PDF
          </button>
        )}
      </div>

      <CaseHeader job={job} cost={cost} />

      {status === "error" && <ErrorPanel message={job.error_message} />}

      {(status === "queued" || status === "running") && (
        <Paper>
          <div className="p-6">
            <div className="text-lg font-semibold">
              Pipeline in progress
            </div>
            <p
              className="mb-6 mt-1.5 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              Each stage builds the evidence base for a falsifiable hypothesis.
              This view refreshes automatically every few seconds.
            </p>
            <Stepper status={status} currentStage={job.current_stage} />
          </div>
        </Paper>
      )}

      {status === "awaiting_review" && (
        <div className="flex flex-col gap-5">
          <Paper>
            <div className="p-6">
              <div className="text-lg font-semibold">
                Pipeline complete — awaiting your sign-off
              </div>
              <div className="mt-5">
                <Stepper status={status} currentStage={job.current_stage} />
              </div>
            </div>
          </Paper>
          <Paper>
            <div className="p-6 sm:p-8">
              <ReportView report={job.report} />
            </div>
          </Paper>
          <SignOff onResume={onResume} busy={resuming} />
        </div>
      )}

      {status === "completed" && (
        <Paper className="relative overflow-hidden">
          <div className="pointer-events-none absolute right-6 top-6 z-10">
            <Stamp decision={job.decision} />
          </div>
          <div className="p-6 sm:p-8">
            <ReportView report={job.report} />
            {job.review_notes && (
              <div
                className="mt-8 border-t pt-5"
                style={{ borderColor: "rgba(42,43,46,0.15)" }}
              >
                <div
                  className="font-mono text-[0.62rem] uppercase tracking-[0.2em] mb-2"
                  style={{ color: "var(--ink-muted)" }}
                >
                  Reviewer sign-off note
                </div>
                <p
                  className="text-sm leading-relaxed"
                  style={{ color: "var(--ink)", fontStyle: "italic" }}
                >
                  "{job.review_notes}"
                </p>
              </div>
            )}
          </div>
        </Paper>
      )}
    </div>
  );
}
