import { useEffect, useRef, useState } from "react";

export default function NewCaseDialog({ open, onClose, onOpen, busy }) {
  const [disease, setDisease] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (open && inputRef.current) inputRef.current.focus();
  }, [open]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape" && !busy) onClose();
    }
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  function submit(e) {
    e.preventDefault();
    onOpen(disease.trim());
  }

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
        className="w-full max-w-md rounded-lg border fade-in"
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
          {!busy && (
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

          <div className="mt-6 flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="btn btn-ghost btn-sm"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy}
              className="btn btn-primary btn-sm"
            >
              {busy ? "Opening…" : "Open new case"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
