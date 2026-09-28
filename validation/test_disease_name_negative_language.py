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
