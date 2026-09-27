export const THERAPEUTIC_APPLICABILITY_HEADING =
  "Therapeutic applicability not assessed";

// Kept in step with the same disclosure in agents/writer.py: it states what
// the pipeline does and does not measure, and stops there.
export const THERAPEUTIC_APPLICABILITY_TEXT =
  "Ranking does not assess whether a drug reaches the relevant tissue, cell, or compartment at an effective, tolerable human exposure. Route, dose, pharmacokinetics (PK), disease stage/subtype and therapeutic window are outside what this pipeline measures.";

export function hasTherapeuticApplicabilityDisclosure(report) {
  return typeof report === "string"
    && report.toLowerCase().includes(
      THERAPEUTIC_APPLICABILITY_HEADING.toLowerCase(),
    );
}