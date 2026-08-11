"""Offline guards for the triage discrimination study machinery.

These tests never touch the network: they pin case-set determinism, the
dev-suite exclusion, cohort disjointness, control-class integrity, the Wilson
math, and the runner's fail-closed / freeze-verification refusals.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import build_triage_discrimination_cases as builder  # noqa: E402
from validation import run_triage_discrimination as runner  # noqa: E402


class CaseSetBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.caseset = builder.build()

    def test_build_is_deterministic(self):
        again = builder.build()
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(self.caseset, sort_keys=True))

    def test_cohort_sizes_and_disjointness(self):
        coh = self.caseset["cohorts"]
        a = {c["drug_name"].casefold() for c in coh["cohort_a"]["cases"]}
        n1 = {c["drug_name"].casefold()
              for c in coh["nc1_modality_contradiction"]["cases"]}
        n2 = {c["drug_name"].casefold()
              for c in coh["nc2_route_contradiction"]["cases"]}
        self.assertEqual(len(a), 200)
        self.assertFalse(a & n1)
        self.assertFalse(a & n2)
        self.assertFalse(n1 & n2)

    def test_dev_suite_drugs_excluded(self):
        dev = builder._dev_suite_drugs()
        self.assertTrue(dev, "dev-suite list must be non-empty")
        for coh in self.caseset["cohorts"].values():
            for case in coh["cases"]:
                self.assertNotIn(builder._norm(case["drug_name"]), dev)

    def test_control_class_integrity(self):
        coh = self.caseset["cohorts"]
        for case in coh["nc1_modality_contradiction"]["cases"]:
            self.assertIn(case["sel_molecule_type"].lower(),
                          builder._BIOLOGIC_TYPES)
        for case in coh["nc2_route_contradiction"]["cases"]:
            self.assertEqual(case["sel_oral"], "0.0")

    def test_cohort_a_unit_is_distinct_drug(self):
        names = [c["drug_name"].casefold()
                 for c in self.caseset["cohorts"]["cohort_a"]["cases"]]
        self.assertEqual(len(names), len(set(names)))


class WilsonMath(unittest.TestCase):
    def test_zero_events_bounds(self):
        _, hi200 = runner._wilson(0, 200)
        _, hi60 = runner._wilson(0, 60)
        _, hi6 = runner._wilson(0, 6)
        self.assertAlmostEqual(hi200, 0.018, places=2)
        self.assertAlmostEqual(hi60, 0.059, places=2)
        self.assertAlmostEqual(hi6, 0.39, places=2)
        self.assertLess(hi200, hi60)
        self.assertLess(hi60, hi6)

    def test_empty_denominator(self):
        self.assertEqual(runner._wilson(0, 0), (0.0, 1.0))


class RunnerRefusals(unittest.TestCase):
    def test_refuses_to_rescore_when_results_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_results = Path(tmp) / "results.json"
            fake_results.write_text("{}")
            with mock.patch.object(runner, "RESULTS_PATH", fake_results):
                with mock.patch.object(sys, "argv", ["prog"]):
                    with self.assertRaises(SystemExit):
                        runner.main()

    def test_freeze_verification_rejects_case_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cases = tmp / "cases.json"
            prereg = tmp / "prereg.md"
            cases.write_text("{}")
            prereg.write_text("x")
            manifest = tmp / "manifest.json"
            manifest.write_text(json.dumps({
                "cases_sha256": "0" * 64,  # wrong on purpose
                "preregistration_sha256": runner._sha256_file(prereg),
                "rule_fingerprint": runner.RULE_FINGERPRINT,
                "redaction_contract": runner.REDACTION_CONTRACT,
            }))
            with mock.patch.object(runner, "CASES_PATH", cases), \
                 mock.patch.object(runner, "PREREG_PATH", prereg), \
                 mock.patch.object(runner, "MANIFEST_PATH", manifest):
                with self.assertRaises(SystemExit):
                    runner._verify_freeze()


if __name__ == "__main__":
    unittest.main()
