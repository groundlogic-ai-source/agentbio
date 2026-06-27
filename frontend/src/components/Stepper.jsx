import { STAGES, stepperProgress } from "../lib/stages.js";

function Marker({ state }) {
  // done | active | pending | failed
  if (state === "done") {
    return (
      <span
        className="grid h-6 w-6 place-items-center rounded-full text-[0.7rem] font-bold"
        style={{ backgroundColor: "var(--brass)", color: "var(--paper)" }}
        aria-hidden="true"
      >
        ✓
      </span>
    );
  }
  if (state === "active") {
    return (
      <span
        className="pulse-dot grid h-6 w-6 place-items-center rounded-full"
        style={{
          border: "2px solid var(--brass)",
          backgroundColor: "rgba(192, 138, 53, 0.18)",
        }}
        aria-hidden="true"
      >
        <span
          className="h-2 w-2 rounded-full"
          style={{ backgroundColor: "var(--brass)" }}
        />
      </span>
    );
  }
  if (state === "failed") {
    return (
      <span
        className="grid h-6 w-6 place-items-center rounded-full text-[0.8rem] font-bold"
        style={{ backgroundColor: "var(--oxide)", color: "var(--paper)" }}
        aria-hidden="true"
      >
        !
      </span>
    );
  }
  return (
    <span
      className="grid h-6 w-6 place-items-center rounded-full"
      style={{ border: "2px solid rgba(42,43,46,0.25)" }}
      aria-hidden="true"
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: "rgba(42,43,46,0.25)" }}
      />
    </span>
  );
}

export default function Stepper({ status, currentStage }) {
  const { completedThrough, activeIndex } = stepperProgress(status, currentStage);

  return (
    <ol className="relative flex flex-col gap-0">
      {STAGES.map((stage, i) => {
        let state = "pending";
        if (i <= completedThrough) state = "done";
        else if (i === activeIndex) state = "active";

        const isLast = i === STAGES.length - 1;
        const labelColor =
          state === "active"
            ? "var(--brass)"
            : state === "done"
              ? "var(--ink)"
              : "rgba(42,43,46,0.5)";

        let note = null;
        if (state === "active") {
          note = stage.key === "awaiting_review" ? "Ready for sign-off" : "Working…";
        } else if (state === "done") {
          note = "Complete";
        }

        return (
          <li key={stage.key} className="flex items-start gap-3">
            <div className="flex flex-col items-center">
              <Marker state={state} />
              {!isLast && (
                <span
                  className="my-0.5 w-px flex-1"
                  style={{
                    minHeight: "1.4rem",
                    backgroundColor:
                      i < active ? "var(--brass)" : "rgba(42,43,46,0.2)",
                  }}
                  aria-hidden="true"
                />
              )}
            </div>
            <div className="pb-3 pt-0.5">
              <div
                className="text-sm font-semibold"
                style={{ color: labelColor }}
              >
                {stage.label}
              </div>
              {note && (
                <div
                  className="font-mono text-[0.68rem] uppercase tracking-wider"
                  style={{ color: labelColor, opacity: 0.8 }}
                >
                  {note}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
