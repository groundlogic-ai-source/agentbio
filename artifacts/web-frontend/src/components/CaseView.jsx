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
        backgroundColor: "var(--surface)",
        borderColor: "var(--border)",
        color: "var(--ink-base)",
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
              className="text-xs font-semibold"
              style={{ color: "var(--ink-muted)" }}
            >
              Case dossier
            </div>
            <div
              className="font-mono text-xs"
              style={{ color: "var(--ink-muted)" }}
            >
              #{caseId}
            </div>
          </div>
          <h1
            className="text-3xl font-semibold leading-tight tracking-tight"
            style={{ color: "var(--ink)" }}
          >
            {diseaseLabel(job)}
          </h1>
          <div
            className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs"
            style={{ color: "var(--ink-muted)" }}
          >
            <span>Opened {formatDate(job.created_at)}</span>
            {liveCost != null && (
              <span style={{ color: "var(--brass-deep)" }}>
                Metered structure cost {formatCost(liveCost)} · LLM cost unknown
              </span>
            )}
            {liveCost == null && (
              <span style={{ color: "var(--ink-muted)" }}>
                Run cost unknown
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
          style={{ color: "var(--ink-dim)" }}
        >
          Loading case…
        </p>
      </div>
    );
  }

  const status = job.status;
  const canPrint =
    status === "completed" || status === "awaiting_review" || status === "reviewing";
  const artifacts = job.artifacts || {};

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
          <a
            href={artifacts.report_pdf?.url || `/api/runs/${encodeURIComponent(job.job_id)}/report.pdf`}
            className="btn btn-ghost btn-sm ml-auto"
          >
            Download PDF
          </a>
        )}
        {artifacts.evidence_zip?.url && (
          <a
            href={artifacts.evidence_zip.url}
            className="btn btn-ghost btn-sm"
          >
            Download evidence package
          </a>
        )}
      </div>

      <CaseHeader job={job} cost={cost} />
      {artifacts.cifs?.length > 0 && (
        <Paper className="mb-6">
          <div className="p-4 text-sm">
            <div className="font-semibold" style={{ color: "var(--ink)" }}>
              Durable structure artifacts
            </div>
            <ul className="mt-2 list-disc pl-5">
              {artifacts.cifs.map((artifact) => (
                <li key={artifact.artifact_id}>
                  <a href={artifact.url} className="underline">
                    View/Download CIF: {artifact.filename}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </Paper>
      )}

      {!job.actionable &&
        (status === "awaiting_review" || status === "completed") && (
          <Paper className="mb-6">
            <div
              className="border-l-4 p-5"
              style={{
                borderColor: "var(--oxide)",
                backgroundColor: "var(--oxide-glow)",
              }}
            >
              <div className="text-lg font-semibold" style={{ color: "var(--oxide)" }}>
                Superseded policy snapshot
              </div>
              <p className="mt-1 text-sm font-semibold" style={{ color: "var(--ink)" }}>
                Historical only — cannot be approved.
              </p>
              {job.stale_reasons?.length > 0 && (
                <ul
                  className="mt-3 list-disc pl-5 text-sm"
                  style={{ color: "var(--ink-muted)" }}
                >
                  {job.stale_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
            </div>
          </Paper>
        )}

      {status === "error" && <ErrorPanel message={job.error_message} />}

      {status === "no_eligible_candidate" && (
        <Paper className="mb-6">
          <div className="p-6">
            <div className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
              No eligible repurposing candidate
            </div>
            <p
              className="mt-2 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              {job.error_message ||
                "The pipeline completed but found no compound with an established human safety profile for this target."}
            </p>
            <p
              className="mt-3 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              This is a real result, not a failure: no dossier was written and no
              sign-off is required. Re-run against a different target to continue.
            </p>
          </div>
        </Paper>
      )}

      {status === "source_unavailable" && (
        <Paper className="mb-6">
          <div className="p-6">
            <div className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
              Evidence services unavailable
            </div>
            <p
              className="mt-2 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              {job.error_message ||
                "One or more evidence services failed while this case was running."}
            </p>
            <p
              className="mt-3 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              No scientific conclusion was produced. The pipeline stopped before
              it could evaluate the evidence, so no dossier or sign-off was
              created. Retry this case after the evidence services recover.
            </p>
          </div>
        </Paper>
      )}

      {status === "degraded_unscorable" && (
        <Paper className="mb-6">
          <div className="p-6">
            <div className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
              Literature evidence checks unavailable
            </div>
            <p
              className="mt-2 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              {job.error_message ||
                "All bounded literature checks failed while this case was running."}
            </p>
            <p
              className="mt-3 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              No scientific conclusion was produced. The pipeline could not score
              the evidence, so no dossier or sign-off was created. Retry this case
              after the evidence services recover.
            </p>
          </div>
        </Paper>
      )}

      {(status === "queued" || status === "running" || status === "reviewing") && (
        <Paper>
          <div className="p-6">
            <div className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
              {status === "reviewing"
                ? "Review decision in progress"
                : "Pipeline in progress"}
            </div>
            <p
              className="mb-6 mt-1.5 text-sm leading-relaxed"
              style={{ color: "var(--ink-muted)" }}
            >
              {status === "reviewing"
                ? "Another request has atomically claimed this sign-off. Duplicate actions are disabled while the durable decision is recorded."
                : "Each stage builds the evidence base for a falsifiable hypothesis. This view refreshes automatically every few seconds."}
            </p>
            <Stepper status={status} currentStage={job.current_stage} />
          </div>
        </Paper>
      )}

      {status === "awaiting_review" && (
        <div className="flex flex-col gap-5">
          <Paper>
            <div className="p-6">
              <div className="text-lg font-semibold" style={{ color: "var(--ink)" }}>
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
          <SignOff
            onResume={onResume}
            busy={resuming}
            diseaseName={job.disease_name || ""}
            disabled={!job.actionable}
            disabledReasons={job.stale_reasons || []}
          />
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
                style={{ borderColor: "var(--border-light)" }}
              >
                <div
                  className="text-xs font-semibold mb-2"
                  style={{ color: "var(--ink-muted)" }}
                >
                  Reviewer sign-off note
                </div>
                <p
                  className="text-sm leading-relaxed"
                  style={{ color: "var(--ink-base)", fontStyle: "italic" }}
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
