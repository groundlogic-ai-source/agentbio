import { useModalityMode } from "../modalityMode";

// Toggle for the disengageable modality base-rate mode. Disclosure-only:
// switching it never changes scores, ranks, caps, or persisted data —
// only whether the non-oral-biologic caution is shown.
export default function ModalityModeToggle() {
  const [engaged, setEngaged] = useModalityMode();
  return (
    <label
      style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", cursor: "pointer" }}
      title="Registry-confirmed finding run-704c0cb4-H05: non-oral biologics have ~0.30x odds of repurposing success. Show or hide this base-rate caution. Disclosure only — never changes scores or ranks."
    >
      <input
        type="checkbox"
        checked={engaged}
        onChange={(e) => setEngaged(e.target.checked)}
      />
      <span
        style={{
          fontFamily: "monospace", fontSize: "0.61rem", textTransform: "uppercase",
          letterSpacing: "0.11em", color: "var(--ink-dim)",
        }}
      >
        Modality base-rate mode
      </span>
    </label>
  );
}
