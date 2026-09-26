"""A compound is ranked on its best-attributed target, and compared only there.

Regression cover for two post-benchmark corrections of 2026-09-26, both found
in the same AKT2 run:

  * The same drug pooled against several targets was ranked independently per
    target, so a thin pathway-neighbour row outranked the row on the gene that
    causes the disease. See `_demote_weaker_tier_duplicates`.
  * The Tanimoto approved-drug reference set was built flat across every
    pooled target, so a candidate was scored against an approved drug with no
    pharmacology at its target at all. See `agents/chemist.py`.
"""

import unittest

from agents.reviewer import (
    _compound_identity,
    _demote_weaker_tier_duplicates,
    _rank_reviewed,
)


def _row(name: str, target: str, method: str, score: float,
         inchikey: str = "") -> dict:
    return {
        "drug_name": name,
        "target_symbol": target,
        "target_discovery_method": method,
        "composite_score": score,
        "pre_cap_score": score,
        "inchikey": inchikey,
        "strong_match": True,
    }


CAPIVASERTIB_KEY = "NHVQJIRCTIYIRY-CQSZACIVSA-N"


class WeakerTierDuplicateDemotionTest(unittest.TestCase):

    def test_precedent_row_loses_to_causal_row_for_the_same_drug(self):
        """The AKT1/AKT2 case: right drug, wrong gene, higher score."""
        reviewed = [
            _row("capivasertib", "AKT1", "pharmacological_precedent", 0.8256,
                 CAPIVASERTIB_KEY),
            _row("capivasertib", "AKT2", "genetic_association", 0.8183,
                 CAPIVASERTIB_KEY),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        self.assertEqual(reviewed[0]["target_symbol"], "AKT2")
        self.assertTrue(reviewed[1]["tier_duplicate_demoted"])
        self.assertFalse(reviewed[0]["tier_duplicate_demoted"])

    def test_scores_are_never_changed(self):
        reviewed = [
            _row("capivasertib", "AKT1", "pharmacological_precedent", 0.8256,
                 CAPIVASERTIB_KEY),
            _row("capivasertib", "AKT2", "genetic_association", 0.8183,
                 CAPIVASERTIB_KEY),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        by_target = {r["target_symbol"]: r["composite_score"] for r in reviewed}
        self.assertEqual(by_target["AKT1"], 0.8256)
        self.assertEqual(by_target["AKT2"], 0.8183)

    def test_different_drugs_are_never_compared_to_each_other(self):
        """Demotion is per compound; an unrelated drug keeps its rank."""
        reviewed = [
            _row("drug-a", "AKT1", "pharmacological_precedent", 0.90, "AAA-B-N"),
            _row("drug-b", "AKT2", "genetic_association", 0.10, "BBB-B-N"),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        self.assertEqual(reviewed[0]["drug_name"], "drug-a")
        self.assertFalse(reviewed[0]["tier_duplicate_demoted"])

    def test_single_attribution_pool_is_untouched(self):
        reviewed = [
            _row("drug-a", "AKT2", "genetic_association", 0.80, "AAA-B-N"),
            _row("drug-b", "AKT2", "genetic_association", 0.70, "BBB-B-N"),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        self.assertEqual(
            [r["drug_name"] for r in reviewed], ["drug-a", "drug-b"])
        self.assertFalse(any(r["tier_duplicate_demoted"] for r in reviewed))

    def test_exploratory_row_loses_to_precedent_row(self):
        """Strength is ordered across all three tiers, not just causal/other."""
        reviewed = [
            _row("d", "NRG1", "pathway_neighbor", 0.90, CAPIVASERTIB_KEY),
            _row("d", "AKT1", "pharmacological_precedent", 0.50,
                 CAPIVASERTIB_KEY),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        self.assertEqual(reviewed[0]["target_symbol"], "AKT1")

    def test_salt_and_stereo_variants_group_as_one_compound(self):
        reviewed = [
            _row("d salt", "AKT1", "pharmacological_precedent", 0.90,
                 "NHVQJIRCTIYIRY-XXXXXXXXXX-N"),
            _row("d", "AKT2", "genetic_association", 0.10, CAPIVASERTIB_KEY),
        ]
        _demote_weaker_tier_duplicates(reviewed)
        self.assertEqual(reviewed[0]["target_symbol"], "AKT2")

    def test_identity_falls_back_to_name_without_a_structure(self):
        self.assertEqual(
            _compound_identity({"drug_name": "Capivasertib"}),
            "name:capivasertib")

    def test_rank_reviewed_resolves_attribution_before_ranking(self):
        reviewed = [
            _row("capivasertib", "AKT1", "pharmacological_precedent", 0.8256,
                 CAPIVASERTIB_KEY),
            _row("capivasertib", "AKT2", "genetic_association", 0.8183,
                 CAPIVASERTIB_KEY),
        ]
        _rank_reviewed(reviewed)
        self.assertEqual(reviewed[0]["target_symbol"], "AKT2")


class TanimotoReferenceScopeTest(unittest.TestCase):

    def test_reference_set_is_keyed_by_target(self):
        """The chemist must not lend one target's approved drugs to another.

        Structural guard: the reference map is target -> {molecule_id: ...},
        so a candidate can only ever be compared within its own target.
        """
        import inspect
        from agents import chemist
        source = inspect.getsource(chemist.run_chemist)
        self.assertIn("approved_fps.get(", source)
        self.assertIn("approved_fps.setdefault(", source)
        self.assertNotIn(
            "for mid, (ref, ref_fp, ref_dfp) in approved_fps.items():", source,
            "reference set is being iterated flat across all targets")


if __name__ == "__main__":
    unittest.main()
