"""Tests for the self-hosting scoring-weight override knobs and the dossier
score-breakdown disclosure that must accompany them.

Contract under test:
  * AGENTBIO_TRACTABILITY_WEIGHTS / AGENTBIO_COMPOSITE_WEIGHTS accept a JSON
    object with EXACTLY the default key set, finite non-negative values, and a
    positive total. Anything else falls back to defaults and never raises
    (import-time parsing must not be able to take down API startup).
  * When an override is active, the dossier's composite breakdown carries a
    "Non-default scoring configuration" banner (formula.scoring_config_overridden),
    because such scores are not comparable to the frozen benchmark.
  * The breakdown table renders the v2 efficacy_evidence schema (weight and
    contribution actually shown) and shows the coverage renormalization step
    when a term was never observed.

Run:  python3 -m unittest validation.test_scoring_weight_overrides -v
"""

import os
import unittest
from unittest import mock

from agents.target_selection import (
    _DEFAULT_TRACTABILITY_WEIGHTS,
    _load_weight_overrides,
)
from agents.reviewer import (
    _DEFAULT_COMPOSITE_WEIGHTS,
    _load_composite_weight_overrides,
)
from agents.writer import _composite_breakdown

_TRACT_ENV = "AGENTBIO_TRACTABILITY_WEIGHTS"
_COMP_ENV = "AGENTBIO_COMPOSITE_WEIGHTS"


def _load_tract(raw):
    env = {_TRACT_ENV: raw} if raw is not None else {}
    with mock.patch.dict(os.environ, env, clear=False):
        if raw is None:
            os.environ.pop(_TRACT_ENV, None)
        return _load_weight_overrides(
            _TRACT_ENV, _DEFAULT_TRACTABILITY_WEIGHTS, "tractability")


def _load_comp(raw):
    env = {_COMP_ENV: raw} if raw is not None else {}
    with mock.patch.dict(os.environ, env, clear=False):
        if raw is None:
            os.environ.pop(_COMP_ENV, None)
        return _load_composite_weight_overrides()


class TestTractabilityWeightOverrides(unittest.TestCase):

    def test_absent_env_returns_defaults(self):
        weights, overridden = _load_tract(None)
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_valid_override_accepted(self):
        weights, overridden = _load_tract(
            '{"chembl_log_count": 0.5, "afdb_plddt": 0.3, "trial_penalty": 0.2}')
        self.assertTrue(overridden)
        self.assertEqual(weights["chembl_log_count"], 0.5)

    def test_missing_key_falls_back(self):
        weights, overridden = _load_tract('{"chembl_log_count": 1.0}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_extra_key_falls_back(self):
        weights, overridden = _load_tract(
            '{"chembl_log_count": 0.4, "afdb_plddt": 0.35, '
            '"trial_penalty": 0.25, "surprise": 0.1}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_all_zero_falls_back(self):
        # An all-zero vector would divide by zero downstream.
        weights, overridden = _load_tract(
            '{"chembl_log_count": 0, "afdb_plddt": 0, "trial_penalty": 0}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_negative_falls_back(self):
        weights, overridden = _load_tract(
            '{"chembl_log_count": 0.9, "afdb_plddt": -0.2, "trial_penalty": 0.3}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_nan_falls_back(self):
        # json.loads accepts bare NaN tokens — these must not reach scoring.
        weights, overridden = _load_tract(
            '{"chembl_log_count": NaN, "afdb_plddt": 0.35, "trial_penalty": 0.25}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_infinity_falls_back(self):
        weights, overridden = _load_tract(
            '{"chembl_log_count": Infinity, "afdb_plddt": 0.35, "trial_penalty": 0.25}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_malformed_json_falls_back(self):
        weights, overridden = _load_tract("not json at all")
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)

    def test_non_object_json_falls_back(self):
        weights, overridden = _load_tract('["chembl_log_count"]')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_TRACTABILITY_WEIGHTS)


class TestCompositeWeightOverrides(unittest.TestCase):

    def test_absent_env_returns_defaults(self):
        weights, overridden = _load_comp(None)
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_COMPOSITE_WEIGHTS)

    def test_valid_override_accepted(self):
        weights, overridden = _load_comp(
            '{"efficacy_evidence": 0.6, "ot_association": 0.2, '
            '"tanimoto": 0.1, "no_failed_trial": 0.1}')
        self.assertTrue(overridden)
        self.assertEqual(weights["efficacy_evidence"], 0.6)

    def test_all_zero_falls_back(self):
        # ZeroDivisionError in _coverage_aware_composite otherwise.
        weights, overridden = _load_comp(
            '{"efficacy_evidence": 0, "ot_association": 0, '
            '"tanimoto": 0, "no_failed_trial": 0}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_COMPOSITE_WEIGHTS)

    def test_nan_falls_back(self):
        weights, overridden = _load_comp(
            '{"efficacy_evidence": NaN, "ot_association": 0.2, '
            '"tanimoto": 0.15, "no_failed_trial": 0.15}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_COMPOSITE_WEIGHTS)

    def test_zero_efficacy_falls_back(self):
        # efficacy_evidence is the only always-observed term; a zero weight
        # would allow coverage == 0 (all optional terms unobserved) and a
        # ZeroDivisionError in _coverage_aware_composite.
        weights, overridden = _load_comp(
            '{"efficacy_evidence": 0, "ot_association": 0.4, '
            '"tanimoto": 0.3, "no_failed_trial": 0.3}')
        self.assertFalse(overridden)
        self.assertEqual(weights, _DEFAULT_COMPOSITE_WEIGHTS)


class TestWriterBreakdownDisclosure(unittest.TestCase):

    _WEIGHTS = {"efficacy_evidence": 0.50, "ot_association": 0.20,
                "tanimoto": 0.15, "no_failed_trial": 0.15}

    def _candidate(self, **components):
        comp = {"efficacy_evidence": 0.8, "normalized_ot_association": 0.5,
                "normalized_tanimoto": 0.3, "no_failed_trial": 1,
                "evidence_weight_coverage": 1.0}
        comp.update(components)
        return {"drug_name": "TestDrug", "score_components": comp,
                "composite_score": 0.695}

    def test_v2_schema_renders_efficacy_row_with_real_contribution(self):
        out = _composite_breakdown(self._candidate(),
                                   {"composite_weights": self._WEIGHTS})
        self.assertIn("Efficacy evidence", out)
        # 0.50 * 0.8 = 0.4 must appear as a printed contribution, and the
        # weighted subtotal 0.4 + 0.1 + 0.045 + 0.15 = 0.695 must be shown.
        self.assertIn("0.4000", out)
        self.assertIn("0.6950", out)

    def test_coverage_gap_shows_renormalization(self):
        cand = self._candidate(normalized_tanimoto=None,
                               evidence_weight_coverage=0.85)
        out = _composite_breakdown(cand, {"composite_weights": self._WEIGHTS})
        self.assertIn("not observed", out)
        self.assertIn("renormalized over covered weight", out)
        # observed subtotal 0.4 + 0.1 + 0.15 = 0.65 over covered weight 0.85
        self.assertIn("0.6500", out)
        self.assertIn("0.8500", out)

    def test_override_banner_shown_when_flagged(self):
        out = _composite_breakdown(
            self._candidate(),
            {"composite_weights": self._WEIGHTS,
             "scoring_config_overridden": True})
        self.assertIn("Non-default scoring configuration", out)
        self.assertIn("not comparable", out.lower())

    def test_no_banner_by_default(self):
        out = _composite_breakdown(self._candidate(),
                                   {"composite_weights": self._WEIGHTS})
        self.assertNotIn("Non-default scoring configuration", out)

    def test_directional_bonus_row_shown(self):
        out = _composite_breakdown(
            self._candidate(qualified_directional_bonus=0.05),
            {"composite_weights": self._WEIGHTS})
        self.assertIn("Qualified directional evidence bonus", out)
        self.assertIn("+0.0500", out)

    def test_non_unit_total_override_shows_renormalization(self):
        # The Reviewer always divides numerator by covered weight; with a
        # valid override whose weights sum to 4.0 the dossier must show that
        # division even though every term was observed (no exclusions).
        weights = {k: 1.0 for k in self._WEIGHTS}
        cand = self._candidate(evidence_weight_coverage=4.0)
        out = _composite_breakdown(cand, {"composite_weights": weights})
        # subtotal 0.8 + 0.5 + 0.3 + 1 = 2.6, renormalized 2.6 / 4.0 = 0.65
        self.assertIn("2.6000", out)
        self.assertIn("renormalized over covered weight", out)
        self.assertIn("0.6500", out)

    def test_unit_total_no_exclusions_omits_identity_division(self):
        # With default (unit-sum) weights and full observation the division is
        # the identity; showing it would be noise, so it stays hidden.
        out = _composite_breakdown(self._candidate(),
                                   {"composite_weights": self._WEIGHTS})
        self.assertNotIn("renormalized over covered weight", out)


class TestBlankModeCacheInvalidation(unittest.TestCase):
    """Blank-mode runs must not silently reuse a Stage-1 ranking produced
    under an unknown or mismatched tractability configuration — the override
    knob would be ineffective and the dossier banner misleading.  The ranking
    carries a fingerprint sidecar (top_candidates.config.json) that must
    match the active weights."""

    _ROWS = [{"disease_name": "X"}]

    def _run(self, *, overridden, fingerprint, force=False):
        import main_graph

        def fake_load(name):
            if name == "top_candidates.json":
                return list(self._ROWS)
            if name == "top_candidates.config.json":
                return fingerprint
            return None

        with mock.patch.object(main_graph, "TRACTABILITY_WEIGHTS_OVERRIDDEN", overridden), \
             mock.patch.object(main_graph, "FORCE_RECOMPUTE", force), \
             mock.patch.object(main_graph, "_load_json", side_effect=fake_load):
            return main_graph._blank_mode_rows()

    def test_unfingerprinted_ranking_rejected_under_override(self):
        with self.assertRaises(RuntimeError):
            self._run(overridden=True, fingerprint=None)

    def test_unfingerprinted_ranking_allowed_without_override(self):
        # Legacy pre-fingerprint artifact necessarily used default weights.
        self.assertEqual(self._run(overridden=False, fingerprint=None),
                         self._ROWS)

    def test_fingerprint_mismatch_rejected(self):
        fp = {"ranking_schema": 1,
              "tractability_weights": {"chembl_log_count": 0.5,
                                       "afdb_plddt": 0.3,
                                       "trial_penalty": 0.2}}
        with self.assertRaises(RuntimeError):
            self._run(overridden=False, fingerprint=fp)

    def test_fingerprint_match_accepted(self):
        fp = {"ranking_schema": 1,
              "tractability_weights": dict(_DEFAULT_TRACTABILITY_WEIGHTS)}
        self.assertEqual(self._run(overridden=False, fingerprint=fp),
                         self._ROWS)

    def test_force_recompute_returns_none_when_config_valid(self):
        fp = {"ranking_schema": 1,
              "tractability_weights": dict(_DEFAULT_TRACTABILITY_WEIGHTS)}
        self.assertIsNone(self._run(overridden=False, fingerprint=fp,
                                    force=True))

    def test_force_recompute_still_rejects_mismatched_fingerprint(self):
        # FORCE_RECOMPUTE must not bypass the fingerprint check: the sweep
        # manager would hand the same stale file back to the caller.
        fp = {"ranking_schema": 1,
              "tractability_weights": {"chembl_log_count": 0.5,
                                       "afdb_plddt": 0.3,
                                       "trial_penalty": 0.2}}
        with self.assertRaises(RuntimeError):
            self._run(overridden=False, fingerprint=fp, force=True)

    def test_force_recompute_no_file_returns_none(self):
        import main_graph
        with mock.patch.object(main_graph, "TRACTABILITY_WEIGHTS_OVERRIDDEN", True), \
             mock.patch.object(main_graph, "FORCE_RECOMPUTE", True), \
             mock.patch.object(main_graph, "_load_json", return_value=None):
            self.assertIsNone(main_graph._blank_mode_rows())


if __name__ == "__main__":
    unittest.main()
