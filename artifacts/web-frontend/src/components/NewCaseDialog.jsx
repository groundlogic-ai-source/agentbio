import { useEffect, useRef, useState } from "react";

export default function NewCaseDialog({
  open,
  onClose,
  onOpen,
  onPreflight,
  busy,
}) {
  const [disease, setDisease] = useState("");
  const [useCase, setUseCase] = useState({
    subgroup: "",
    stage: "",
    treatment_setting: "",
    proposed_advantage: "",
  });
  const [hypothesisOnly, setHypothesisOnly] = useState(false);
  const [preflight, setPreflight] = useState(null);
  const [checking, setChecking] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setPreflight(null);
      setChecking(false);
      setUseCase({
        subgroup: "",
        stage: "",
        treatment_setting: "",
        proposed_advantage: "",
      });
      setHypothesisOnly(false);
      if (inputRef.current) inputRef.current.focus();
    }
  }, [open]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape" && !busy && !checking) onClose();
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, checking, onClose]);

  if (!open) return null;

  async function submit(e) {
    e.preventDefault();
    const value = disease.trim();
    const framing = Object.fromEntries(
      Object.entries(useCase)
        .map(([key, fieldValue]) => [key, fieldValue.trim()])
        .filter(([, fieldValue]) => fieldValue),
    );
    if (!value || !onPreflight) {
      onOpen(value, value ? framing : null, hypothesisOnly);
      return;
    }
    setChecking(true);
    try {
      setPreflight(await onPreflight(value, framing));
    } catch (error) {
      setPreflight({
        error: error?.message || "Flagship preflight could not be completed.",
      });
    } finally {
      setChecking(false);
    }
  }

  const verdict = preflight?.verdict;
  const verdictLabel = verdict
    ? verdict.replaceAll("_", " ")
    : "";
  const verdictTone =
    verdict === "FLAGSHIP_READY"
      ? "var(--success)"
      : verdict === "NOT_FLAGSHIP_READY"
        ? "var(--oxide)"
        : "var(--brass-deep)";

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center p-4"
      style={{ backgroundColor: "rgba(0,0,0,0.4)", backdropFilter: "blur(3px)" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="w-full max-w-lg rounded-lg border fade-in"
        style={{
          backgroundColor: "var(--surface)",
          borderColor: "var(--border)",
          color: "var(--ink-base)",
          boxShadow: "var(--shadow-paper)",
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="newcase-title"
      >
        {/* Modal header strip */}
        <div
          className="flex items-center justify-between border-b px-6 py-4"
          style={{ borderColor: "var(--border-light)" }}
        >
          <div className="flex items-center gap-3">
            <span
              className="font-mono text-[0.6rem] uppercase tracking-[0.14em]"
              style={{ color: "var(--brass-deep)" }}
            >
              New case
            </span>
          </div>
          {!busy && !checking && (
            <button
              type="button"
              onClick={onClose}
              className="font-mono text-[0.68rem]"
              style={{ color: "var(--ink-muted)" }}
              aria-label="Close dialog"
            >
              ESC
            </button>
          )}
        </div>

        <div className="px-6 py-5">
          <h2
            id="newcase-title"
            className="text-xl font-semibold leading-snug"
            style={{ color: "var(--ink)" }}
          >
            Open a new case
          </h2>
          <p
            className="mt-2 text-sm leading-relaxed"
            style={{ color: "var(--ink-muted)" }}
          >
            Name a rare or neglected-tropical disease to investigate it directly —
            its targets are scored with the same formulas used by the full ranking.
            Diseases outside that scope are rejected rather than substituted. Leave
            the field blank to explore the ranked list automatically.
          </p>

          <label
            htmlFor="disease"
            className="mt-5 block font-mono text-[0.62rem] uppercase tracking-wider mb-1.5"
            style={{ color: "var(--ink-base)" }}
          >
            Disease — one name, or leave blank to explore
          </label>
          <input
            id="disease"
            ref={inputRef}
            type="text"
            value={disease}
            onChange={(e) => setDisease(e.target.value)}
            placeholder="e.g. Pompe disease — or leave blank to explore"
            className="w-full rounded border p-2.5 font-mono text-sm outline-none"
            style={{
              borderColor: "var(--border)",
              backgroundColor: "var(--paper-warm)",
              color: "var(--ink-base)",
              transition: "border-color 0.2s ease",
            }}
            onFocus={(e) => {
              e.currentTarget.style.borderColor = "var(--brass)";
            }}
            onBlur={(e) => {
              e.currentTarget.style.borderColor = "var(--border)";
            }}
          />

          <div className="mt-5 border-t pt-4" style={{ borderColor: "var(--border-light)" }}>
            <div
              className="font-mono text-[0.62rem] uppercase tracking-wider"
              style={{ color: "var(--ink-base)" }}
            >
              Optional flagship hypothesis framing
            </div>
            <p className="mt-1 text-xs leading-relaxed" style={{ color: "var(--ink-muted)" }}>
              Give experts a concrete population, setting, and testable
              differentiator. These are hypothesis inputs, not efficacy evidence.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {[
                ["subgroup", "Subgroup", "e.g. TS1 p.G406R / exon 8A"],
                ["stage", "Stage or disease state", "e.g. relapsed or refractory"],
                ["treatment_setting", "Treatment setting", "e.g. genotype-matched cardiac care"],
              ].map(([key, label, placeholder]) => (
                <label key={key} className="block">
                  <span
                    className="mb-1 block font-mono text-[0.58rem] uppercase tracking-wider"
                    style={{ color: "var(--ink-muted)" }}
                  >
                    {label}
                  </span>
                  <input
                    type="text"
                    value={useCase[key]}
                    onChange={(e) => {
                      setUseCase((current) => ({
                        ...current,
                        [key]: e.target.value,
                      }));
                      setPreflight(null);
                    }}
                    placeholder={placeholder}
                    className="w-full rounded border p-2.5 text-sm outline-none"
                    style={{
                      borderColor: "var(--border)",
                      backgroundColor: "var(--paper-warm)",
                      color: "var(--ink-base)",
                    }}
                  />
                </label>
              ))}
            </div>
            <label className="mt-3 block">
              <span
                className="mb-1 block font-mono text-[0.58rem] uppercase tracking-wider"
                style={{ color: "var(--ink-muted)" }}
              >
                Proposed advantage / differentiator
              </span>
              <textarea
                value={useCase.proposed_advantage}
                onChange={(e) => {
                  setUseCase((current) => ({
                    ...current,
                    proposed_advantage: e.target.value,
                  }));
                  setPreflight(null);
                }}
                placeholder="e.g. a cardiac benefit without requiring CNS exposure"
                rows={2}
                className="w-full resize-y rounded border p-2.5 text-sm outline-none"
                style={{
                  borderColor: "var(--border)",
                  backgroundColor: "var(--paper-warm)",
                  color: "var(--ink-base)",
                }}
              />
            </label>
          </div>

          <label
            className="mt-5 flex cursor-pointer items-start gap-2.5 rounded-md border px-3.5 py-3"
            style={{
              borderColor: hypothesisOnly
                ? "var(--brass-border)"
                : "var(--border-light)",
              backgroundColor: hypothesisOnly
                ? "var(--brass-glow)"
                : "transparent",
            }}
          >
            <input
              type="checkbox"
              checked={hypothesisOnly}
              onChange={(e) => setHypothesisOnly(e.target.checked)}
              className="mt-0.5"
            />
            <span>
              <span
                className="block text-sm font-medium"
                style={{ color: "var(--ink-base)" }}
              >
                Run with ChEMBL disabled
              </span>
              <span
                className="mt-1 block text-xs leading-relaxed"
                style={{ color: "var(--ink-muted)" }}
              >
                Build a clearly marked hypothesis from GtoPdb, DrugCentral,
                BindingDB, and literature. Missing ChEMBL affinity and safety
                coverage will remain an explicit limitation.
              </span>
            </span>
          </label>

          {preflight && (
            <div
              className="mt-5 rounded-md border px-3.5 py-3"
              style={{
                borderColor: preflight.error
                  ? "var(--oxide-border)"
                  : "var(--brass-border)",
                backgroundColor: preflight.error
                  ? "var(--oxide-glow)"
                  : "var(--brass-glow)",
              }}
              aria-live="polite"
            >
              {preflight.error ? (
                <p className="text-sm" style={{ color: "var(--oxide)" }}>
                  {preflight.error}
                </p>
              ) : (
                <>
                  <div
                    className="font-mono text-[0.62rem] uppercase tracking-[0.12em]"
                    style={{ color: verdictTone }}
                  >
                    Flagship hypothesis screen · {verdictLabel}
                  </div>
                  <p
                    className="mt-2 text-sm leading-relaxed"
                    style={{ color: "var(--ink-base)" }}
                  >
                    {preflight.next_action}
                  </p>
                  {(preflight.reasons || []).length > 0 && (
                    <ul
                      className="mt-2 list-disc space-y-1 pl-4 text-xs leading-relaxed"
                      style={{ color: "var(--ink-muted)" }}
                    >
                      {preflight.reasons.slice(0, 4).map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}
                  {(preflight.missing_evidence || []).length > 0 && (
                    <p
                      className="mt-2 font-mono text-[0.62rem] leading-relaxed"
                      style={{ color: "var(--ink-muted)" }}
                    >
                      Missing: {preflight.missing_evidence.slice(0, 5).join("; ")}
                    </p>
                  )}
                  {preflight.flagship_use_case_claims && (
                    <p
                      className="mt-2 text-xs leading-relaxed"
                      style={{ color: "var(--ink-muted)" }}
                    >
                       The framing you supplied is persisted with this verdict
                       and is reproduced in the dossier.
                    </p>
                  )}
                  <p
                    className="mt-3 text-xs leading-relaxed"
                    style={{ color: "var(--ink-muted)" }}
                  >
                    This gate evaluates computational hypothesis readiness, not
                    efficacy, exposure, safety, or clinical benefit. The full
                    case can still be run.
                  </p>
                </>
              )}
            </div>
          )}

          <div className="mt-6 flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={busy || checking}
              className="btn btn-ghost btn-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || checking}
              className="btn btn-primary btn-sm"
            >
              {busy
                ? "Opening…"
                : checking
                  ? "Checking…"
                  : preflight
                    ? "Check again"
                    : disease.trim()
                      ? "Check flagship fit"
                      : "Open research case"}
            </button>
            {preflight && !preflight.error && (
              <button
                type="button"
                onClick={() => {
                  const framing = Object.fromEntries(
                    Object.entries(useCase)
                      .map(([key, fieldValue]) => [key, fieldValue.trim()])
                      .filter(([, fieldValue]) => fieldValue),
                  );
                  onOpen(disease.trim(), framing, hypothesisOnly);
                }}
                disabled={busy || checking}
                className="btn btn-primary btn-sm"
              >
                Run research case
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
