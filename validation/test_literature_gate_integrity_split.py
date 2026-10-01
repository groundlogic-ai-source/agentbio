"""A gap in our vocabulary is not a failure of the classifier.

Post-benchmark correction of 2026-09-29. The literature gate folded two
different questions into one `valid` boolean, and every way of failing it
incremented `classifier_integrity_failures` -- the counter that escalates to
SEARCH_FAILED and blocks a candidate from promotion.

A Niemann-Pick type C run died there. GPT-5.4 correctly labelled three records
APPLICABLE_SUPPORT. They phrase clinical benefit the way clinicians do:

    "Miglustat was initiated, leading to clinical stabilization."
    "only 2 therapies are now established treatments ... miglustat for
     Niemann-Pick disease type C"
    "including the use of miglustat, the only specific drug approved at the time"

`_SUPPORT_WORDS` matches seven stems -- benefit, effective, efficacy, improve,
recommend, respond, successful -- and none of those quotes contains one. The
deterministic corroboration step therefore could not confirm the label, and the
code reported that as the model returning unusable output. Three such records
produced SEARCH_FAILED, the gate refused to clear, ELIGLUSTAT (a UGCG inhibitor
at composite 0.8315, the strongest candidate in a 397-row pool) was excluded on
`literature_gate_not_cleared`, and the run terminated no_eligible_candidate.

The distinction now enforced:

    integrity failure   the classifier's OUTPUT is unusable -- unknown label,
                        unverifiable citation, a negative finding hidden behind
                        an IRRELEVANT label. Only this may fail the search.

    not corroborated    OUR checks could not confirm the label. The record is
                        dropped from evidence, which is conservative, and the
                        search continues.
"""

import unittest

from data_sources.literature_limitation import (
    APPLICABLE_SUPPORT,
    EXPLICIT_LIMITATION,
    NOT_APPLICABLE_TO_EXACT_DRUG_USE,
    UNKNOWN_INTEGRITY_FAILED,
    _SUPPORT_WORDS,
    classify_record_validity,
)

# Verbatim from the failing Niemann-Pick type C run.
NPC_QUOTES = [
    "only 2 therapies are now established treatments, namely, dietary "
    "restriction for phenylketonuria and miglustat for Niemann-Pick disease "
    "type C.",
    "including the use of miglustat, the only specific drug approved at the time",
    "Miglustat was initiated, leading to clinical stabilization.",
]


def _call(**overrides):
    """A well-formed, corroborated APPLICABLE_SUPPORT record by default."""
    kwargs = dict(
        valid_label=True,
        valid_citation=True,
        label=APPLICABLE_SUPPORT,
        requested_label=APPLICABLE_SUPPORT,
        recovered_from_unknown=False,
        exact_match=True,
        valid_quote=True,
        explicit_language=False,
        supportive_language=True,
        source_has_negative_language=False,
    )
    kwargs.update(overrides)
    return classify_record_validity(**kwargs)


class SupportVocabularyGapTest(unittest.TestCase):
    """The specific gap that terminated the run."""

    def test_the_real_quotes_contain_no_support_word(self):
        """Documents the gap rather than assuming it."""
        for quote in NPC_QUOTES:
            self.assertIsNone(
                _SUPPORT_WORDS.search(quote),
                f"vocabulary unexpectedly matched: {quote[:60]}")

    def test_a_vocabulary_gap_is_not_an_integrity_failure(self):
        """The regression. This is what killed the run."""
        integrity_ok, corroborated = _call(supportive_language=False)
        self.assertTrue(
            integrity_ok,
            "an unmatched phrasing must not be reported as the classifier "
            "returning unusable output")
        self.assertFalse(
            corroborated,
            "the record still must not be counted as support")

    def test_the_record_is_dropped_not_counted(self):
        """Conservative: we decline to credit what we cannot confirm."""
        _, corroborated = _call(supportive_language=False)
        self.assertFalse(corroborated)


class GenuineIntegrityFailureTest(unittest.TestCase):
    """These must still fail the search -- the gate stays fail-closed."""

    def test_unknown_label_is_an_integrity_failure(self):
        integrity_ok, _ = _call(label=UNKNOWN_INTEGRITY_FAILED)
        self.assertFalse(integrity_ok)

    def test_invalid_label_is_an_integrity_failure(self):
        integrity_ok, _ = _call(valid_label=False)
        self.assertFalse(integrity_ok)

    def test_unverifiable_citation_is_an_integrity_failure(self):
        integrity_ok, _ = _call(valid_citation=False)
        self.assertFalse(integrity_ok)

    def test_negative_finding_hidden_behind_irrelevant_is_an_integrity_failure(self):
        """The model must not bury a negative result under IRRELEVANT."""
        integrity_ok, _ = _call(
            label=NOT_APPLICABLE_TO_EXACT_DRUG_USE,
            requested_label=NOT_APPLICABLE_TO_EXACT_DRUG_USE,
            source_has_negative_language=True,
        )
        self.assertFalse(integrity_ok)


class CorroborationTest(unittest.TestCase):
    """Corroboration failures are local: not counted, not fatal."""

    def test_unverifiable_quote_does_not_fail_the_search(self):
        """An ellipsis-joined quote is unconfirmable, not evidence of fraud.

        A real limitation record in an RTH-beta run was discarded this way:
        the quote joined two non-contiguous sentences, so the verbatim check
        failed and the whole search was declared broken.
        """
        integrity_ok, corroborated = _call(valid_quote=False)
        self.assertTrue(integrity_ok)
        self.assertFalse(corroborated)

    def test_wrong_drug_is_not_corroborated_and_not_fatal(self):
        integrity_ok, corroborated = _call(exact_match=False)
        self.assertTrue(integrity_ok)
        self.assertFalse(corroborated)

    def test_uncorroborated_negative_claim_stays_fatal(self):
        """The asymmetry. An unconfirmable limitation must NOT clear the gate.

        A record reading "calcium channel blockers are ineffective for Timothy
        syndrome" that we cannot tie to the exact candidate drug still fails
        the search. The deterministic drug matcher may be wrong, and silently
        dropping a real limitation is the dangerous direction to err in.
        """
        integrity_ok, corroborated = _call(
            label=EXPLICIT_LIMITATION, explicit_language=False)
        self.assertFalse(corroborated)
        self.assertFalse(
            integrity_ok,
            "an uncorroborated negative claim must still fail closed")

    def test_negative_language_in_source_also_stays_fatal(self):
        integrity_ok, _ = _call(
            exact_match=False, source_has_negative_language=True)
        self.assertFalse(integrity_ok)

    def test_positive_and_negative_uncorroborated_claims_differ(self):
        """The whole point of the split, stated as one comparison."""
        positive_ok, _ = _call(
            label=APPLICABLE_SUPPORT, supportive_language=False)
        negative_ok, _ = _call(
            label=EXPLICIT_LIMITATION, explicit_language=False)
        self.assertTrue(positive_ok, "uncounted support must not fail a search")
        self.assertFalse(negative_ok, "an unconfirmable limitation must")

    def test_explicit_limitation_with_negative_language_is_corroborated(self):
        _, corroborated = _call(
            label=EXPLICIT_LIMITATION, explicit_language=True)
        self.assertTrue(corroborated)

    def test_support_claimed_alongside_negative_language_is_refused(self):
        """A record cannot be support and a limitation at once."""
        _, corroborated = _call(supportive_language=True, explicit_language=True)
        self.assertFalse(corroborated)


class HealthyRecordTest(unittest.TestCase):

    def test_a_well_formed_supported_record_passes_both(self):
        integrity_ok, corroborated = _call()
        self.assertTrue(integrity_ok)
        self.assertTrue(corroborated)

    def test_irrelevant_routing_still_corroborates(self):
        _, corroborated = _call(
            label=NOT_APPLICABLE_TO_EXACT_DRUG_USE,
            requested_label=NOT_APPLICABLE_TO_EXACT_DRUG_USE,
            exact_match=False,
        )
        self.assertTrue(corroborated)


class CounterSemanticsTest(unittest.TestCase):
    """Only integrity failures may increment the counter that fails a search."""

    def test_the_increment_is_keyed_to_integrity_not_validity(self):
        import inspect
        from data_sources import literature_limitation
        source = inspect.getsource(literature_limitation)
        self.assertIn(
            "if not classifier_integrity_ok:\n            "
            "classifier_integrity_failures += 1",
            source,
            "the counter must increment on integrity failure only")
        self.assertNotIn(
            "if not valid:\n            classifier_integrity_failures += 1",
            source,
            "an uncorroborated record must not fail the whole search")


if __name__ == "__main__":
    unittest.main()
