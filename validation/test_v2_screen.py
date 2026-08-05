"""Offline unit tests for the v2 Amendment-1 screen and the runner's
cross-freeze contamination guard. All source lookups are mocked."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import screen_v2_cases as sc  # noqa: E402
from validation import run_benchmark as rb  # noqa: E402

CASE = {"drug_name": "DrugX", "ind_name": "Some Rare Disease",
        "efo_id": "MONDO_1", "stratum": "rare"}


def _targets(n=1):
    return [{"target_symbol": f"GENE{i}", "uniprot_id": f"P0000{i}",
             "association_score": 0.5} for i in range(n)]


def _patch_ot(search="MONDO_1", canonical="some rare disease",
              descendants=2, targets=None, healthy=True):
    return mock.patch.multiple(
        sc,
        search_disease_efo=mock.Mock(return_value=search),
        get_ot_canonical_disease_name=mock.Mock(return_value=canonical),
        get_disease_descendant_count=mock.Mock(return_value=descendants),
        get_target_disease_score=mock.Mock(
            return_value=_targets() if targets is None else targets),
        ot_healthy=mock.Mock(return_value=healthy),
    )


class ScreenCaseTest(unittest.TestCase):
    def test_pass_when_pool_non_empty(self):
        with _patch_ot(), mock.patch.object(
                sc, "collect_target_candidates",
                return_value={"candidates": [{"name": "DrugX"}]}):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual(verdict, "pass")
        self.assertIn("GENE0", reason)

    def test_umbrella_excluded(self):
        with _patch_ot(descendants=500):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual((verdict, reason.split(" ")[0]),
                         ("exclude", "umbrella_term"))

    def test_name_mismatch_excluded(self):
        with _patch_ot(canonical="completely unrelated words here"):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual(verdict, "exclude")
        self.assertIn("name_mismatch", reason)

    def test_resolution_drift_excluded(self):
        with _patch_ot(search="EFO_999"):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual(verdict, "exclude")
        self.assertIn("resolution_mismatch", reason)

    def test_no_genetic_target_excluded(self):
        with _patch_ot(targets=[]):
            verdict, _ = sc.screen_case(CASE)
        self.assertEqual(verdict, "exclude")

    def test_below_gate_target_not_considered(self):
        weak = [{"target_symbol": "WEAK", "uniprot_id": "P9",
                 "association_score": 0.05}]
        with _patch_ot(targets=weak):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual((verdict, reason), ("exclude", "no_ot_genetic_target"))

    def test_empty_pool_excluded(self):
        with _patch_ot(), mock.patch.object(
                sc, "collect_target_candidates",
                return_value={"candidates": []}):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual((verdict, reason), ("exclude", "empty_union_pool"))

    def test_pool_failure_is_unavailable_not_absence(self):
        with _patch_ot(), mock.patch.object(
                sc, "collect_target_candidates",
                side_effect=RuntimeError("boom")):
            with self.assertRaises(sc.ScreenDataUnavailable):
                sc.screen_case(CASE)


class ScreenOutageDisciplineTest(unittest.TestCase):
    """OT helpers swallow transport errors and return None/[]; the screen must
    treat those as unavailable — not absence — whenever OT probes unhealthy."""

    def test_unresolved_during_outage_is_unavailable(self):
        with _patch_ot(search=None, healthy=False):
            with self.assertRaises(sc.ScreenDataUnavailable):
                sc.screen_case(CASE)

    def test_unresolved_when_healthy_is_genuine_exclusion(self):
        with _patch_ot(search=None, healthy=True):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual((verdict, reason), ("exclude", "efo_unresolved"))

    def test_empty_targets_during_outage_is_unavailable(self):
        with _patch_ot(targets=[], healthy=False):
            with self.assertRaises(sc.ScreenDataUnavailable):
                sc.screen_case(CASE)

    def test_provider_unavailable_is_not_empty_pool(self):
        pool = {"candidates": [],
                "source_status": {"drugcentral": {"status": "unavailable"}}}
        with _patch_ot(), mock.patch.object(
                sc, "collect_target_candidates", return_value=pool):
            with self.assertRaises(sc.ScreenDataUnavailable):
                sc.screen_case(CASE)

    def test_provider_ok_statuses_allow_empty_pool_exclusion(self):
        pool = {"candidates": [],
                "source_status": {"gtopdb": {"status": "ok"},
                                  "chembl": {"status": "empty"},
                                  "drugcentral": {"status": "disabled"}}}
        with _patch_ot(), mock.patch.object(
                sc, "collect_target_candidates", return_value=pool):
            verdict, reason = sc.screen_case(CASE)
        self.assertEqual((verdict, reason), ("exclude", "empty_union_pool"))

    def test_metadata_gap_with_healthy_probe_is_unavailable(self):
        with _patch_ot(descendants=None, healthy=True):
            with self.assertRaises(sc.ScreenDataUnavailable):
                sc.screen_case(CASE)


class ScreenMainTest(unittest.TestCase):
    def test_indeterminate_exit3_no_partial_list(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "src.json")
            out = os.path.join(td, "out.json")
            with open(src, "w") as f:
                json.dump({"primary": [CASE]}, f)
            with _patch_ot(), mock.patch.object(
                    sc, "collect_target_candidates",
                    side_effect=RuntimeError("boom")), \
                    mock.patch.object(sc, "_chembl_healthy", return_value=True), \
                    mock.patch.object(sc, "ot_healthy", return_value=True), \
                    mock.patch.object(sc, "SOURCE_LIST", src), \
                    mock.patch.object(sc, "OUT_JSON", out):
                rc = sc.main()
            self.assertEqual(rc, 3)
            self.assertFalse(os.path.exists(out))

    def test_unhealthy_sources_exit3(self):
        with mock.patch.object(sc, "_chembl_healthy", return_value=False), \
                mock.patch.object(sc, "ot_healthy", return_value=True):
            self.assertEqual(sc.main(), 3)


class RunnerContaminationGuardTest(unittest.TestCase):
    def test_cross_freeze_resume_refused(self):
        with tempfile.TemporaryDirectory() as td:
            results = os.path.join(td, "results.json")
            with open(results, "w") as f:
                json.dump({"freeze_tag": "benchmark-freeze-v1", "cases": []}, f)
            with mock.patch.object(rb, "RESULTS_JSON", results):
                with self.assertRaises(SystemExit) as ctx:
                    rb._load_done()
            self.assertEqual(ctx.exception.code, 2)

    def test_head_not_at_tag_refused(self):
        def fake_run(args, **kwargs):
            out = mock.Mock(returncode=0)
            out.stdout = "aaaa1111\n" if args[-1] == "HEAD" else "bbbb2222\n"
            return out
        with mock.patch.object(rb.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(SystemExit) as ctx:
                rb._check_freeze_integrity()
        self.assertEqual(ctx.exception.code, 2)

    def test_preflight_tag_not_at_head_refused(self):
        from validation import run_v2_preflight as pf

        def fake_run(args, **kwargs):
            out = mock.Mock(returncode=0)
            out.stdout = "aaaa1111\n" if args[-1] == "HEAD" else "bbbb2222\n"
            return out
        with mock.patch.object(pf.os.path, "exists", return_value=True), \
                mock.patch.object(pf, "_healthy", return_value=True), \
                mock.patch.object(pf.subprocess, "run", side_effect=fake_run):
            self.assertEqual(pf.main(), 2)


if __name__ == "__main__":
    unittest.main()
