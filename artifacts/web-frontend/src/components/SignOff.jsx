import { useState } from "react";

// The human review checkpoint. Either decision requires a short typed note
// before it confirms — this is a deliberate sign-off, not a generic form.
export default function SignOff({
  onResume,
  busy,
  diseaseName = "",
  disabled = false,
  disabledReasons = [],
}) {
  const timothyScope = /^timothy syndrome$/i.test(diseaseName.trim());
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(null); // "approve" | "reject" | null
  const canSubmit = note.trim().length > 0 && !busy && !disabled;

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
        borderColor: "var(--border)",
        backgroundColor: "var(--surface)",
        boxShadow: "var(--shadow-paper)",
      }}
    >
      {/* Header strip */}
      <div
        className="px-6 py-3 border-b"
        style={{
          borderColor: "var(--border-light)",
          borderLeft: "3px solid var(--brass)",
          borderRadius: "8px 8px 0 0",
        }}
      >
        <span
          className="font-mono text-[0.62rem] uppercase tracking-[0.14em]"
          style={{ color: "var(--brass-deep)" }}
        >
          Human Review Checkpoint
        </span>
      </div>

      <div className="px-6 py-5">
        {disabled && (
          <div
            className="mb-5 rounded border p-4"
            style={{
              borderColor: "var(--oxide)",
              backgroundColor: "var(--oxide-glow)",
              color: "var(--oxide)",
            }}
          >
            <p className="font-semibold">
              Superseded policy snapshot — historical only — cannot be approved.
            </p>
            {disabledReasons.length > 0 && (
              <ul className="mt-2 list-disc pl-5 text-sm">
                {disabledReasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        <p
          className="text-base font-semibold leading-snug"
          style={{ color: "var(--ink)" }}
        >
          This hypothesis is falsifiable by the experiments named below.
        </p>
        {timothyScope && (
          <p
            className="mt-3 rounded border p-3 text-sm leading-relaxed"
            style={{ borderColor: "var(--brass-border)", color: "var(--ink-muted)" }}
          >
            This flagship is scoped to TS1 CACNA1C p.G406R/exon 8A cardiac
            electrophysiology. Approval or rejection is recorded against that
            scope.
          </p>
        )}
        <p
          className="mt-1.5 text-sm leading-relaxed"
          style={{ color: "var(--ink-muted)" }}
        >
          Record your scientific reasoning and your organization&apos;s chosen
          next step. Approval and rejection both record the reviewer&apos;s
          decision against this case; neither is a measurement of the
          candidate.
        </p>

        <label
          htmlFor="signoff-note"
          className="mt-5 mb-1.5 flex items-center gap-2"
        >
          <span
            className="font-mono text-[0.62rem] uppercase tracking-wider"
            style={{ color: "var(--ink-base)" }}
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
          placeholder="State the exact disease subtype, phenotype, evidence limits, and next validation decision."
          className="w-full resize-y rounded border p-3 text-sm outline-none"
          style={{
            borderColor: note.trim()
              ? "var(--brass)"
              : "var(--border)",
            backgroundColor: "var(--paper-warm)",
            color: "var(--ink-base)",
            transition: "border-color 0.2s ease",
          }}
          onFocus={(e) => {
            if (!note.trim())
              e.currentTarget.style.borderColor = "var(--brass-border)";
          }}
          onBlur={(e) => {
            if (!note.trim())
              e.currentTarget.style.borderColor = "var(--border)";
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
            disabled={busy || disabled}
            className="rounded px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
            style={{
              backgroundColor: "var(--brass)",
              color: "var(--paper)",
              boxShadow: "0 2px 8px rgba(176, 122, 40, 0.2)",
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
            disabled={busy || disabled}
            className="rounded px-5 py-2.5 text-sm font-semibold disabled:opacity-50"
            style={{
              backgroundColor: "transparent",
              color: "var(--oxide)",
              border: "1px solid var(--oxide)",
              transition: "background-color 0.15s ease, transform 0.1s ease",
            }}
            onMouseEnter={(e) => {
              if (!busy)
                e.currentTarget.style.backgroundColor = "var(--oxide-glow)";
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
