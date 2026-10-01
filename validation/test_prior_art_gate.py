"""The prior-art gate: does a published record already pair this drug and disease?

Built 2026-09-29 to close a capability gap that produced a live false novelty
claim. The pipeline's only novelty-adjacent signals counted TRIALS
(`prior_trial_count`, `literature_trial_count`), so a preclinical paper
proposing exactly this drug for exactly this disease scored zero on both.

A blind run on resistance to thyroid hormone beta promoted RESMETIROM with
prior_trial_count=0 and a cleared literature gate. Three Europe PMC records
already paired them, including a title-level one ("Effects of Resmetirom on
Resistance to Thyroid Hormone Receptor Mutants") and PMID 40301009, which
generated 57 mutant TRbetas to determine which RTH mutations resmetirom rescues.

These tests are offline. Every function exercised here is pure; the network
path is covered by the controls recorded in `PriorArtSemanticsTest`.
"""

import os
import unittest

from data_sources import prior_art
from data_sources.prior_art import (
    STRENGTH_DISTANT,
    STRENGTH_PROXIMAL,
    STRENGTH_TITLE,
    VERDICT_FOUND,
    VERDICT_NONE,
    VERDICT_UNCONFIRMED,
    build_query,
    confirm_hit,
    disease_terms,
    drug_terms,
    min_term_distance,
)

RTH_ORPHANET = ("Resistance to thyroid hormone due to a mutation in thyroid "
                "hormone receptor beta")
RTH_CORE = "Resistance to thyroid hormone"


class WhitespaceNormalizationTest(unittest.TestCase):
    """Line-wrapped phrases must still match.

    This is the bug that decided a headline result. The literature gate's
    disease-name suppression missed a PubMed title wrapped as "resistance to
    thyroid\\nhormone"; the unsuppressed match invalidated liothyronine and
    levothyroxine, and resmetirom was promoted in their place. Any phrase
    matching over fetched text normalizes whitespace first.
    """

    def test_newline_wrapped_phrase_is_matched(self):
        record = {
            "title": "Tissue responses in resistance to thyroid\nhormone",
            "abstractText": "Resmetirom was assessed.",
        }
        hit = confirm_hit(record, ["resmetirom"], [RTH_CORE])
        self.assertIsNotNone(hit, "a wrapped disease name must still match")

    def test_multiple_spaces_and_tabs_collapse(self):
        record = {
            "title": "Resistance   to\tthyroid  hormone and resmetirom",
            "abstractText": "",
        }
        hit = confirm_hit(record, ["resmetirom"], [RTH_CORE])
        self.assertIsNotNone(hit)
        self.assertEqual(hit["strength"], STRENGTH_TITLE)


class DiseaseTermTest(unittest.TestCase):
    """Orphanet writes qualifier clauses the literature drops."""

    def test_orphanet_qualifier_clause_is_stripped(self):
        terms = disease_terms(RTH_ORPHANET)
        self.assertIn(RTH_ORPHANET, terms)
        self.assertIn(RTH_CORE, terms)

    def test_searching_only_the_full_string_would_find_nothing(self):
        """The core name is what abstracts actually contain."""
        abstract = ("Resistance to thyroid hormone is a rare congenital "
                    "disorder of reduced sensitivity.")
        self.assertNotIn(RTH_ORPHANET.lower(), abstract.lower())
        self.assertIn(RTH_CORE.lower(), abstract.lower())

    def test_short_abbreviations_are_refused(self):
        """"RTH", "MS", "ALS" collide across fields and would invent prior art."""
        for name in ("RTH", "MS", "ALS", ""):
            self.assertEqual(disease_terms(name), [])

    def test_terms_are_deduplicated(self):
        terms = disease_terms("Achondroplasia", extra_aliases=["achondroplasia"])
        self.assertEqual(len(terms), 1)


class DrugTermTest(unittest.TestCase):

    def test_development_codes_are_included(self):
        self.assertEqual(drug_terms("RESMETIROM", ["MGL-3196"]),
                         ["RESMETIROM", "MGL-3196"])

    def test_very_short_tokens_are_refused(self):
        self.assertEqual(drug_terms("T3"), [])

    def test_hyphenated_code_matches_as_a_whole_token(self):
        record = {"title": "MGL-3196 in resistance to thyroid hormone",
                  "abstractText": ""}
        self.assertIsNotNone(confirm_hit(record, ["MGL-3196"], [RTH_CORE]))

    def test_a_code_does_not_match_inside_a_longer_token(self):
        record = {"title": "XMGL-31960 in resistance to thyroid hormone",
                  "abstractText": ""}
        self.assertIsNone(confirm_hit(record, ["MGL-3196"], [RTH_CORE]))


class QueryTest(unittest.TestCase):

    def test_query_requires_both_sides(self):
        query = build_query(["resmetirom"], [RTH_CORE])
        self.assertIn(" AND ", query)
        self.assertIn('TITLE_ABS:"resmetirom"', query)
        self.assertIn(f'TITLE_ABS:"{RTH_CORE}"', query)

    def test_alternatives_are_or_joined(self):
        query = build_query(["resmetirom", "MGL-3196"], [RTH_CORE])
        self.assertIn(" OR ", query)


class ConfirmHitTest(unittest.TestCase):
    """Search-engine relevance is not evidence; every hit is re-verified."""

    def test_a_record_missing_the_drug_is_rejected(self):
        record = {"title": "Resistance to thyroid hormone: a review",
                  "abstractText": "Levothyroxine was discussed."}
        self.assertIsNone(confirm_hit(record, ["resmetirom"], [RTH_CORE]))

    def test_a_record_missing_the_disease_is_rejected(self):
        record = {"title": "Resmetirom in MASH",
                  "abstractText": "Liver fibrosis endpoints."}
        self.assertIsNone(confirm_hit(record, ["resmetirom"], [RTH_CORE]))

    def test_empty_record_is_rejected(self):
        self.assertIsNone(confirm_hit({}, ["resmetirom"], [RTH_CORE]))

    def test_title_co_occurrence_is_the_strongest_signal(self):
        record = {"title": "Effects of Resmetirom on Resistance to Thyroid "
                           "Hormone Receptor Mutants",
                  "abstractText": "..."}
        hit = confirm_hit(record, ["Resmetirom"], [RTH_CORE])
        self.assertEqual(hit["strength"], STRENGTH_TITLE)

    def test_abstract_only_co_occurrence_still_counts(self):
        """The motivating paper names the disease only in its abstract."""
        record = {
            "title": "Resmetirom Is an Effective Thyromimetics for the "
                     "Chemical Rescue of Thyroid Hormone Receptor Mutants",
            "abstractText": ("Mutations in TRs lead to a condition known as "
                             "resistance to thyroid hormone (RTH)."),
        }
        hit = confirm_hit(record, ["Resmetirom"], [RTH_CORE])
        self.assertIsNotNone(hit)
        self.assertEqual(hit["strength"], STRENGTH_PROXIMAL)

    def test_distant_co_occurrence_is_marked_incidental(self):
        """A broad review naming both without relating them."""
        record = {
            "title": "Biochemistry, Cyclic GMP",
            "abstractText": ("Sildenafil inhibits PDE5. " + ("filler " * 120)
                             + " Achondroplasia involves FGFR3 signalling."),
        }
        hit = confirm_hit(record, ["Sildenafil"], ["Achondroplasia"])
        self.assertEqual(hit["strength"], STRENGTH_DISTANT)


class TermDistanceTest(unittest.TestCase):

    def test_absent_side_yields_none(self):
        self.assertIsNone(min_term_distance("resmetirom only", ["resmetirom"],
                                            [RTH_CORE]))

    def test_overlapping_mentions_are_zero(self):
        text = "Resistance to thyroid hormone receptor beta agonist"
        self.assertEqual(
            min_term_distance(text, ["thyroid hormone"], [RTH_CORE]), 0)

    def test_closest_pair_is_reported(self):
        text = ("resmetirom " + ("x " * 200)
                + "resistance to thyroid hormone and resmetirom again")
        self.assertLess(min_term_distance(text, ["resmetirom"], [RTH_CORE]), 60)


class PriorArtSemanticsTest(unittest.TestCase):
    """Verdict semantics. These are the claims the dossier will rest on.

    Live controls run against Europe PMC on 2026-09-29:

        RESMETIROM   x RTH-beta       -> PRIOR_ART_FOUND       (3 records)
        INFIGRATINIB x Achondroplasia -> PRIOR_ART_FOUND       (24 records)
        VOSORITIDE   x Achondroplasia -> PRIOR_ART_FOUND
        SILDENAFIL   x Achondroplasia -> PRIOR_ART_UNCONFIRMED (1 incidental)
        CLOXACILLIN  x Achondroplasia -> NO_PRIOR_ART_FOUND
        ASPIRIN      x RTH-beta       -> NO_PRIOR_ART_FOUND
    """

    def test_absence_is_never_stated_as_novelty(self):
        """The gate answers a bounded question and must say so."""
        envelope = prior_art._envelope(VERDICT_NONE, "reason")
        self.assertFalse(envelope["found"])
        self.assertTrue(envelope["gate_cleared"])

    def test_no_verdict_asserts_novelty(self):
        for verdict in (VERDICT_FOUND, VERDICT_NONE, VERDICT_UNCONFIRMED,
                        prior_art.VERDICT_FAILED,
                        prior_art.VERDICT_NOT_ASSESSED):
            self.assertNotIn("NOVEL", verdict.upper())

    def test_search_failure_is_unknown_not_a_clear_gate(self):
        """Fail-closed: an outage must not read as absence of prior art."""
        envelope = prior_art._envelope(prior_art.VERDICT_FAILED, "outage")
        self.assertFalse(envelope["found"])
        self.assertFalse(envelope["gate_cleared"])
        self.assertEqual(envelope["source_status"], "UNAVAILABLE")

    def test_unconfirmed_does_not_block_the_candidate(self):
        """One passing mention is not a proposal."""
        envelope = prior_art._envelope(VERDICT_UNCONFIRMED, "single mention")
        self.assertFalse(envelope["found"])
        self.assertTrue(envelope["gate_cleared"])
        self.assertTrue(envelope["requires_human_review"])

    def test_found_blocks_and_needs_no_human_review(self):
        envelope = prior_art._envelope(VERDICT_FOUND, "records exist")
        self.assertTrue(envelope["found"])
        self.assertFalse(envelope["gate_cleared"])

    def test_bounded_scope_is_disclosed_in_the_negative_reason(self):
        """The wording must not let a reader infer certainty."""
        source = prior_art.check_prior_art.__doc__ or ""
        self.assertIn("never a novelty certification", source)


class GateToggleTest(unittest.TestCase):
    """Discovery and validation want opposite consequences from one fact.

    Hunting a new pair, a published record disqualifies the candidate. Auditing
    a hypothesis that already exists -- the audit/triage front door, or
    reproducing a known pair to demonstrate the machine reaches it -- prior art
    is the expected result, and a gate that excluded it would refuse to score
    precisely the cases used to validate the pipeline.

    So the search always runs and the finding is always recorded. Only whether
    it blocks is configurable.
    """

    def setUp(self):
        self._saved = os.environ.get(prior_art._GATE_ENV)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop(prior_art._GATE_ENV, None)
        else:
            os.environ[prior_art._GATE_ENV] = self._saved

    def test_enabled_by_default(self):
        os.environ.pop(prior_art._GATE_ENV, None)
        self.assertTrue(prior_art.gate_is_enabled())

    def test_disengaged_by_recognised_values(self):
        for value in ("off", "0", "false", "no", "disabled",
                      "OFF", " False "):
            os.environ[prior_art._GATE_ENV] = value
            self.assertFalse(prior_art.gate_is_enabled(),
                             f"{value!r} should disengage the gate")

    def test_unrecognised_value_leaves_the_gate_on(self):
        """An unparsed setting must not silently disable a gate."""
        for value in ("on", "1", "true", "yes", "banana", ""):
            os.environ[prior_art._GATE_ENV] = value
            self.assertTrue(prior_art.gate_is_enabled())

    def test_disengaging_does_not_suppress_the_search(self):
        """Turning the gate off changes the consequence, not the reporting."""
        import inspect
        source = inspect.getsource(prior_art.check_prior_art)
        self.assertNotIn("gate_is_enabled", source,
                         "the search itself must not depend on the toggle")


class CacheVersionTest(unittest.TestCase):
    """A changed result shape must move the cache key with it.

    This repo has been bitten twice by a fix that shipped inert because a
    cached value from the previous contract was replayed. The prior-art result
    shape changed twice during its own construction (proximity, then
    corroboration), so the key carries a version.
    """

    def test_cache_key_is_versioned_past_the_superseded_shapes(self):
        self.assertNotEqual(prior_art._CACHE_VERSION, "prior_art_v1")
        self.assertNotEqual(prior_art._CACHE_VERSION, "prior_art_v2_proximity")

    def test_transient_failure_is_not_cached(self):
        """An outage hardening into a permanent 'no prior art' reads as novelty."""
        import inspect
        source = inspect.getsource(prior_art.check_prior_art)
        failure_block = source.split("except _SourceUnavailable")[1]
        failure_block = failure_block.split("hits = ")[0]
        self.assertNotIn("cache_set", failure_block)


if __name__ == "__main__":
    unittest.main()
