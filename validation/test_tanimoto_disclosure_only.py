"""Structural similarity is disclosed, not scored.

Post-benchmark correction of 2026-09-27. Tanimoto was a 0.15-weight scoring
term measuring similarity to the approved drugs already in the pool for the
same target. In a repurposing pool every candidate is by construction a known
active at that target, so the term mostly measured CHEMOTYPE CROWDING: a drug
that is one of five near-identical analogues scores high against its own
siblings, while a structurally distinct potent binder scores low and is
penalised for it.

The worked example is an Acrodysostosis run. AMINOPHYLLINE -- theophylline plus
ethylenediamine, with no measured PDE4D potency at all -- beat ROFLUMILAST,
which binds PDE4D at pChEMBL 9.89, because the pool held five methylxanthines:

    AMINOPHYLLINE  eff 0.8725  tan 0.7436  pChEMBL none  -> composite 0.8915
    roflumilast    eff 0.9575  tan 0.1720  pChEMBL 9.886 -> composite 0.8406

The Tanimoto gap (0.0857 weighted) was twice roflumilast's evidence advantage
(0.0425 weighted), so a term about chemical lookalikes selected the candidate
over measured affinity and evidence quality.
"""

import unittest
from unittest import mock

from agents.reviewer import COMPOSITE_WEIGHTS, _coverage_aware_composite


class TanimotoIsNotScoredTest(unittest.TestCase):

    def test_similarity_cannot_outrank_evidence(self):
        """The acrodysostosis inversion, with Tanimoto excluded."""
        crowded, _ = _coverage_aware_composite(
            efficacy_evidence=0.8725, ot_association=0.837,
            tanimoto=None, no_failed_trial=None)
        distinct, _ = _coverage_aware_composite(
            efficacy_evidence=0.9575, ot_association=0.837,
            tanimoto=None, no_failed_trial=None)
        self.assertGreater(
            distinct, crowded,
            "the better-evidenced candidate must rank higher")

    def test_two_candidates_differing_only_in_similarity_now_tie(self):
        lookalike, _ = _coverage_aware_composite(
            efficacy_evidence=0.90, ot_association=0.80,
            tanimoto=None, no_failed_trial=None)
        novel_chemotype, _ = _coverage_aware_composite(
            efficacy_evidence=0.90, ot_association=0.80,
            tanimoto=None, no_failed_trial=None)
        self.assertEqual(lookalike, novel_chemotype)

    def test_the_weight_still_exists_for_frozen_studies(self):
        """Holdout runs must reproduce the original semantics exactly."""
        self.assertIn("tanimoto", COMPOSITE_WEIGHTS)
        self.assertGreater(COMPOSITE_WEIGHTS["tanimoto"], 0)

    def test_weight_is_applied_when_a_value_is_passed(self):
        """_coverage_aware_composite itself is unchanged; the call site gates."""
        without, cov_without = _coverage_aware_composite(
            efficacy_evidence=0.90, ot_association=0.80,
            tanimoto=None, no_failed_trial=None)
        with_tan, cov_with = _coverage_aware_composite(
            efficacy_evidence=0.90, ot_association=0.80,
            tanimoto=1.0, no_failed_trial=None)
        self.assertGreater(with_tan, without)
        self.assertGreater(cov_with, cov_without)


class ProductionGatingTest(unittest.TestCase):
    """The gate is `_holdout.is_active()`, matching the other post-benchmark
    corrections: production gets the fix, frozen studies keep old semantics."""

    def test_production_path_passes_none(self):
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertIn(
            "n_tanimoto if _holdout.is_active() else None", source,
            "the composite call must gate Tanimoto on holdout state")

    def test_tanimoto_is_still_persisted_for_disclosure(self):
        """Removing it from the score must not remove it from the dossier."""
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertIn('"tanimoto_score": c.get("tanimoto_score")', source)
        self.assertIn("normalized_tanimoto", source)


if __name__ == "__main__":
    unittest.main()
