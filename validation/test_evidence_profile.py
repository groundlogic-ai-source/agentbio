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


class RuleIsPinned(unittest.TestCase):
    def test_fingerprint_is_stable(self):
        """If this fails, the scored rule changed. That is only legitimate
        before the freeze -- update the constant deliberately, never to
        make a run look better."""
        self.assertEqual(
            RULE_FINGERPRINT,
            "cf9bb3b9401ed89a5cf8e925ff7dcaee06a5d4e70f677960632a0dd0c7f40d9f")


if __name__ == "__main__":
    unittest.main()
