"""Two long-standing honesty defects in what the dossier says and ranks.

1. BLACK-BOX WARNINGS FED NOTHING
   CLAUDE.md carried this as "one known live bug remains": a boxed warning was
   recorded on the candidate and then consumed by nothing -- not the composite,
   not a cap, not a gate -- so a drug carrying one ranked exactly as if it did
   not. The documented condition is about being strictly dominated: carrying a
   boxed warning while the SAME target already has an approved drug in the pool
   WITHOUT one. Implemented as an eligibility signal rather than a score
   change, so the composite stays an evidence summary and stays comparable,
   and nothing here judges how severe a given warning is.

2. THE DOSSIER MISDESCRIBED ITS OWN METHOD
   The applicability disclosure stated flatly that "Ranking does not assess
   whether this drug reaches the relevant tissue", and the code comment beside
   it claimed it "intentionally adds no tissue-specific score, penalty, cap, or
   gate". Both stopped being true when the compartment-exposure gate shipped: a
   documented failure to reach the compartment now excludes the candidate. A
   dossier that understates its own method is as wrong as one that overstates
   it, so the disclosure now names what was actually checked while keeping
   every clause that genuinely remains unassessed -- dose, route, PK and
   therapeutic window.
"""

import unittest

from agents import writer


class BlackBoxDominationTest(unittest.TestCase):

    def test_gate_is_wired_into_exclusion_reasons(self):
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertIn(
            'reasons.append("black_box_with_safer_same_target_alternative")',
            source)

    def test_the_safer_alternative_set_requires_approval_and_no_warning(self):
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        block = source.split("_safer_same_target_alternative: set[str] = {")[1]
        block = block.split("}")[0]
        self.assertIn('row.get("is_approved_drug") is True', block)
        self.assertIn('not row.get("black_box_advisory")', block)

    def test_it_is_an_eligibility_signal_not_a_score_change(self):
        """The composite must stay an evidence summary, and stay comparable."""
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertNotIn("black_box_advisory\") else composite", source)
        self.assertNotIn("composite *= ", source)


class CompartmentDisclosureTest(unittest.TestCase):

    def test_unassessed_candidate_adds_no_claim(self):
        """Most rows never reach the gate; silence is correct for them."""
        self.assertEqual(writer._compartment_exposure_disclosure({}), "")
        self.assertEqual(
            writer._compartment_exposure_disclosure(
                {"tissue_exposure": {"verdict": "NOT_ASSESSED"}}), "")

    def test_documented_failure_is_stated_plainly(self):
        text = writer._compartment_exposure_disclosure({
            "tissue_exposure": {
                "verdict": "COMPARTMENT_EXPOSURE_UNLIKELY",
                "compartment": "central nervous system",
                "reason": "Documented P-gp substrate.",
            }})
        self.assertIn("does not reach", text)
        self.assertIn("central nervous system", text)
        self.assertIn("excluded", text)

    def test_clearing_the_gate_is_not_claimed_as_adequate_exposure(self):
        """The error this project keeps removing: a bounded negative read as a
        positive claim."""
        text = writer._compartment_exposure_disclosure({
            "tissue_exposure": {
                "verdict": "COMPARTMENT_EXPOSURE_PLAUSIBLE",
                "compartment": "central nervous system",
            }})
        self.assertIn("not evidence of adequate exposure", text)
        for term in ("dose", "route", "PK", "therapeutic window"):
            self.assertIn(term, text)

    def test_unknown_is_stated_as_unknown(self):
        text = writer._compartment_exposure_disclosure({
            "tissue_exposure": {
                "verdict": "INSUFFICIENT_INFO",
                "compartment": "central nervous system",
            }})
        self.assertIn("unknown", text.lower())
        self.assertIn("not a finding", text.lower())

    def test_non_cns_disease_asserts_no_requirement(self):
        text = writer._compartment_exposure_disclosure({
            "tissue_exposure": {
                "verdict": "NO_COMPARTMENT_REQUIREMENT_IDENTIFIED",
            }})
        self.assertIn("not assessed", text.lower())

    def test_the_blanket_disclosure_still_disclaims_what_is_unassessed(self):
        """Narrowing one clause must not quietly retire the others."""
        import inspect
        source = inspect.getsource(writer)
        self.assertIn("Therapeutic applicability not assessed", source)
        self.assertIn("disease stage/subtype and therapeutic window", source)

    def test_the_stale_comment_claiming_no_gate_is_gone(self):
        import inspect
        source = inspect.getsource(writer)
        self.assertNotIn(
            "intentionally adds no tissue-specific score, penalty, cap, or gate",
            source)


if __name__ == "__main__":
    unittest.main()
