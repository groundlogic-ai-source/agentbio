import assert from "node:assert/strict";
import test from "node:test";
import {
  hasTherapeuticApplicabilityDisclosure,
} from "./therapeuticApplicability.js";

test("detects writer-generated therapeutic applicability disclosure", () => {
  assert.equal(
    hasTherapeuticApplicabilityDisclosure(
      "> **Therapeutic applicability not assessed.** Ranking does not assess exposure.",
    ),
    true,
  );
});

test("legacy reports without the disclosure remain detectable", () => {
  assert.equal(
    hasTherapeuticApplicabilityDisclosure("# Historical report\n\nNo applicability section."),
    false,
  );
  assert.equal(hasTherapeuticApplicabilityDisclosure(null), false);
});