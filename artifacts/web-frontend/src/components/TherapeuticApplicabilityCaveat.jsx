import {
  THERAPEUTIC_APPLICABILITY_HEADING,
  THERAPEUTIC_APPLICABILITY_TEXT,
} from "../therapeuticApplicability.js";

export default function TherapeuticApplicabilityCaveat({ className = "" }) {
  return (
    <aside
      className={`therapeutic-applicability-caveat ${className}`.trim()}
      role="note"
      aria-label="Therapeutic applicability limitation"
    >
      <strong>{THERAPEUTIC_APPLICABILITY_HEADING}.</strong>{" "}
      {THERAPEUTIC_APPLICABILITY_TEXT}
    </aside>
  );
}