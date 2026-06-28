import { useState } from "react";

// The human review checkpoint. Either decision requires a short typed note
// before it confirms — this is a deliberate sign-off, not a generic form.
export default function SignOff({ onResume, busy }) {
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(null); // "approve" | "reject" | null
  const canSubmit = note.trim().length > 0 && !busy;

  function submit(action) {
    if (!canSubmit) {
      setPending(action);
      return;
    }
    onResume(action, note.trim());
  }

  return (
    <div
      className="rounded-md border p-5"
      style={{
        borderColor: "var(--silver)",
        backgroundColor: "rgba(199, 202, 209, 0.12)",
      }}
    >
      <div
        className="font-display text-lg font-semibold"
        style={{ color: "var(--ink)" }}
      >
        Human review checkpoint
      </div>
      <p className="mt-1 text-sm" style={{ color: "rgba(42,43,46,0.75)" }}>
        This is a falsifiable hypothesis, not a finding. Record your reasoning,
        then sign off. Approving advances the candidate for wet-lab validation;
        rejecting closes the case.
      </p>

      <label
        htmlFor="signoff-note"
        className="mt-4 block font-mono text-[0.7rem] uppercase tracking-wider"
        style={{ color: "var(--ink)" }}
      >
        Reviewer note (required)
      </label>
      <textarea
        id="signoff-note"
        value={note}
        onChange={(e) => {
          setNote(e.target.value);
          if (e.target.value.trim()) setPending(null);
        }}
        rows={3}
        placeholder="e.g. Affinity and structure confidence justify wet-lab follow-up despite the sub-threshold composite score."
        className="mt-1 w-full resize-y rounded border bg-white/70 p-2.5 text-sm outline-none"
        style={{ borderColor: "var(--silver)", color: "var(--ink)" }}
      />

      {pending && !note.trim() && (
        <p className="mt-2 text-xs" style={{ color: "var(--oxide)" }}>
          A short note is required before you can {pending} this case.
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-3">
        <button
          type="button"
          onClick={() => submit("approve")}
          disabled={busy}
          className="rounded px-4 py-2 text-sm font-semibold transition-opacity disabled:opacity-50"
          style={{ backgroundColor: "var(--brass)", color: "var(--paper)" }}
        >
          {busy ? "Signing off…" : "Approve case"}
        </button>
        <button
          type="button"
          onClick={() => submit("reject")}
          disabled={busy}
          className="rounded px-4 py-2 text-sm font-semibold transition-opacity disabled:opacity-50"
          style={{ backgroundColor: "var(--oxide)", color: "var(--paper)" }}
        >
          {busy ? "Signing off…" : "Reject case"}
        </button>
      </div>
    </div>
  );
}
