import { useCallback, useEffect, useRef, useState } from "react";
import { openCase, getRun, getCost } from "../api.js";
import { STAGES, stepperProgress } from "../lib/stages.js";

const POLL_MS = 4000;

// A case is auditable once the pipeline has finished producing a pool, which
// happens at the awaiting_review checkpoint — the human sign-off that follows
// is not required. This is deliberately broader than lib/stages.js's
// isTerminal(), which answers a different question ("is the job over?").
const AUDITABLE = new Set(["awaiting_review", "completed"]);

function stageLabel(job) {
  if (!job) return "Queued";
  if (job.status === "queued") return "Queued";
  const { activeIndex } = stepperProgress(job.status, job.current_stage);
  if (activeIndex < 0 || activeIndex >= STAGES.length) return "Finishing";
  return STAGES[activeIndex].label;
}

/**
 * Runs a full case for `disease` without leaving the audit surface, then hands
 * the finished job back so the caller can re-run its own query automatically.
 *
 * Deliberately NOT automatic: a case run costs money and takes minutes, and a
 * mistyped disease name resolves to the wrong ontology term. The user confirms
 * once; everything after that is hands-off.
 *
 * Lifecycle notes — this component starts a *metered* backend job, so the
 * bookkeeping is stricter than a typical poller:
 *   - `genRef` is a generation token bumped on unmount and on any disease
 *     change. Every async continuation re-checks it, so an in-flight request
 *     that lands late can never setState or fire onReady for a stale run.
 *   - `startedRef` hard-blocks re-entrant starts; the button being hidden by a
 *     state transition is not sufficient protection against double billing.
 *     It latches unconditionally, including when openCase *appears* to fail:
 *     a transport error after the backend already committed the job is
 *     indistinguishable from one before it, and re-submitting would double-bill
 *     a real run. The panel points at Case Files instead.
 *   - the poll interval is only ever scheduled while the run is genuinely
 *     still in progress, so a job that is already finished on the first tick
 *     never installs a timer. `inFlightRef` drops overlapping ticks when a poll
 *     outlives POLL_MS, and `readyFiredRef` makes onReady strictly once-only.
 */
export default function InlineCaseRunner({ disease, onReady, verb = "Run this case now" }) {
  const [job, setJob] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | starting | running | ready | failed
  const [cost, setCost] = useState(null);
  const [error, setError] = useState(null);

  const timerRef = useRef(null);
  const genRef = useRef(0);
  const startedRef = useRef(false);
  const inFlightRef = useRef(false);
  const readyFiredRef = useRef(false);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Invalidate any in-flight work on unmount.
  useEffect(() => () => {
    genRef.current += 1;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  // A new disease is a different run: invalidate, reset, and re-arm the start.
  useEffect(() => {
    genRef.current += 1;
    startedRef.current = false;
    inFlightRef.current = false;
    readyFiredRef.current = false;
    stopPolling();
    setJob(null);
    setPhase("idle");
    setCost(null);
    setError(null);
  }, [disease, stopPolling]);

  const start = useCallback(async () => {
    if (startedRef.current) return; // never bill the same run twice
    startedRef.current = true;

    const gen = genRef.current;
    const isStale = () => gen !== genRef.current;

    setPhase("starting");
    setError(null);

    let jobId;
    try {
      ({ job_id: jobId } = await openCase(disease));
    } catch (e) {
      if (isStale()) return;
      // startedRef stays latched on purpose. A transport error can arrive after
      // the backend already committed the job, and the client cannot tell the
      // two apart — retrying here would bill a second real run.
      setError(e.message || "Could not start the case");
      setPhase("failed");
      return;
    }
    if (isStale()) return;

    setPhase("running");

    // Returns true while the run should keep being polled. setInterval does not
    // await an async callback, so a poll slower than POLL_MS would otherwise
    // overlap with the next one and both could observe the ready status —
    // firing onReady (and the caller's audit re-run) twice.
    const tick = async () => {
      if (inFlightRef.current) return true; // a poll is already outstanding
      inFlightRef.current = true;
      try {
        let current;
        try {
          current = await getRun(jobId);
        } catch (e) {
          if (isStale()) return false;
          setError(e.message || "Lost contact with the run");
          setPhase("failed");
          return false;
        }
        if (isStale()) return false;

        setJob(current);

        // Cost is informational; a failure here must never end the run.
        getCost(jobId)
          .then((c) => { if (!isStale()) setCost(c.total_cost_usd); })
          .catch(() => {});

        if (AUDITABLE.has(current.status)) {
          setPhase("ready");
          if (!readyFiredRef.current) {
            readyFiredRef.current = true; // strictly once, belt and braces
            onReadyRef.current?.(current);
          }
          return false;
        }
        if (current.status === "error") {
          setError(current.error_message || "The pipeline stopped with an error");
          setPhase("failed");
          return false;
        }
        return true;
      } finally {
        inFlightRef.current = false;
      }
    };

    // Only install a timer if the very first poll shows work still in flight.
    const keepGoing = await tick();
    if (!keepGoing || isStale()) {
      stopPolling();
      return;
    }

    timerRef.current = setInterval(async () => {
      const again = await tick();
      if (!again) stopPolling();
    }, POLL_MS);
  }, [disease, stopPolling]);

  if (!disease?.trim()) return null;

  if (phase === "idle" || phase === "failed") {
    const retry = phase === "failed";
    return (
      <div className="inline-runner">
        {error && <p className="inline-runner-error">{error}</p>}
        {!retry && (
          <button className="btn btn-primary btn-sm" onClick={start}>
            {verb}
          </button>
        )}
        {retry && (
          <p className="inline-runner-note">
            This panel will not resubmit the case. The request may already have reached the
            backend before the error, and starting it again could bill a second run — open
            Case Files to check whether <strong>{disease}</strong> is already running.
          </p>
        )}
        <p className="inline-runner-note">
          Runs the full six-stage pipeline for <strong>{disease}</strong> and returns here
          automatically — you will not have to re-enter anything. Takes a few minutes and
          bills a metered run cost, which is why it is one click rather than automatic.
        </p>
      </div>
    );
  }

  const { completedThrough } = stepperProgress(job?.status, job?.current_stage);
  const done = Math.max(0, completedThrough + 1);

  return (
    <div className="inline-runner">
      <div className="inline-runner-status">
        {phase !== "ready" && <span className="inline-runner-spinner" aria-hidden="true" />}
        {/* Only the phase sentence is announced. The counter and running cost
            change on every poll, which would make a live region intolerably
            chatty for screen-reader users; they stay visible but silent. */}
        <span role="status" aria-live="polite">
          {phase === "ready"
            ? `Case ready for ${disease} — re-running your audit…`
            : `Running ${disease} — ${stageLabel(job)}`}
        </span>
        <span className="inline-runner-count" aria-hidden="true">
          {done}/{STAGES.length}
          {cost != null && ` · $${Number(cost).toFixed(3)}`}
        </span>
      </div>
      <ol className="inline-runner-stages">
        {STAGES.map((s, i) => (
          <li
            key={s.key}
            className={
              i <= completedThrough
                ? "is-done"
                : i === completedThrough + 1 && phase !== "ready"
                  ? "is-active"
                  : ""
            }
          >
            {s.label}
          </li>
        ))}
      </ol>
      <p className="inline-runner-note">
        You can leave this tab open — the audit re-runs on its own when the pipeline
        reaches the review checkpoint.
      </p>
    </div>
  );
}
