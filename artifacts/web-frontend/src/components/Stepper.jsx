import { STAGES, stepperProgress } from "../lib/stages.js";

const STAGE_DESCRIPTIONS = {
  target_selection: "Ranks disease–target pairs by tractability and unmet need",
  biologist: "Gathers mechanistic evidence and literature for the target",
  chemist: "Screens candidate compounds by bioactivity and drug-likeness",
  reviewer: "Scores, safety-screens, and ranks all candidates",
  structure_validation: "Boltz structure prediction and binding-site analysis",
  awaiting_review: "Human review checkpoint — sign off to proceed",
};

function Marker({ state }) {
  if (state === "done") {
    return (
      <span
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-[0.68rem] font-bold"
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
        className="pulse-dot grid h-6 w-6 shrink-0 place-items-center rounded-full"
        style={{
          border: "2px solid var(--brass)",
          backgroundColor: "var(--brass-glow)",
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
        className="grid h-6 w-6 shrink-0 place-items-center rounded-full text-[0.8rem] font-bold"
        style={{ backgroundColor: "var(--oxide)", color: "var(--paper)" }}
        aria-hidden="true"
      >
        !
      </span>
    );
  }
  return (
    <span
      className="grid h-6 w-6 shrink-0 place-items-center rounded-full"
      style={{ border: "2px solid rgba(42,43,46,0.2)" }}
      aria-hidden="true"
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: "rgba(42,43,46,0.2)" }}
      />
    </span>
  );
}

export default function Stepper({ status, currentStage }) {
  const { completedThrough, activeIndex } = stepperProgress(status, currentStage);

  return (
    <ol className="stepper relative flex flex-col">
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
              : "rgba(42,43,46,0.4)";

        const descColor =
          state === "done"
            ? "var(--ink-muted)"
            : state === "active"
              ? "rgba(192,138,53,0.8)"
              : "rgba(42,43,46,0.3)";

        const description = STAGE_DESCRIPTIONS[stage.key];

        return (
          <li key={stage.key} className="flex items-start gap-3.5">
            <div className="flex shrink-0 flex-col items-center">
              <Marker state={state} />
              {!isLast && (
                <span
                  className="w-px flex-1"
                  style={{
                    minHeight: "2rem",
                    marginTop: "2px",
                    marginBottom: "2px",
                    backgroundColor:
                      i < activeIndex
                        ? "var(--brass)"
                        : "rgba(42,43,46,0.15)",
                    opacity: i < activeIndex ? 0.5 : 1,
                    transition: "background-color 0.3s ease",
                  }}
                  aria-hidden="true"
                />
              )}
            </div>
            <div className="pb-4 pt-0.5">
              <div
                className="text-sm font-semibold"
                style={{ color: labelColor, transition: "color 0.2s ease" }}
              >
                {stage.label}
              </div>
              {description && (
                <div
                  className="mt-0.5 text-[0.72rem] leading-snug"
                  style={{
                    color: descColor,
                    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
                    transition: "color 0.2s ease",
                  }}
                >
                  {state === "active" && stage.key !== "awaiting_review"
                    ? "Working…"
                    : state === "active" && stage.key === "awaiting_review"
                      ? "Ready for sign-off"
                      : state === "done"
                        ? "Complete"
                        : description}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
