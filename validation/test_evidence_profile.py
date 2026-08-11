"""Guards for the pre-registered evidence-profile scoring rule.

The profile is the discrimination study's scoring function. Two failure
modes would quietly invalidate the study, so both are pinned here:

* **Treating "unresolved" as a negative.** An audit layer that cannot
  tell looks decisive if unresolved collapses into flagged. That is the
  exact error audit claim-set v1 made, and it manufactures a false
  disqualification rate on real repurposings.
* **Scoring coverage gaps as judgments.** A drug the pipeline never
  generated was never assessed; calling that a disqualification would
  measure recall, not audit quality.

The rule fingerprint is also pinned: the freeze binds it, so a silent
edit to the thresholds after seeing results must break a test.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation.evidence_profile import (  # noqa: E402
    DISQUALIFIED, NOT_ASSESSABLE, QUALIFIED, RULE_FINGERPRINT, SUPPORTED,
    build_profile,
)


def _audit(status="found", *, cand=None, findings=(), rank=3, total=40):
    return {
        "status": status,
        "rank": rank,
        "total_candidates": total,
        "candidate": cand if cand is not None else {
            "composite_score": 0.7, "pre_cap_score": 0.7,
            "score_components": {"evidence_weight_coverage": 1.0},
            "pubchem_xlogp": 2.0, "nonoral_biologic_flag": False,
        },
        "audit_context": {
            "findings": [{"code": c, "status": s, "title": "", "rationale": "",
                          "evidence": []} for c, s in findings],
            "holdout_redaction": {"applied": True},
        },
    }


class UnresolvedIsNotNegative(unittest.TestCase):
    def test_all_unresolved_findings_do_not_disqualify(self):
        profile = build_profile("X", _audit(findings=[
            ("N2", "unresolved"), ("N3", "unresolved"), ("N4", "unresolved")]))
        self.assertEqual(profile["hard_disqualifiers_fired"], [])
        self.assertEqual(profile["disposition"], SUPPORTED)
        self.assertEqual(
            profile["dimensions"]["human_mechanism_evidence"], "UNRESOLVED")

    def test_unresolved_xlogp_is_not_a_caution(self):
        profile = build_profile("X", _audit(cand={
            "score_components": {"evidence_weight_coverage": 1.0},
            "pubchem_xlogp": None, "nonoral_biologic_flag": False}))
        self.assertEqual(profile["dimensions"]["lipophilicity"], "UNRESOLVED")
        self.assertEqual(profile["disposition"], SUPPORTED)


class CoverageGapsAreNotJudgments(unittest.TestCase):
    def test_absent_from_pool_is_not_disqualified(self):
        profile = build_profile("X", _audit("absent"))
        self.assertEqual(
            profile["dimensions"]["pool_presence"], "ABSENT_FROM_POOL")
        self.assertEqual(profile["hard_disqualifiers_fired"], [])
        self.assertNotEqual(profile["disposition"], DISQUALIFIED)

    def test_no_case_and_unresolved_name_are_not_assessable(self):
        for status in ("no_case", "unresolved"):
            with self.subTest(status=status):
                self.assertEqual(
                    build_profile("X", _audit(status))["disposition"],
                    NOT_ASSESSABLE)


class HardDisqualifiersFire(unittest.TestCase):
    def test_mechanism_cap_disqualifies(self):
        profile = build_profile("X", _audit(cand={
            "mechanism_cap_applied": True,
            "score_components": {"evidence_weight_coverage": 1.0},
            "pubchem_xlogp": 2.0, "nonoral_biologic_flag": False}))
        self.assertEqual(profile["disposition"], DISQUALIFIED)
        self.assertIn("mechanism_direction=INCOMPATIBLE",
                      profile["hard_disqualifiers_fired"])

    def test_preclinical_only_evidence_disqualifies(self):
        profile = build_profile("X", _audit(findings=[("N3", "flagged")]))
        self.assertEqual(profile["disposition"], DISQUALIFIED)

    def test_safety_cap_disqualifies_but_black_box_only_qualifies(self):
        base = {"score_components": {"evidence_weight_coverage": 1.0},
                "pubchem_xlogp": 2.0, "nonoral_biologic_flag": False}
        capped = build_profile("X", _audit(
            cand={**base, "safety_cap_applied": True}))
        self.assertEqual(capped["disposition"], DISQUALIFIED)
        # Most approved drugs carry a black box; disqualifying on it alone
        # would fail real repurposings by construction.
        advisory = build_profile("X", _audit(
            cand={**base, "black_box_advisory": True}))
        self.assertEqual(advisory["disposition"], QUALIFIED)


class PrimaryIgnoresPool(unittest.TestCase):
    """The self-critique found the pool is not disease-blind (reviewer.py
    derives composite/rank from disease-linked OT/trial data and
    check_mechanism_direction takes the disease). The PRIMARY metric must
    therefore never read a pool-derived dimension. These guards pin that."""

    def test_dimension_split_is_complete_and_disjoint(self):
        from validation.evidence_profile import (
            DISEASE_DEPENDENT_DIMS, DISEASE_INDEPENDENT_DIMS)
        self.assertFalse(DISEASE_INDEPENDENT_DIMS & DISEASE_DEPENDENT_DIMS)
        # The genuinely mechanical, pool-free dimensions must be the primary set.
        self.assertEqual(DISEASE_INDEPENDENT_DIMS, {
            "modality_feasibility", "route_feasibility",
            "human_mechanism_evidence", "lipophilicity"})
        # The pool-derived dimensions must be quarantined as descriptive-only.
        self.assertIn("rank", DISEASE_DEPENDENT_DIMS)
        self.assertIn("mechanism_direction", DISEASE_DEPENDENT_DIMS)
        self.assertIn("safety", DISEASE_DEPENDENT_DIMS)

    def test_primary_disposition_needs_no_pool(self):
        # A disease-dependent hard disqualifier (mechanism cap) must NOT fire
        # the primary disposition, because that dimension is not provably blind.
        profile = build_profile("X", _audit(cand={
            "mechanism_cap_applied": True,
            "safety_cap_applied": True,
            "score_components": {"evidence_weight_coverage": 1.0},
            "pubchem_xlogp": 2.0, "nonoral_biologic_flag": False}))
        self.assertEqual(profile["disposition"], DISQUALIFIED)  # overall
        self.assertEqual(profile["primary_disposition"], SUPPORTED)  # primary
        self.assertEqual(profile["primary_hard_fired"], [])

    def test_primary_disposition_fires_on_disease_independent_flag(self):
        profile = build_profile("X", _audit(findings=[("N3", "flagged")]))
        self.assertEqual(profile["primary_disposition"], DISQUALIFIED)
        self.assertIn("human_mechanism_evidence=PRECLINICAL_ONLY",
                      profile["primary_hard_fired"])

    def test_primary_disposition_available_even_without_case(self):
        # no_case makes the overall disposition NOT_ASSESSABLE, but the primary
        # claim never touches the pool, so it must still be scored.
        profile = build_profile("X", _audit("no_case"))
        self.assertEqual(profile["disposition"], NOT_ASSESSABLE)
        self.assertIn(profile["primary_disposition"],
                      {SUPPORTED, QUALIFIED, DISQUALIFIED})


class RuleIsPinned(unittest.TestCase):
    def test_fingerprint_is_stable(self):
        """If this fails, the scored rule changed. That is only legitimate
        before the freeze -- update the constant deliberately, never to
        make a run look better."""
        self.assertEqual(
            RULE_FINGERPRINT,
            # Amendment 1 (2026-08-11): route_feasibility=FLAGGED surfaced and
            # added to SOFT_CAUTIONS after NC2 caught the dropout post-freeze.
            # Original frozen fingerprint: cf9bb3b9…f40d9f. Study A results
            # remain bound to the original; the change is unreachable under
            # Study A's claim-free cohort.
            "c600f834faf889d8f9dd97eaff0d0fbcf4cb96e07828bfacad69168619d260bf")


if __name__ == "__main__":
    unittest.main()
