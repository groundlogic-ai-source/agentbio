"""Association by geography is not association by biology.

Built 2026-09-29. Genetic association is the pipeline's strongest
target-selection signal and cannot distinguish a gene that causes a disease
from a gene that happens to sit beside one. Where the common mutation is a
large deletion, every gene in the interval inherits a real, statistically
genuine association for positional reasons.

Cystinosis is the worked case. The common Northern European mutation is a 57-kb
deletion removing CTNS and extending into neighbouring sequence. Target
selection returned CTNS, SHPK and TRPV1. Live Ensembl coordinates on
chromosome 17:

    TRPV1   3,565,444 - 3,609,880
    SHPK    3,607,433 - 3,636,637
    CTNS    3,636,446 - 3,663,103

TRPV1 looked like a strong, heavily-drugged target with a real association. It
is a capsaicin receptor whose loss produces a sensory phenotype and has nothing
to do with the renal failure that defines the disease. A TRPV1 agonist proposed
for a kidney disease is the kind of claim that ends a conversation with a
biologist, and it was caught by hand rather than by the pipeline.

The control behaves: Niemann-Pick type C returns NPC1 (chr18), NPC2 (chr14),
UGCG (chr9) and SMPD1 (chr11). Different chromosomes, no positional
explanation, nothing flagged.

This is a DISCLOSURE. Neighbouring genes are sometimes genuinely related, so
proximity raises a question for a human rather than settling one.
"""

import unittest
from unittest import mock

from data_sources import gene_locus
from data_sources.gene_locus import (
    DEFAULT_PROXIMITY_BP,
    positional_association_risk,
    span_distance,
)

# Live Ensembl coordinates, retrieved 2026-09-29.
LOCI = {
    "CTNS":    {"symbol": "CTNS",  "chromosome": "17", "start": 3636446, "end": 3663103},
    "SHPK":    {"symbol": "SHPK",  "chromosome": "17", "start": 3607433, "end": 3636637},
    "TRPV1":   {"symbol": "TRPV1", "chromosome": "17", "start": 3565444, "end": 3609880},
    "NPC1":    {"symbol": "NPC1",  "chromosome": "18", "start": 23506184, "end": 23586969},
    "UGCG":    {"symbol": "UGCG",  "chromosome": "9",  "start": 111896772, "end": 111935384},
}

CYSTINOSIS = [
    {"target_symbol": "CTNS", "ot_association_score": 0.90},
    {"target_symbol": "TRPV1", "ot_association_score": 0.70},
    {"target_symbol": "SHPK", "ot_association_score": 0.60},
]
NPC = [
    {"target_symbol": "NPC1", "ot_association_score": 0.95},
    {"target_symbol": "UGCG", "ot_association_score": 0.60},
]


def _with_loci(loci=None):
    table = loci if loci is not None else LOCI
    return mock.patch.object(
        gene_locus, "get_gene_locus", side_effect=lambda s: table.get(str(s).upper()))


class SpanDistanceTest(unittest.TestCase):

    def test_overlapping_genes_are_zero_apart(self):
        self.assertEqual(span_distance(LOCI["SHPK"], LOCI["CTNS"]), 0)

    def test_distance_is_symmetric(self):
        self.assertEqual(span_distance(LOCI["TRPV1"], LOCI["CTNS"]),
                         span_distance(LOCI["CTNS"], LOCI["TRPV1"]))

    def test_the_real_trpv1_ctns_gap(self):
        self.assertEqual(span_distance(LOCI["TRPV1"], LOCI["CTNS"]), 26566)

    def test_different_chromosomes_have_no_distance(self):
        self.assertIsNone(span_distance(LOCI["NPC1"], LOCI["UGCG"]))

    def test_missing_locus_has_no_distance(self):
        self.assertIsNone(span_distance(LOCI["CTNS"], {}))
        self.assertIsNone(span_distance({}, LOCI["CTNS"]))


class CystinosisRegressionTest(unittest.TestCase):
    """The case the pipeline could not see."""

    def test_trpv1_is_flagged_against_ctns(self):
        with _with_loci():
            risk = positional_association_risk(CYSTINOSIS)
        self.assertTrue(risk["TRPV1"]["flagged"])
        self.assertEqual(risk["TRPV1"]["nearest_higher_ranked_target"], "CTNS")
        self.assertEqual(risk["TRPV1"]["distance_bp"], 26566)

    def test_shpk_is_flagged(self):
        with _with_loci():
            risk = positional_association_risk(CYSTINOSIS)
        self.assertTrue(risk["SHPK"]["flagged"])

    def test_the_top_ranked_target_is_never_flagged(self):
        """Nothing outranks the causal gene, so it has no positional story."""
        with _with_loci():
            risk = positional_association_risk(CYSTINOSIS)
        self.assertFalse(risk["CTNS"]["flagged"])
        self.assertIsNone(risk["CTNS"]["nearest_higher_ranked_target"])

    def test_the_disclosure_names_the_neighbour_and_distance(self):
        with _with_loci():
            reason = positional_association_risk(CYSTINOSIS)["TRPV1"]["reason"]
        self.assertIn("CTNS", reason)
        self.assertIn("26,566", reason)

    def test_the_disclosure_does_not_assert_the_association_is_false(self):
        with _with_loci():
            reason = positional_association_risk(CYSTINOSIS)["TRPV1"]["reason"]
        self.assertIn("not a finding", reason.lower())


class ControlTest(unittest.TestCase):

    def test_niemann_pick_targets_are_not_flagged(self):
        with _with_loci():
            risk = positional_association_risk(NPC)
        for symbol in ("NPC1", "UGCG"):
            self.assertFalse(risk[symbol]["flagged"], symbol)

    def test_every_target_gets_an_entry(self):
        """An absent flag must mean 'checked', never 'never looked at'."""
        with _with_loci():
            risk = positional_association_risk(CYSTINOSIS)
        self.assertEqual(set(risk), {"CTNS", "TRPV1", "SHPK"})

    def test_a_distant_same_chromosome_pair_is_not_flagged(self):
        far = {
            "A": {"chromosome": "1", "start": 1_000, "end": 2_000},
            "B": {"chromosome": "1", "start": 90_000_000, "end": 90_001_000},
        }
        with _with_loci(far):
            risk = positional_association_risk(
                [{"target_symbol": "A", "ot_association_score": 0.9},
                 {"target_symbol": "B", "ot_association_score": 0.5}])
        self.assertFalse(risk["B"]["flagged"])
        # The distance is still reported. A same-chromosome neighbour that is
        # simply far away should read as "measured and cleared", not as though
        # no comparison happened.
        self.assertEqual(risk["B"]["distance_bp"], 89_998_000)
        self.assertTrue(risk["B"]["assessed"])


class UnknownIsNotCleanTest(unittest.TestCase):
    """A missing locus removes the ability to make a claim, not the risk."""

    def test_unavailable_coordinates_report_unassessed(self):
        with _with_loci({}):
            risk = positional_association_risk(CYSTINOSIS)
        for entry in risk.values():
            self.assertFalse(entry["assessed"])
            self.assertFalse(entry["flagged"])
            self.assertIn("not a clean result", entry["reason"])

    def test_empty_target_list_is_handled(self):
        self.assertEqual(positional_association_risk([]), {})

    def test_blank_symbols_are_skipped(self):
        with _with_loci():
            self.assertEqual(
                positional_association_risk([{"target_symbol": "  "}]), {})


class ChromosomeNormalisationTest(unittest.TestCase):

    def test_chr_prefix_and_case_are_normalised(self):
        self.assertEqual(gene_locus._canonical_chromosome("chr17"), "17")
        self.assertEqual(gene_locus._canonical_chromosome("X"), "X")

    def test_scaffolds_and_patches_are_refused(self):
        """An unplaced scaffold is not a basis for a proximity claim."""
        for value in ("KI270728.1", "GL000009.2", "", None, "99"):
            self.assertIsNone(gene_locus._canonical_chromosome(value))


class ThresholdTest(unittest.TestCase):

    def test_default_window_covers_the_cystinosis_interval(self):
        self.assertGreater(DEFAULT_PROXIMITY_BP, 98_000)

    def test_window_is_configurable(self):
        with _with_loci():
            risk = positional_association_risk(CYSTINOSIS, proximity_bp=1_000)
        self.assertFalse(risk["TRPV1"]["flagged"],
                         "26kb should fall outside a 1kb window")


if __name__ == "__main__":
    unittest.main()
