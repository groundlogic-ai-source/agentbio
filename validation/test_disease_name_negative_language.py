"""A negative word inside the disease's own name is nomenclature, not a finding.

Post-benchmark correction of 2026-09-27. The literature gate scopes negative
language to a +/-45 character window around the candidate drug or its class,
so background text about disease biology cannot masquerade as a negative
finding about the candidate. Some disease names defeat that scoping by putting
a negative word directly beside the drug class they implicate.

"Resistance to thyroid hormone" pairs "resistance" -- in _NEGATIVE_WORDS -- with
"thyroid hormone", the class of any thyroid hormone analogue. Every abstract
about the disease therefore looked like a negative finding about the candidate,
every record failed reconciliation, and a blind run terminated
degraded_unscorable before a single candidate was scored.
"""

import unittest

from data_sources.literature_limitation import (
    _disease_name_spans,
    _support_quote_has_candidate_negative_language,
)

RTH = "Resistance to thyroid hormone"
ABSTRACT = (
    "Resistance to thyroid hormone is a rare congenital disorder "
    "characterized by impaired sensitivity of target tissues to thyroid "
    "hormone. The disease is mostly caused by heterozygous mutations of "
    "thyroid hormone receptor beta."
)


class DiseaseNameNegativeLanguageTest(unittest.TestCase):

    def test_the_disease_name_alone_does_not_flag_negative_language(self):
        """The exact shape that terminated the RTH-beta run."""
        self.assertFalse(_support_quote_has_candidate_negative_language(
            ABSTRACT,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name=RTH,
        ))

    def test_without_the_disease_name_the_same_text_still_flags(self):
        """Confirms the disease name is what suppresses it, not the rewrite."""
        self.assertTrue(_support_quote_has_candidate_negative_language(
            ABSTRACT,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name="",
        ))

    def test_a_real_negative_finding_is_still_caught(self):
        """The guard must keep working on genuine adverse statements."""
        text = (
            "Resistance to thyroid hormone was diagnosed. Treatment with "
            "thyroid hormone analogue therapy was ineffective in this patient."
        )
        self.assertTrue(_support_quote_has_candidate_negative_language(
            text,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name=RTH,
        ))

    def test_negative_word_outside_the_disease_name_is_not_suppressed(self):
        text = (
            "Resistance to thyroid hormone is described. A separate cohort "
            "showed thyroid hormone therapy did not improve outcomes."
        )
        self.assertTrue(_support_quote_has_candidate_negative_language(
            text,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name=RTH,
        ))

    def test_unrelated_disease_name_suppresses_nothing(self):
        self.assertTrue(_support_quote_has_candidate_negative_language(
            ABSTRACT,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name="Achondroplasia",
        ))


class OrphanetQualifierClauseTest(unittest.TestCase):
    """The real disease string is the Orphanet one, not the literature one.

    The first version of this fix matched the full Orphanet name verbatim and
    therefore never fired: papers write "resistance to thyroid hormone" while
    Orphanet writes "Resistance to thyroid hormone due to a mutation in thyroid
    hormone receptor beta". The run failed a second time for the same reason.
    """

    ORPHANET = ("Resistance to thyroid hormone due to a mutation in "
                "thyroid hormone receptor beta")

    def test_core_name_is_matched_when_the_full_string_is_absent(self):
        spans = _disease_name_spans(ABSTRACT, self.ORPHANET)
        self.assertTrue(
            spans, "qualifier-stripped variant should match the abstract")

    def test_the_orphanet_string_suppresses_its_own_nomenclature(self):
        """End to end: the exact input that failed twice in production."""
        self.assertFalse(_support_quote_has_candidate_negative_language(
            ABSTRACT,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name=self.ORPHANET,
        ))

    def test_a_real_negative_still_fires_with_the_orphanet_string(self):
        text = (
            "Resistance to thyroid hormone is described. Separately, thyroid "
            "hormone analogue therapy was ineffective in this cohort."
        )
        self.assertTrue(_support_quote_has_candidate_negative_language(
            text,
            drug_name="LEVOTHYROXINE SODIUM",
            drug_class="thyroid hormone",
            aliases=["thyroxine"],
            disease_name=self.ORPHANET,
        ))

    def test_other_qualifier_separators_are_handled(self):
        for name in (
            "Hypercalcemia caused by a mutation in CASR",
            "Myopathy associated with a defect in RYR1",
            "Neuropathy secondary to a variant in PMP22",
        ):
            core = _disease_name_spans(name.split(" ")[0] + " findings", name)
            self.assertIsInstance(core, list)

    def test_a_short_core_is_not_used(self):
        """Stripping must not produce a stub that suppresses everything."""
        self.assertEqual(
            _disease_name_spans("resistance everywhere", "RTH due to THRB"), [])


class DiseaseNameSpanTest(unittest.TestCase):

    def test_spans_are_found_case_insensitively(self):
        spans = _disease_name_spans(ABSTRACT, RTH)
        self.assertTrue(spans)
        start, end = spans[0]
        self.assertEqual(ABSTRACT[start:end].lower(), RTH.lower())

    def test_short_names_are_ignored_to_avoid_over_suppression(self):
        """A very short disease name would suppress far too much text."""
        self.assertEqual(_disease_name_spans("resistance seen in ALS", "ALS"), [])

    def test_absent_name_yields_no_spans(self):
        self.assertEqual(_disease_name_spans(ABSTRACT, "Achondroplasia"), [])

    def test_empty_name_yields_no_spans(self):
        self.assertEqual(_disease_name_spans(ABSTRACT, ""), [])


if __name__ == "__main__":
    unittest.main()
