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
      style={{ backgroundColor: "rgba(28,29,33,0.7)" }}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <form
        onSubmit={submit}
        className="w-full max-w-md rounded-md border p-6"
        style={{
          backgroundColor: "var(--paper)",
          borderColor: "var(--silver)",
          color: "var(--ink)",
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="newcase-title"
      >
        <h2
          id="newcase-title"
          className="font-display text-xl font-semibold"
          style={{ color: "var(--ink)" }}
        >
          Open a new case
        </h2>
        <p className="mt-1 text-sm" style={{ color: "rgba(42,43,46,0.75)" }}>
          Name a disease to investigate, or leave it blank to let the pipeline
          auto-pick the top-ranked candidate from the universe.
        </p>

        <label
          htmlFor="disease"
          className="mt-4 block font-mono text-[0.7rem] uppercase tracking-wider"
        >
          Disease (optional)
        </label>
        <input
          id="disease"
          ref={inputRef}
          type="text"
          value={disease}
          onChange={(e) => setDisease(e.target.value)}
          placeholder="e.g. Pompe disease — or leave blank"
          className="mt-1 w-full rounded border bg-white/70 p-2.5 font-mono text-sm outline-none"
          style={{ borderColor: "var(--silver)", color: "var(--ink)" }}
        />

        <div className="mt-6 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded border px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ borderColor: "var(--silver)", color: "var(--ink)" }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded px-4 py-2 text-sm font-semibold disabled:opacity-50"
            style={{ backgroundColor: "var(--brass)", color: "var(--paper)" }}
          >
            {busy ? "Opening…" : "Open new case"}
          </button>
        </div>
      </form>
    </div>
  );
}
