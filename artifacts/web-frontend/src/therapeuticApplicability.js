export const THERAPEUTIC_APPLICABILITY_HEADING =
  "Therapeutic applicability not assessed";

export const THERAPEUTIC_APPLICABILITY_TEXT =
  "Ranking does not assess whether a drug reaches the relevant tissue, cell, or compartment at an effective, tolerable human exposure. Route, dose, pharmacokinetics (PK), disease stage/subtype, and therapeutic window require expert review. Unknown must not be interpreted as compatible.";

export function hasTherapeuticApplicabilityDisclosure(report) {
  return typeof report === "string"
    && report.toLowerCase().includes(
      THERAPEUTIC_APPLICABILITY_HEADING.toLowerCase(),
    );
}