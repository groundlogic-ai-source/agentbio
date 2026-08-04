"""
Tests for radical / multi-part conditional hypothesis support.

Covers the two routes added in radical_hypotheses_preregistration.md v1:

  * Boolean composition (all_of / any_of / not_op) — encodes a multi-part
    conditional subgroup as ONE binary column, so it stays powered on the
    narrow framing's ~51 genuine failures.
  * interaction3 — a genuine three-way conditional term, gated by the
    pre-registered events-per-parameter and per-stratum guards.

Also pins two correctness fixes found while building the above:
  * predictor_kind() misrouted continuous ops to Fisher's exact.
  * the confirmation stage used a bare uncorrected p < 0.05, which is
    optional stopping once batches are chained until something confirms.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_prep"),
)
import features as F  # noqa: E402
import stats_tests as S  # noqa: E402
import hypothesis_registry as R  # noqa: E402

XLOGP = {"op": "xlogp_threshold", "params": {"k": 5}}
ORAL = {"op": "is_oral"}
SMALL = {"op": "is_small_molecule"}
ESTAB = {"op": "established"}


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestComposition(unittest.TestCase):
    def _base(self):
        return _df([
            {"pubchem_xlogp": 6.0, "chembl_oral": True},
            {"pubchem_xlogp": 6.0, "chembl_oral": False},
            {"pubchem_xlogp": 1.0, "chembl_oral": True},
            {"pubchem_xlogp": 1.0, "chembl_oral": False},
        ])

    def test_all_of_is_logical_and(self):
        spec = {"op": "all_of", "params": {"terms": [XLOGP, ORAL]}}
        out = F.compute(self._base(), spec)
        self.assertEqual(list(out), [1.0, 0.0, 0.0, 0.0])

    def test_any_of_is_logical_or(self):
        spec = {"op": "any_of", "params": {"terms": [XLOGP, ORAL]}}
        out = F.compute(self._base(), spec)
        self.assertEqual(list(out), [1.0, 1.0, 1.0, 0.0])

    def test_not_op_negates(self):
        spec = {"op": "not_op", "params": {"term": ORAL}}
        out = F.compute(self._base(), spec)
        self.assertEqual(list(out), [0.0, 1.0, 0.0, 1.0])

    def test_nested_composition(self):
        # lipophilic AND NOT oral
        spec = {
            "op": "all_of",
            "params": {"terms": [XLOGP, {"op": "not_op", "params": {"term": ORAL}}]},
        }
        out = F.compute(self._base(), spec)
        self.assertEqual(list(out), [0.0, 1.0, 0.0, 0.0])

    def test_missing_data_propagates_and_does_not_become_false(self):
        """An unobserved XLogP must not read as 'not lipophilic' under negation."""
        df = _df([
            {"pubchem_xlogp": 6.0, "chembl_oral": True},
            {"pubchem_xlogp": None, "chembl_oral": True},
        ])
        out = F.compute(df, {"op": "not_op", "params": {"term": XLOGP}})
        self.assertEqual(out.iloc[0], 0.0)
        self.assertTrue(math.isnan(out.iloc[1]), "missing XLogP must stay missing")

    def test_missing_propagates_through_all_of(self):
        df = _df([{"pubchem_xlogp": None, "chembl_oral": True}])
        out = F.compute(df, {"op": "all_of", "params": {"terms": [XLOGP, ORAL]}})
        self.assertTrue(math.isnan(out.iloc[0]))


class TestCompositionValidation(unittest.TestCase):
    def test_valid_composition_supported(self):
        self.assertTrue(F.is_supported({"op": "all_of", "params": {"terms": [XLOGP, ORAL]}}))

    def test_rejects_single_term(self):
        self.assertFalse(F.is_supported({"op": "all_of", "params": {"terms": [XLOGP]}}))

    def test_rejects_too_many_terms(self):
        terms = [XLOGP, ORAL, SMALL, ESTAB, {"op": "prc_threshold", "params": {"k": 1}}]
        self.assertGreater(len(terms), F.MAX_COMPOSITION_TERMS)
        self.assertFalse(F.is_supported({"op": "all_of", "params": {"terms": terms}}))

    def test_rejects_duplicate_terms(self):
        """'X AND X' is just X, and would evade feature-level dedup."""
        self.assertFalse(F.is_supported({"op": "all_of", "params": {"terms": [XLOGP, XLOGP]}}))

    def test_rejects_continuous_term(self):
        self.assertFalse(
            F.is_supported({"op": "all_of", "params": {"terms": [{"op": "mw_raw"}, ORAL]}})
        )

    def test_rejects_excessive_nesting_depth(self):
        spec = ORAL
        for _ in range(F.MAX_COMPOSITION_DEPTH + 2):
            spec = {"op": "not_op", "params": {"term": spec}}
        self.assertFalse(F.is_supported(spec))

    def test_malformed_composition_not_waved_through_on_op_name(self):
        self.assertFalse(F.is_supported({"op": "all_of"}))
        self.assertFalse(F.is_supported({"op": "all_of", "params": {"terms": "nope"}}))

    def test_composition_usable_as_moderator(self):
        spec = {
            "op": "interaction",
            "params": {
                "base": {"op": "mw_raw"},
                "moderator": {"op": "all_of", "params": {"terms": [XLOGP, ORAL]}},
            },
        }
        self.assertTrue(F.is_supported(spec))


class TestGuardrailRecursion(unittest.TestCase):
    def test_confounded_term_cannot_hide_inside_composition(self):
        spec = {
            "op": "all_of",
            "params": {"terms": [ORAL, {"op": "prc_threshold", "params": {"k": 1}}]},
        }
        self.assertTrue(F.is_confounded(spec))

    def test_confounded_term_cannot_hide_inside_nested_composition(self):
        spec = {
            "op": "all_of",
            "params": {
                "terms": [
                    ORAL,
                    {"op": "not_op", "params": {"term": {"op": "prc_raw"}}},
                ]
            },
        }
        self.assertTrue(F.is_confounded(spec))

    def test_stage_proxy_cannot_hide_inside_composition(self):
        spec = {
            "op": "all_of",
            "params": {
                "terms": [
                    ORAL,
                    {"op": "ind_keyword", "params": {"keywords": ["refractory"]}},
                ]
            },
        }
        self.assertTrue(F.is_indication_stage_proxy(spec))

    def test_stage_proxy_recurses_into_interaction3(self):
        spec = {
            "op": "interaction3",
            "params": {
                "base": {"op": "mw_raw"},
                "moderator": ORAL,
                "moderator2": {"op": "ind_keyword", "params": {"keywords": ["relapsed"]}},
            },
        }
        self.assertTrue(F.is_indication_stage_proxy(spec))

    def test_clean_composition_passes_guardrails(self):
        spec = {"op": "all_of", "params": {"terms": [XLOGP, ORAL]}}
        self.assertFalse(F.is_confounded(spec))
        self.assertFalse(F.is_indication_stage_proxy(spec))


class TestPredictorKindRouting(unittest.TestCase):
    """
    Regression: predictor_kind() returned 'binary' for every op except prc_raw,
    so continuous columns were sent to Fisher's exact, which casts to int and
    reindexes the 2x2 table to [0, 1] — zeroing every cell. A production
    hypothesis on mw_raw logged p=1 in both framings for exactly this reason.
    """

    def test_continuous_ops_route_to_logistic(self):
        for op in ("prc_raw", "mw_raw", "xlogp_raw", "global_max_phase_raw"):
            self.assertEqual(F.predictor_kind({"op": op}), "continuous", op)

    def test_binary_ops_still_binary(self):
        for spec in (XLOGP, ORAL, SMALL, ESTAB):
            self.assertEqual(F.predictor_kind(spec), "binary", spec)

    def test_composition_is_binary(self):
        self.assertEqual(
            F.predictor_kind({"op": "all_of", "params": {"terms": [XLOGP, ORAL]}}), "binary"
        )

    def test_interaction_kinds(self):
        self.assertEqual(F.predictor_kind({"op": "interaction"}), "interaction")
        self.assertEqual(F.predictor_kind({"op": "interaction3"}), "interaction3")


class TestInteraction3Validation(unittest.TestCase):
    def _spec(self, base, m1, m2):
        return {
            "op": "interaction3",
            "params": {"base": base, "moderator": m1, "moderator2": m2},
        }

    def test_valid(self):
        self.assertTrue(F.is_supported(self._spec({"op": "mw_raw"}, ORAL, SMALL)))

    def test_rejects_identical_moderators(self):
        self.assertFalse(F.is_supported(self._spec({"op": "mw_raw"}, ORAL, ORAL)))

    def test_rejects_base_equal_to_moderator(self):
        self.assertFalse(F.is_supported(self._spec(ORAL, ORAL, SMALL)))

    def test_rejects_continuous_moderator(self):
        self.assertFalse(F.is_supported(self._spec({"op": "mw_raw"}, {"op": "xlogp_raw"}, ORAL)))

    def test_missing_moderator2_rejected(self):
        self.assertFalse(
            F.is_supported(
                {"op": "interaction3", "params": {"base": {"op": "mw_raw"}, "moderator": ORAL}}
            )
        )


class TestPowerGuards(unittest.TestCase):
    def test_epp_rejects_underpowered(self):
        # 51 minority events — the real narrow-framing discovery count.
        y = pd.Series([1] * 2862 + [0] * 51)
        ok, why = F.events_per_parameter_ok(y, F._N_PARAMS["interaction3"])
        self.assertFalse(ok)
        self.assertIn("insufficient power", why)

    def test_epp_accepts_two_way_on_narrow_counts(self):
        y = pd.Series([1] * 2862 + [0] * 51)
        ok, _ = F.events_per_parameter_ok(y, F._N_PARAMS["interaction"])
        self.assertTrue(ok, "2-way (4 params) must remain testable on 51 events")

    def test_epp_accepts_broad_counts_for_three_way(self):
        y = pd.Series([1] * 2862 + [0] * 1341)
        ok, _ = F.events_per_parameter_ok(y, F._N_PARAMS["interaction3"])
        self.assertTrue(ok)

    def test_epp_requires_two_classes(self):
        ok, why = F.events_per_parameter_ok(pd.Series([1] * 500), 4)
        self.assertFalse(ok)
        self.assertIn("<2 classes", why)

    def test_composite_support_rejects_tiny_subgroup(self):
        feat = pd.Series([1] * 3 + [0] * 500)
        y = pd.Series([1] * 250 + [0] * 253)
        ok, why = F.composite_support_ok(feat, y)
        self.assertFalse(ok)
        self.assertIn("too small", why)

    def test_composite_support_accepts_adequate_subgroup(self):
        feat = pd.Series([1] * 40 + [0] * 500)
        y = pd.Series([1] * 270 + [0] * 270)
        ok, _ = F.composite_support_ok(feat, y)
        self.assertTrue(ok)


class TestLogisticInteraction3(unittest.TestCase):
    def test_recovers_a_planted_three_way_effect(self):
        """base hurts under m1 ONLY when m2=1 — the exact 'but not when F' shape."""
        rng = np.random.default_rng(11)
        n = 12000
        b = rng.integers(0, 2, n)
        m1 = rng.integers(0, 2, n)
        m2 = rng.integers(0, 2, n)
        # three-way term is the only structured signal
        logit = -0.2 + 0.1 * b + 0.1 * m1 + 0.1 * m2 - 2.5 * (b * m1 * m2)
        y = rng.binomial(1, 1 / (1 + np.exp(-logit)))
        res = S.logistic_interaction3(
            pd.Series(b), pd.Series(m1), pd.Series(m2), pd.Series(y)
        )
        self.assertLess(res.p_value, 1e-6)
        self.assertLess(res.odds_ratio, 1.0, "planted effect was negative")
        self.assertEqual(res.test_type, "logistic_interaction3")

    def test_null_three_way_is_not_significant(self):
        rng = np.random.default_rng(7)
        n = 6000
        b = rng.integers(0, 2, n)
        m1 = rng.integers(0, 2, n)
        m2 = rng.integers(0, 2, n)
        y = rng.binomial(1, 0.5, n)
        res = S.logistic_interaction3(
            pd.Series(b), pd.Series(m1), pd.Series(m2), pd.Series(y)
        )
        self.assertGreater(res.p_value, 0.01)


class TestConfirmationFDR(unittest.TestCase):
    """
    The confirmation stage must be BH-corrected over its cumulative family.
    Without it, chaining batches until a confirmation lands is optional
    stopping and a false confirmation is eventually guaranteed.
    """

    def _hist(self, pvals, test_ids=None):
        return pd.DataFrame({
            "test_id": test_ids or [f"T{i}" for i in range(len(pvals))],
            "confirmation_raw_p": pvals,
        })

    def test_q_exceeds_raw_p(self):
        # 0.02 ranks 2nd of 4 -> q = 0.02 * 4/2 = 0.04. (The single LARGEST p in a
        # BH family always keeps q == p, so it cannot be used for this check.)
        with mock.patch.object(R, "load_history", return_value=self._hist([0.01, 0.03, 0.04])):
            q = R.confirmation_q_for([("NEW", 0.02)])
        self.assertGreater(q[0], 0.02)
        self.assertAlmostEqual(q[0], 0.04)

    def test_marginal_p_fails_once_family_is_large(self):
        """A p just under 0.05 must NOT confirm after many prior attempts."""
        prior = [0.4 + i * 0.01 for i in range(60)]
        with mock.patch.object(R, "load_history", return_value=self._hist(prior)):
            q = R.confirmation_q_for([("NEW", 0.049)])
        self.assertGreaterEqual(q[0], R.CONFIRMATION_ALPHA)

    def test_strong_effect_still_confirms(self):
        """The real oncology finding (p=5.3e-14) must survive the correction."""
        prior = [0.4 + i * 0.01 for i in range(60)]
        with mock.patch.object(R, "load_history", return_value=self._hist(prior)):
            q = R.confirmation_q_for([("NEW", 5.33e-14)])
        self.assertLess(q[0], R.CONFIRMATION_ALPHA)

    def test_rerun_does_not_double_count_itself(self):
        """Re-testing an existing test_id must not put that row in the family twice."""
        hist = self._hist([0.01, 0.02], test_ids=["T0", "T1"])
        with mock.patch.object(R, "load_history", return_value=hist):
            prior_rerun = R._confirmation_prior_pvalues({"T0"})
            prior_fresh = R._confirmation_prior_pvalues({"NEW"})
        self.assertEqual(prior_rerun, [0.02], "the row being re-tested must be excluded")
        self.assertEqual(sorted(prior_fresh), [0.01, 0.02])
        self.assertEqual(len(prior_rerun) + 1, 2, "family size stays at the true count")

    def test_empty_pending_returns_empty(self):
        self.assertEqual(R.confirmation_q_for([]), [])

    def test_non_finite_prior_values_ignored(self):
        hist = self._hist([0.01, None, float("nan")])
        with mock.patch.object(R, "load_history", return_value=hist):
            q = R.confirmation_q_for([("NEW", 0.02)])
        self.assertEqual(len(q), 1)
        self.assertTrue(math.isfinite(q[0]))


class TestConfirmationStatusIsAuthoritativeAtReadTime(unittest.TestCase):
    """
    A cumulative correction is only honest if consumers read the RECOMPUTED
    status. The stored confirmation_pass is the verdict against the family as it
    stood at test time; the family grows with every later attempt, so a stored
    True must be able to go stale — exactly as stored discovery q-values do.
    """

    def setUp(self):
        import hypothesis_report as HR
        self.HR = HR

    def _hist(self):
        return pd.DataFrame([{
            "hypothesis_id": "H99",
            "test_id": "T99",
            "run_id": "",
            "outcome_framing": "narrow",
            "feature_spec": '{"op": "is_oral"}',
            "discovery_test_type": "fisher_exact",
            "discovery_raw_p": 0.001,
            "confirmation_raw_p": 0.04,
            "confirmation_pass": True,  # stale: passed when the family was small
            "outcome_note": "READY: x",
            "confound_check_summary": "",
            "novelty_tag": "NOVEL",
        }])

    def _collect(self, cqmap):
        with mock.patch.object(self.HR.R, "migrate_registries"), \
             mock.patch.object(self.HR.R, "load_history_full", return_value=self._hist()), \
             mock.patch.object(
                 self.HR.R, "cumulative_fdr",
                 return_value=pd.DataFrame([{"test_id": "T99", "fdr_q": 0.001}])), \
             mock.patch.object(self.HR.R, "confirmation_qmap", return_value=cqmap):
            return self.HR.collect_facts("H99")

    def test_stale_pass_flips_to_fail_once_family_grows(self):
        facts = self._collect({"T99": 0.30})
        fr = facts["framings"][0]
        self.assertFalse(fr["confirmation_pass"], "must reflect the CURRENT family")
        self.assertTrue(fr["confirmation_pass_at_test_time"], "provenance retained")
        self.assertFalse(facts["passed_both"])

    def test_genuine_pass_survives(self):
        facts = self._collect({"T99": 0.001})
        self.assertTrue(facts["framings"][0]["confirmation_pass"])
        self.assertTrue(facts["passed_both"])

    def test_absent_q_is_not_a_pass(self):
        """No confirmation q -> untested/unconfirmed, never inherited from storage."""
        facts = self._collect({})
        self.assertFalse(facts["framings"][0]["confirmation_pass"])
        self.assertFalse(facts["passed_both"])

    def test_dossier_reports_not_confirmed_for_a_stale_pass(self):
        """The audit verdict, not just the raw fact, must follow the recompute."""
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "api"))
        import dossier as D
        facts = self._collect({"T99": 0.30})
        status, reasons = D.audit_status_for(facts, ["READY"])
        self.assertEqual(status, D._STATUS_NOT_CONFIRMED)
        self.assertTrue(any("NOT confirmed" in r for r in reasons))


class TestContinuousLoopStopSemantics(unittest.TestCase):
    """
    'Run until double pass' must not quit at a soft budget with nothing found,
    and must never report such a run as completed.
    """

    def setUp(self):
        import run_discovery as RD
        self.RD = RD

    def _summary(self, confirmed=0, domains=("d",), hyps=5):
        return {
            "domains": list(domains),
            "hypotheses_reviewed": hyps,
            "tests_run": hyps * 2,
            "confirmed": confirmed,
        }

    def test_keeps_going_past_soft_budget(self):
        calls = {"n": 0}

        def fake_batch(run_id=None):
            calls["n"] += 1
            # confirm only well after the soft budget would have stopped it
            confirmed = 1 if calls["n"] == 8 else 0
            return self._summary(confirmed=confirmed, domains=(f"d{calls['n']}",), hyps=10)

        with mock.patch.object(self.RD, "run_batch", side_effect=fake_batch):
            out = self.RD.run_continuous_batch(
                stop_flag={}, max_domains=2, max_hypotheses=10
            )
        self.assertEqual(out["stopped_reason"], "double_pass_achieved")
        self.assertTrue(out["double_pass_achieved"])
        self.assertTrue(out["soft_budget_exceeded"])
        self.assertEqual(calls["n"], 8)

    def test_hard_bound_reports_failure_not_completion(self):
        with mock.patch.object(self.RD, "run_batch", return_value=self._summary()):
            out = self.RD.run_continuous_batch(
                stop_flag={}, max_domains=1, max_hypotheses=1, hard_max_batches=3
            )
        self.assertEqual(out["batches_run"], 3)
        self.assertEqual(out["stopped_reason"], "hard_batch_limit")
        self.assertFalse(out["double_pass_achieved"])

    def test_stops_immediately_on_double_pass(self):
        with mock.patch.object(self.RD, "run_batch", return_value=self._summary(confirmed=1)):
            out = self.RD.run_continuous_batch(stop_flag={})
        self.assertEqual(out["batches_run"], 1)
        self.assertEqual(out["stopped_reason"], "double_pass_achieved")

    def test_user_stop_is_reported(self):
        flag = {"stop": False}

        def fake_batch(run_id=None):
            flag["stop"] = True
            return self._summary()

        with mock.patch.object(self.RD, "run_batch", side_effect=fake_batch):
            out = self.RD.run_continuous_batch(stop_flag=flag)
        self.assertEqual(out["stopped_reason"], "stopped_by_user")
        self.assertTrue(out["stopped_by_user"])
        self.assertFalse(out["double_pass_achieved"])

    def test_transient_failure_does_not_end_the_search(self):
        calls = {"n": 0}

        def flaky(run_id=None):
            calls["n"] += 1
            if calls["n"] in (1, 3):
                raise RuntimeError("upstream 503")
            if calls["n"] >= 5:
                return self._summary(confirmed=1)
            return self._summary()

        with mock.patch.object(self.RD, "run_batch", side_effect=flaky):
            out = self.RD.run_continuous_batch(stop_flag={})
        self.assertEqual(out["stopped_reason"], "double_pass_achieved")
        self.assertEqual(len(out["batch_errors"]), 2)

    def test_persistent_failure_stops_with_reason(self):
        with mock.patch.object(self.RD, "run_batch", side_effect=RuntimeError("dead")):
            out = self.RD.run_continuous_batch(stop_flag={})
        self.assertEqual(out["stopped_reason"], "repeated_batch_failures")
        self.assertFalse(out["double_pass_achieved"])
        self.assertEqual(len(out["batch_errors"]), self.RD.MAX_CONSECUTIVE_FAILURES)

    def test_time_bound_reports_failure(self):
        with mock.patch.object(self.RD, "run_batch", return_value=self._summary()):
            out = self.RD.run_continuous_batch(stop_flag={}, hard_max_seconds=-1)
        self.assertEqual(out["stopped_reason"], "time_limit")
        self.assertEqual(out["batches_run"], 0)


if __name__ == "__main__":
    unittest.main()
