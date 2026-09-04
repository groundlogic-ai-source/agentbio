"""Regression coverage for retryable degraded terminal outcomes."""

import json
import os
import tempfile
import unittest
from unittest import mock

from api import audit
from main_graph import eligibility_gate_node


class TestDegradedEligibility(unittest.TestCase):
    def test_failed_candidate_source_is_not_reported_as_genuine_empty_pool(self):
        verdict = eligibility_gate_node({
            "repurposing_only": True,
            "reviewed": {"candidates": [], "repurposing_only": True},
            "chemist_output": {
                "source_status": {
                    "chembl": {"status": "failed", "error": "503"},
                    "gtopdb": {"status": "empty", "error": None},
                },
            },
        })["eligibility"]
        self.assertEqual(verdict["terminal_status"], "source_unavailable")
        self.assertTrue(verdict["retryable"])
        self.assertEqual(verdict["source_failures"][0]["source"], "chembl")

    def test_all_search_failed_shortlist_is_degraded_not_no_eligible(self):
        candidates = [
            {
                "drug_name": name,
                "is_approved_drug": True,
                "literature_limitation_blocked": False,
                "literature_limitation_gate_cleared": False,
                "literature_limitation": {
                    "verdict": "SEARCH_FAILED",
                    "source_status": "CLASSIFIER_INTEGRITY_FAILED",
                    "reason": "classifier unavailable",
                },
            }
            for name in ("a", "b")
        ]
        verdict = eligibility_gate_node({
            "repurposing_only": True,
            "reviewed": {"candidates": candidates, "repurposing_only": True},
            "chemist_output": {"source_status": {"chembl": {"status": "ok"}}},
        })["eligibility"]
        self.assertEqual(verdict["terminal_status"], "degraded_unscorable")
        self.assertTrue(verdict["retryable"])
        self.assertEqual(len(verdict["classifier_failures"]), 2)

    def test_partial_source_failure_cannot_produce_no_eligible_conclusion(self):
        candidate = {
            "drug_name": "observed-but-not-approved",
            "is_approved_drug": False,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
        }
        verdict = eligibility_gate_node({
            "repurposing_only": True,
            "reviewed": {"candidates": [candidate], "repurposing_only": True},
            "chemist_output": {
                "source_status": {
                    "chembl": {"status": "ok"},
                    "drugcentral": {"status": "unavailable", "error": "timeout"},
                },
            },
        })["eligibility"]
        self.assertEqual(verdict["terminal_status"], "source_unavailable")
        self.assertTrue(verdict["retryable"])
        self.assertIn("could change eligibility", verdict["terminal_reason"])

    def test_all_healthy_empty_sources_remain_genuine_no_eligible_outcome(self):
        verdict = eligibility_gate_node({
            "reviewed": {"candidates": []},
            "chemist_output": {
                "source_status": {
                    "chembl": {"status": "empty"},
                    "gtopdb": {"status": "empty"},
                },
            },
        })["eligibility"]
        self.assertEqual(verdict["terminal_status"], "no_eligible_candidate")
        self.assertFalse(verdict["retryable"])


class TestPerJobReviewerSnapshot(unittest.TestCase):
    def test_snapshot_is_written_to_database(self):
        payload = {"candidates": [{"drug_name": "durable"}]}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(audit.jobs_db, "save_candidate_snapshot") as save, \
                mock.patch.object(audit, "_CANDIDATES_DIR", directory):
            self.assertTrue(audit.save_job_candidates("job-db", payload))
        save.assert_called_once_with("job-db", payload)

    def test_snapshot_uses_supplied_reviewer_payload_not_shared_artifact(self):
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(audit.jobs_db, "save_candidate_snapshot"), \
                mock.patch.object(audit, "_CANDIDATES_DIR", directory):
            payload = {"candidates": [{"drug_name": "reviewed-now"}]}
            self.assertTrue(audit.save_job_candidates("job-1", payload))
            with open(os.path.join(directory, "job-1.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), payload)

    def test_file_success_does_not_mask_database_snapshot_failure(self):
        payload = {"candidates": [{"drug_name": "ephemeral-only"}]}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(
                    audit.jobs_db,
                    "save_candidate_snapshot",
                    side_effect=RuntimeError("database unavailable"),
                ), \
                mock.patch.object(audit, "_CANDIDATES_DIR", directory):
            self.assertFalse(audit.save_job_candidates("job-no-db", payload))
            self.assertTrue(os.path.exists(
                os.path.join(directory, "job-no-db.json")))

    def test_explicit_job_snapshot_loads_before_file_fallback(self):
        payload = {
            "safety_schema_version": audit.SAFETY_SCHEMA_VERSION,
            "candidates": [{"drug_name": "database-candidate"}],
        }
        with mock.patch.object(
                audit.jobs_db, "get_candidate_snapshot", return_value=payload):
            candidates = audit._load_candidates("job-db", "different disease")
        self.assertEqual(candidates, payload["candidates"])
