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
from validation import run_v2_preflight as pf  # noqa: E402

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
        def fake_run(args, **kwargs):
            out = mock.Mock(returncode=0)
            out.stdout = "aaaa1111\n" if args[-1] == "HEAD" else "bbbb2222\n"
            return out
        with mock.patch.object(pf, "_valid_ablation_results",
                               return_value=(True, "complete")), \
                mock.patch.object(pf.os.path, "exists", return_value=True), \
                mock.patch.object(pf, "_healthy", return_value=True), \
                mock.patch.object(pf.subprocess, "run", side_effect=fake_run):
            self.assertEqual(pf.main(), 2)


class ChEMBLHealthProbeTest(unittest.TestCase):
    def test_filtered_molecule_route_failure_is_unhealthy(self):
        healthy = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={"molecules": [{"molecule_chembl_id": "CHEMBL192"}]}),
        )
        broken = mock.Mock(status_code=500, json=mock.Mock(return_value={}))
        with mock.patch.object(rb.requests, "get",
                               side_effect=[healthy, broken]):
            self.assertFalse(rb._chembl_healthy())

    def test_all_required_molecule_routes_must_be_healthy(self):
        list_response = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={"molecules": [{"molecule_chembl_id": "CHEMBL192"}]}),
        )
        detail_response = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={"molecule_chembl_id": "CHEMBL192"}))
        with mock.patch.object(
                rb.requests, "get",
                side_effect=[list_response, list_response, detail_response]):
            self.assertTrue(rb._chembl_healthy())


class AblationControlIntegrityTest(unittest.TestCase):
    def _payload(self):
        from validation import run_v2_source_ablations as ablation
        conditions = tuple(ablation.CONDITIONS)
        cases = [(drug, disease) for _, drug, disease, _ in ablation.TARGET_CASES]
        snapshots, rows = [], []
        for drug, disease in cases:
            selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
            snap = {
                "case_key": ablation._case_key(drug, disease),
                "drug_name": drug, "disease_name": disease,
                "cap": ablation.DEFAULT_TARGET_CAP, "status": "ok",
                "in_universe": True, "selected_rows": selected,
                "target_input_hash": ablation.target_input_hash(
                    disease, ablation.DEFAULT_TARGET_CAP, selected),
            }
            snapshots.append(snap)
            for condition in conditions:
                enabled = ablation.CONDITIONS[condition]
                rows.append({
                    "condition": condition, "drug_name": drug,
                    "disease_name": disease, "status": "miss",
                    "in_universe": True,
                    "target_input_hash": snap["target_input_hash"],
                    "error": None,
                    "per_target_results": [{
                        "target_symbol": "TGT", "uniprot_id": "P1",
                        "status": "ok", "error": None,
                        "source_status": {
                            provider: {
                                "status": ("ok" if provider in enabled
                                           else "disabled"),
                                "error": None, "release": None,
                            }
                            for provider in pf.ABLATION_PROVIDERS
                        },
                    }],
                })
        return {
            "label": ablation.LABEL,
            "target_cap": ablation.DEFAULT_TARGET_CAP,
            "conditions": {c: list(ablation.CONDITIONS[c]) for c in conditions},
            "fingerprint": ablation.config_source_fingerprint(
                ablation.DEFAULT_TARGET_CAP, conditions),
            "target_snapshots": snapshots, "rows": rows,
        }

    def test_complete_error_free_control_is_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            with open(path, "w") as f:
                json.dump(self._payload(), f)
            self.assertTrue(pf._valid_ablation_results(path)[0])

    def test_error_row_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["status"] = "error"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("non-terminal control row", reason)

    def test_missing_row_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"].pop()
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("incomplete", reason)

    def test_failed_snapshot_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["target_snapshots"][0]["status"] = "error"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("failed target-selection snapshot", reason)

    def test_degraded_target_execution_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["per_target_results"][0]["status"] = "error"
            payload["rows"][0]["per_target_results"][0]["error"] = "boom"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("degraded target execution", reason)

    def test_unavailable_enabled_source_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            statuses = payload["rows"][0]["per_target_results"][0]["source_status"]
            statuses["chembl"] = {"status": "unavailable", "error": "500",
                                  "release": None}
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("degraded source chembl", reason)

    def test_ablated_source_still_answering_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            row = next(r for r in payload["rows"]
                       if r["condition"] == "chembl_only")
            row["per_target_results"][0]["source_status"]["gtopdb"] = {
                "status": "ok", "error": None, "release": None}
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("ablated source gtopdb not disabled", reason)

    def test_missing_per_target_results_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["per_target_results"] = []
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("incomplete per-target results", reason)

    def test_row_error_context_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["error"] = "final pooled reviewer failed"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("degraded control row", reason)

    def test_unknown_row_status_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["status"] = "running"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("non-terminal control row", reason)

    def test_out_of_scope_snapshot_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["target_snapshots"][0].update(
                status="out_of_scope", in_universe=False)
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("failed target-selection snapshot", reason)

    def test_duplicate_snapshot_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["target_snapshots"].append(
                dict(payload["target_snapshots"][0]))
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("duplicate", reason)

    def test_non_dict_snapshot_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["target_snapshots"][0] = "not-a-snapshot"
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("malformed", reason)

    def test_missing_snapshot_case_key_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            del payload["target_snapshots"][0]["case_key"]
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("case key", reason)

    def test_wrong_condition_sources_reject_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["conditions"]["all_three"] = ["chembl"]
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("unexpected sources", reason)

    def test_malformed_condition_mapping_rejects_control(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["conditions"] = ["chembl_only"]
            with open(path, "w") as f:
                json.dump(payload, f)
            ok, reason = pf._valid_ablation_results(path)
            self.assertFalse(ok)
            self.assertIn("malformed condition mapping", reason)

    def test_degraded_ablation_source_blocks_before_running_control(self):
        # A doomed control run must never be paid for: if GtoPdb/DrugCentral
        # are down, every enabled arm would be rejected afterwards anyway.
        with mock.patch.object(pf, "_ablation_sources_healthy",
                               return_value=False), \
                mock.patch("validation.run_benchmark._chembl_healthy",
                           return_value=True), \
                mock.patch("validation.screen_v2_cases.ot_healthy",
                           return_value=True), \
                mock.patch.object(pf, "_run_module") as run_module:
            self.assertEqual(pf.main(), 4)
        run_module.assert_not_called()

    def test_repeated_discards_stop_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            attempts = os.path.join(td, "attempts")
            payload = self._payload()
            payload["rows"][0]["status"] = "error"
            with open(path, "w") as f:
                json.dump(payload, f)
            with mock.patch.object(pf, "ABLATION_RESULTS", path), \
                    mock.patch.object(pf, "ATTEMPTS_PATH", attempts), \
                    mock.patch.object(pf, "MAX_CONTROL_DISCARDS", 2):
                # Row-level failure: quarantine strips the degraded row and
                # keeps the healthy rows instead of deleting the control.
                self.assertEqual(pf._discard_control("bad", "on disk"), 3)
                self.assertTrue(os.path.exists(path))
                with open(path) as f:
                    self.assertEqual(len(json.load(f)["rows"]),
                                     len(payload["rows"]) - 1)
                with open(path, "w") as f:
                    json.dump(payload, f)
                # Budget exhausted: stop for a human rather than re-running an
                # expensive control against a persistently degraded source.
                self.assertEqual(pf._discard_control("bad", "on disk"), 2)

    def test_valid_control_clears_discard_budget(self):
        with tempfile.TemporaryDirectory() as td:
            attempts = os.path.join(td, "attempts")
            with open(attempts, "w") as f:
                f.write("2")
            with mock.patch.object(pf, "ATTEMPTS_PATH", attempts):
                self.assertEqual(pf._discard_attempts(), 2)
                pf._clear_discards()
                self.assertEqual(pf._discard_attempts(), 0)

    def test_preflight_resumes_incomplete_checkpoint_without_discarding(self):
        # The control harness flushes after every completed arm.  That
        # checkpoint is invalid for final freeze validation, but it is the
        # correct resume point—not a degraded artifact to delete.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            with open(path, "w") as f:
                json.dump(self._payload(), f)
            with mock.patch.object(pf, "ABLATION_RESULTS", path), \
                    mock.patch.object(pf, "_healthy", return_value=True), \
                    mock.patch.object(
                        pf, "_valid_ablation_results",
                        side_effect=[
                            (False, "incomplete or duplicate target snapshots"),
                            (True, "ok"),
                        ],
                    ), \
                    mock.patch.object(pf, "_run_module", return_value=0) as run, \
                    mock.patch.object(pf, "_clear_discards"):
                self.assertEqual(pf.main(), 0)
        self.assertEqual(
            run.call_args_list[0],
            mock.call("validation.run_v2_source_ablations"),
        )

    def test_preflight_discards_invalid_control_and_retries(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            payload = self._payload()
            payload["rows"][0]["status"] = "error"
            with open(path, "w") as f:
                json.dump(payload, f)
            validate_control = pf._valid_ablation_results
            with mock.patch.object(pf, "_valid_ablation_results",
                                   side_effect=lambda: validate_control(path)), \
                    mock.patch.object(pf, "ABLATION_RESULTS", path), \
                    mock.patch.object(pf, "_healthy", return_value=True), \
                    mock.patch.object(pf, "_run_module") as run_module:
                self.assertEqual(pf.main(), 3)
            run_module.assert_not_called()
            # Quarantine keeps the healthy rows; only the degraded row is
            # removed (resume re-runs it instead of repeating all arms).
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                self.assertEqual(len(json.load(f)["rows"]),
                                 len(payload["rows"]) - 1)

    def test_structural_failure_still_discards_whole_control(self):
        # Fingerprint drift means no row can be trusted: whole-artifact
        # discard (no quarantine) still applies.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            attempts = os.path.join(td, "attempts")
            payload = self._payload()
            payload["fingerprint"] = "stale"
            with open(path, "w") as f:
                json.dump(payload, f)
            with mock.patch.object(pf, "ABLATION_RESULTS", path), \
                    mock.patch.object(pf, "ATTEMPTS_PATH", attempts):
                self.assertEqual(
                    pf._discard_control("stale control fingerprint",
                                        "on disk"), 3)
                self.assertFalse(os.path.exists(path))

    def test_preflight_resumes_row_incomplete_checkpoint(self):
        # The state a quarantine leaves behind: all present rows valid, some
        # arms missing.  That is a resume point, not a discard.
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "control.json")
            with open(path, "w") as f:
                json.dump(self._payload(), f)
            with mock.patch.object(pf, "ABLATION_RESULTS", path), \
                    mock.patch.object(pf, "_healthy", return_value=True), \
                    mock.patch.object(
                        pf, "_valid_ablation_results",
                        side_effect=[
                            (False, "incomplete or unexpected control rows"),
                            (True, "ok"),
                        ]), \
                    mock.patch.object(pf, "_run_module",
                                      return_value=0) as run, \
                    mock.patch.object(pf, "_clear_discards"):
                self.assertEqual(pf.main(), 0)
        self.assertEqual(run.call_args_list[0],
                         mock.call("validation.run_v2_source_ablations"))


if __name__ == "__main__":
    unittest.main()
