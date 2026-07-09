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
      className="rounded-lg border"
      style={{
        borderColor: "rgba(199,202,209,0.45)",
        backgroundColor: "var(--graphite-raised)",
        boxShadow: "0 2px 12px rgba(0,0,0,0.12)",
      }}
    >
      {/* Header strip */}
      <div
        className="px-6 py-3 border-b"
        style={{
          borderColor: "rgba(199,202,209,0.15)",
          borderLeft: "3px solid var(--brass)",
          borderRadius: "8px 8px 0 0",
        }}
      >
        <span
          className="font-mono text-[0.62rem] uppercase tracking-[0.22em]"
          style={{ color: "var(--brass)" }}
        >
          Human Review Checkpoint
        </span>
      </div>

      <div className="px-6 py-5">
        <p
          className="text-base font-semibold leading-snug"
          style={{ color: "var(--paper)" }}
        >
          This is a falsifiable hypothesis, not a finding.
        </p>
        <p
          className="mt-1.5 text-sm leading-relaxed"
          style={{ color: "var(--silver)" }}
        >
          Record your scientific reasoning below, then sign off. Approving
          advances this candidate for wet-lab validation; rejecting closes the
          case permanently.
        </p>

        <label
          htmlFor="signoff-note"
          className="mt-5 mb-1.5 flex items-center gap-2"
        >
          <span
            className="font-mono text-[0.62rem] uppercase tracking-wider"
            style={{ color: "var(--silver)" }}
          >
            Reviewer note
          </span>
          <span
            className="font-mono text-[0.58rem]"
            style={{ color: "var(--oxide)" }}
          >
            required
          </span>
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
          className="w-full resize-y rounded border p-3 text-sm outline-none"
          style={{
            borderColor: note.trim()
              ? "var(--brass)"
              : "rgba(199,202,209,0.3)",
            backgroundColor: "rgba(237,232,223,0.06)",
            color: "var(--silver)",
            transition: "border-color 0.2s ease",
          }}
          onFocus={(e) => {
            if (!note.trim())
              e.currentTarget.style.borderColor = "rgba(192,138,53,0.5)";
          }}
          onBlur={(e) => {
            if (!note.trim())
              e.currentTarget.style.borderColor = "rgba(199,202,209,0.3)";
          }}
        />

        {pending && !note.trim() && (
          <p
            className="mt-2 font-mono text-[0.65rem]"
            style={{ color: "var(--oxide)" }}
          >
            A short note is required before you can {pending} this case.
          </p>
        )}

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => submit("approve")}
            disabled={busy}
            className="rounded px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
            style={{
              backgroundColor: "var(--brass)",
              color: "var(--paper)",
              boxShadow: "0 2px 8px rgba(192,138,53,0.25)",
              transition:
                "background-color 0.15s ease, transform 0.1s ease",
            }}
            onMouseEnter={(e) => {
              if (!busy)
                e.currentTarget.style.backgroundColor = "var(--brass-deep)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = "var(--brass)";
            }}
            onMouseDown={(e) => {
              e.currentTarget.style.transform = "scale(0.97)";
            }}
            onMouseUp={(e) => {
              e.currentTarget.style.transform = "scale(1)";
            }}
          >
            {busy ? "Signing off…" : "Approve case"}
          </button>

          <button
            type="button"
            onClick={() => submit("reject")}
            disabled={busy}
            className="rounded px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
            style={{
              backgroundColor: "transparent",
              color: "var(--oxide)",
              border: "1px solid var(--oxide)",
              transition: "background-color 0.15s ease, transform 0.1s ease",
            }}
            onMouseEnter={(e) => {
              if (!busy)
                e.currentTarget.style.backgroundColor =
                  "rgba(155,74,63,0.12)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = "transparent";
            }}
            onMouseDown={(e) => {
              e.currentTarget.style.transform = "scale(0.97)";
            }}
            onMouseUp={(e) => {
              e.currentTarget.style.transform = "scale(1)";
            }}
          >
            {busy ? "Signing off…" : "Reject case"}
          </button>
        </div>
      </div>
    </div>
  );
}
