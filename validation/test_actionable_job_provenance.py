"""Fail-closed tests for actionable API dossier provenance."""

import os
import json
import hashlib
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from unittest import mock

from fastapi import HTTPException

from api import main
from api import jobs_db
import main_graph
from api.policy_contracts import (
    DECISION_CONTRACT_VERSION,
    LITERATURE_SCHEMA_VERSION,
    REPORT_CONTRACT_VERSION,
    REVIEWER_FORMULA_VERSION,
    SAFETY_SCHEMA_VERSION,
    sha256_json,
)


def current_snapshot() -> dict:
    return {
        "formula": {"formula_version": REVIEWER_FORMULA_VERSION},
        "safety_schema_version": SAFETY_SCHEMA_VERSION,
        "literature_schema_version": LITERATURE_SCHEMA_VERSION,
        "report_contract_version": REPORT_CONTRACT_VERSION,
        "reviewer_input_fingerprint": "fingerprint-1",
        "source_coverage_complete": True,
        "source_failure_details": [],
        "candidates": [{
            "drug_name": "Current candidate",
            "candidate_source_coverage": {"complete": True, "failures": []},
            "dossier_evidence_contract": {
                "contract_version": REPORT_CONTRACT_VERSION,
            },
        }],
    }


class ActionableJobProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root_patch = mock.patch.object(
            main, "_JOB_REPORT_ROOT", self.tmp.name)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.snapshot = current_snapshot()
        snapshot_hash = sha256_json(self.snapshot)
        self.path = main._job_report_path("job-current")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        report_bytes = (
            "<!-- AgentBio immutable decision provenance -->\n"
            f"{main._snapshot_binding_line(snapshot_hash)}\n\n"
            "# Current report\n"
        ).encode("utf-8")
        with open(self.path, "wb") as fh:
            fh.write(report_bytes)
        self.job = {
            "job_id": "job-current",
            "thread_id": "thread-current",
            "disease_name": "Current disease",
            "status": "awaiting_review",
            "report_path": self.path,
            "decision_contract_version": DECISION_CONTRACT_VERSION,
            "report_contract_version": REPORT_CONTRACT_VERSION,
            "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "candidate_snapshot_sha256": snapshot_hash,
            "reviewer_input_fingerprint": "fingerprint-1",
        }

    def _resume(self, job, snapshot):
        with mock.patch.object(main.jobs_db, "get_job", return_value=job), \
             mock.patch.object(main.jobs_db, "get_candidate_snapshot",
                               return_value=snapshot), \
             mock.patch.object(main, "resume_run",
                               return_value={"decision": "approve"}) as resume, \
             mock.patch.object(main.jobs_db, "claim_job_for_review",
                               return_value=job), \
             mock.patch.object(main.jobs_db, "update_job_status",
                               return_value=job):
            result = main.resume(
                job["job_id"], main.ResumeRequest(action="approve", notes="ok"))
        return result, resume

    def test_current_complete_job_can_approve(self):
        result, resume = self._resume(self.job, self.snapshot)
        self.assertEqual(result["action"], "approve")
        resume.assert_called_once()

    def test_legacy_shared_report_and_null_metadata_cannot_approve(self):
        legacy = {
            **self.job,
            "report_path": os.path.join(self.tmp.name, "shared-report.md"),
            "decision_contract_version": None,
            "report_contract_version": None,
            "report_sha256": None,
            "candidate_snapshot_sha256": None,
            "reviewer_input_fingerprint": None,
        }
        with self.assertRaises(HTTPException) as caught:
            self._resume(legacy, None)
        self.assertEqual(caught.exception.status_code, 409)

    def test_tampered_report_hash_cannot_approve(self):
        tampered = {**self.job, "report_sha256": "0" * 64}
        with self.assertRaises(HTTPException) as caught:
            self._resume(tampered, self.snapshot)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("report hash", str(caught.exception.detail))

    def test_report_binding_mismatch_cannot_approve(self):
        with open(self.path, "wb") as fh:
            fh.write(
                b"<!-- AgentBio immutable decision provenance -->\n"
                b"Candidate snapshot SHA-256: `wrong`\n\n# Current report\n"
            )
        with open(self.path, "rb") as fh:
            rebound = {**self.job,
                       "report_sha256": hashlib.sha256(fh.read()).hexdigest()}
        with self.assertRaises(HTTPException) as caught:
            self._resume(rebound, self.snapshot)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("exact candidate snapshot binding",
                      str(caught.exception.detail))

    def test_missing_snapshot_cannot_approve(self):
        with self.assertRaises(HTTPException) as caught:
            self._resume(self.job, None)
        self.assertEqual(caught.exception.status_code, 409)

    def test_get_and_pdf_disclose_stale_banner(self):
        stale = {
            **self.job,
            "decision_contract_version": None,
            "report_contract_version": None,
        }
        with mock.patch.object(main.jobs_db, "get_job", return_value=stale), \
             mock.patch.object(main.jobs_db, "get_candidate_snapshot",
                               return_value=self.snapshot):
            result = main.get_run(stale["job_id"])
        self.assertFalse(result["actionable"])
        self.assertIn("Superseded policy snapshot", result["stale_policy"])

        captured = {}

        def render(report, _job):
            captured["report"] = report
            return b"%PDF-test"

        with mock.patch.object(main.jobs_db, "get_job", return_value=stale), \
             mock.patch.object(main.jobs_db, "get_candidate_snapshot",
                               return_value=self.snapshot), \
             mock.patch.object(main, "render_case_pdf", side_effect=render):
            main.download_case_report_pdf(stale["job_id"])
        self.assertIn("SUPERSEDED POLICY SNAPSHOT", captured["report"])
        self.assertIn("cannot be approved", captured["report"])


class SeedQuarantineTests(unittest.TestCase):
    def test_legacy_awaiting_review_seed_is_archived(self):
        inserts = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                if "INSERT INTO jobs" in sql:
                    inserts.append(params)

            def fetchone(self):
                return (0,)

        class Connection:
            def cursor(self):
                return Cursor()

        with tempfile.TemporaryDirectory() as tmp:
            seed = os.path.join(tmp, "seed.json")
            with open(seed, "w", encoding="utf-8") as fh:
                json.dump({
                    "jobs": [{
                        "job_id": "legacy-review",
                        "thread_id": "legacy-thread",
                        "disease_name": "Cantú syndrome",
                        "status": "awaiting_review",
                    }],
                    "explored_targets": [],
                }, fh)
            with mock.patch.object(jobs_db, "_SEED_FILE", seed), \
                 mock.patch.object(
                     jobs_db, "_conn",
                     return_value=mock.MagicMock(
                         __enter__=mock.Mock(return_value=Connection()),
                         __exit__=mock.Mock(return_value=False),
                     )):
                jobs_db._seed_if_empty()
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0]["archived"], 1)
        self.assertIsNone(inserts[0]["decision_contract_version"])


class SnapshotImmutabilityTests(unittest.TestCase):
    @staticmethod
    def _db_context(job_row, snapshot_row, executed):
        class Cursor:
            results = [job_row, snapshot_row]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchone(self):
                return self.results.pop(0)

        class Connection:
            def cursor(self):
                return Cursor()

        @contextmanager
        def connection(*_args, **_kwargs):
            yield Connection()

        return connection

    def test_post_report_snapshot_overwrite_is_rejected(self):
        old = {"candidates": [{"drug_name": "old"}]}
        old_hash = sha256_json(old)
        executed = []
        fake = self._db_context(("report-hash", old_hash), (old,), executed)
        with mock.patch.object(jobs_db, "_conn", side_effect=fake):
            with self.assertRaisesRegex(ValueError, "immutable"):
                jobs_db.save_candidate_snapshot(
                    "job-final", {"candidates": [{"drug_name": "new"}]})
        self.assertFalse(any("INSERT INTO job_candidate_snapshots" in sql
                             for sql, _ in executed))

    def test_post_report_identical_snapshot_retry_is_idempotent(self):
        payload = {"candidates": [{"drug_name": "same"}]}
        payload_hash = sha256_json(payload)
        executed = []
        fake = self._db_context(
            ("report-hash", payload_hash), (payload,), executed)
        with mock.patch.object(jobs_db, "_conn", side_effect=fake):
            jobs_db.save_candidate_snapshot("job-final", payload)
        self.assertFalse(any("INSERT INTO job_candidate_snapshots" in sql
                             for sql, _ in executed))


class JobsDbClaimTests(unittest.TestCase):
    def test_claim_is_single_conditional_update_returning_row(self):
        executed = []

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchone(self):
                return {"job_id": "job-claim", "status": "reviewing"}

        class Connection:
            def cursor(self, **_kwargs):
                return Cursor()

        @contextmanager
        def connection(*_args, **_kwargs):
            yield Connection()

        with mock.patch.object(jobs_db, "_conn", side_effect=connection):
            claimed = jobs_db.claim_job_for_review("job-claim")
        self.assertEqual(claimed["status"], "reviewing")
        self.assertEqual(len(executed), 1)
        sql = " ".join(executed[0][0].split())
        self.assertIn(
            "WHERE job_id = %s AND status = 'awaiting_review' RETURNING *",
            sql,
        )


class ResumeConcurrencyTests(unittest.TestCase):
    def test_reviewing_is_a_valid_in_progress_status(self):
        self.assertIn("reviewing", jobs_db.VALID_STATUSES)

    def test_opposite_decisions_have_exactly_one_atomic_winner(self):
        job = {
            "job_id": "job-race",
            "thread_id": "thread-race",
            "status": "awaiting_review",
        }
        barrier = threading.Barrier(2)
        claim_lock = threading.Lock()
        claimed = {"value": False}
        durable_decisions = []

        def actionability(_job):
            barrier.wait(timeout=5)
            return {"actionable": True, "stale_policy": None,
                    "stale_reasons": []}

        def claim(_job_id):
            with claim_lock:
                if claimed["value"]:
                    return None
                claimed["value"] = True
                return {**job, "status": "reviewing"}

        def update(_job_id, **fields):
            if fields.get("decision"):
                durable_decisions.append(fields["decision"])
            return {**job, **fields}

        resume_calls = []

        def graph_resume(_thread_id, action, _notes):
            resume_calls.append(action)
            return {"decision": action}

        def invoke(action):
            try:
                return main.resume(
                    job["job_id"],
                    main.ResumeRequest(action=action, notes=f"{action} note"),
                )
            except HTTPException as exc:
                return exc

        with mock.patch.object(main.jobs_db, "get_job", return_value=job), \
             mock.patch.object(main, "_actionability",
                               side_effect=actionability), \
             mock.patch.object(main.jobs_db, "claim_job_for_review",
                               side_effect=claim), \
             mock.patch.object(main, "resume_run", side_effect=graph_resume), \
             mock.patch.object(main.jobs_db, "update_job_status",
                               side_effect=update):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(invoke, ("approve", "reject")))

        self.assertEqual(len(resume_calls), 1)
        self.assertEqual(durable_decisions, resume_calls)
        losers = [result for result in results
                  if isinstance(result, HTTPException)]
        self.assertEqual(len(losers), 1)
        self.assertEqual(losers[0].status_code, 409)

    def test_resume_failure_is_terminal_with_attempt_context(self):
        job = {
            "job_id": "job-failed-review",
            "thread_id": "thread-failed-review",
            "status": "awaiting_review",
        }
        with mock.patch.object(main.jobs_db, "get_job", return_value=job), \
             mock.patch.object(
                 main, "_actionability",
                 return_value={"actionable": True, "stale_policy": None,
                               "stale_reasons": []}), \
             mock.patch.object(
                 main.jobs_db, "claim_job_for_review",
                 return_value={**job, "status": "reviewing"}), \
             mock.patch.object(
                 main, "resume_run", side_effect=RuntimeError("checkpoint")), \
             mock.patch.object(main.jobs_db, "update_job_status") as update:
            with self.assertRaises(HTTPException) as caught:
                main.resume(
                    job["job_id"],
                    main.ResumeRequest(action="reject", notes="reason"),
                )
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(update.call_args.kwargs["status"], "error")
        self.assertIn("'reject'", update.call_args.kwargs["error_message"])
        self.assertIn("not restored", update.call_args.kwargs["error_message"])


class MultiCandidateApiIntegrationTests(unittest.TestCase):
    def test_api_binds_only_top_ranked_candidate_and_one_report(self):
        candidates = []
        for rank, name in enumerate(("Top", "Second", "Third"), start=1):
            candidates.append({
                "drug_name": name,
                "disease_name": "Three candidate disease",
                "target_symbol": "TARGET",
                "uniprot_id": "P00001",
                "smiles": "CC",
                "rank": rank,
                "strong_match": True,
                "literature_limitation_blocked": False,
                "literature_limitation_gate_cleared": True,
                "paid_validation_eligible": True,
                "headline_eligible": True,
                "candidate_source_coverage": {
                    "complete": True, "failures": []},
                "dossier_evidence_contract": {
                    "contract_version": REPORT_CONTRACT_VERSION},
            })
        reviewed = {
            "formula": {"formula_version": REVIEWER_FORMULA_VERSION},
            "safety_schema_version": SAFETY_SCHEMA_VERSION,
            "literature_schema_version": LITERATURE_SCHEMA_VERSION,
            "report_contract_version": REPORT_CONTRACT_VERSION,
            "reviewer_input_fingerprint": "three-fingerprint",
            "source_coverage_complete": True,
            "source_failure_details": [],
            "repurposing_only": True,
            "candidates": candidates,
            "excluded_candidates": [{"drug_name": "Excluded"}],
        }
        state = {
            "job_id": "job-three",
            "reviewed": reviewed,
            "targets": [{"target_symbol": "TARGET", "uniprot_id": "P00001"}],
            "target": {"target_symbol": "TARGET", "uniprot_id": "P00001"},
            "biologist_output": {
                "target": {"target_symbol": "TARGET", "uniprot_id": "P00001"}},
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(main_graph, "REPO_ROOT", tmp), \
             mock.patch.object(main_graph, "get_structure_confidence",
                               return_value={}), \
             mock.patch.object(main_graph, "get_protein_sequence",
                               return_value="SEQUENCE"), \
             mock.patch.object(
                 main_graph.boltz_api, "predict_complex",
                 return_value={"available": True}), \
             mock.patch.object(
                 main_graph.boltz_api, "predict_adme", return_value={}), \
             mock.patch.object(main_graph, "_write_json"), \
             mock.patch.object(
                 main_graph.writer, "build_report_markdown",
                 return_value="# Top dossier\n"), \
             mock.patch.object(
                 main, "_JOB_REPORT_ROOT",
                 os.path.join(tmp, "output", "job_reports")):
            structure = main_graph.structure_validation_node(state)
            self.assertEqual(
                [row["drug_name"] for row in structure["selected"]], ["Top"])
            reports = main_graph.writer_node({
                **state,
                **structure,
            })["reports"]
            self.assertEqual(len(reports), 1)
            all_reports = []
            for root, _dirs, files in os.walk(
                    os.path.join(tmp, "output", "job_reports")):
                all_reports.extend(
                    os.path.join(root, name) for name in files
                    if name.endswith(".md"))
            self.assertEqual(all_reports, [reports[0]["path"]])

            with mock.patch.object(
                main.jobs_db, "get_candidate_snapshot",
                return_value=reviewed,
            ), mock.patch.object(
                main.jobs_db, "finalize_job_report"
            ) as finalize:
                metadata = main._persist_actionable_report(
                    "job-three", reports[0]["path"])
            finalize.assert_called_once()
            self.assertEqual(metadata["report_path"], reports[0]["path"])
            with open(metadata["report_path"], encoding="utf-8") as fh:
                report_text = fh.read()
            self.assertIn("Candidate snapshot SHA-256", report_text)
            self.assertIn("# Top dossier", report_text)
            self.assertNotIn("Second dossier", report_text)

            job = {
                "job_id": "job-three",
                "thread_id": "thread-three",
                "status": "awaiting_review",
                **metadata,
            }
            with mock.patch.object(main.jobs_db, "get_job", return_value=job), \
                 mock.patch.object(
                     main.jobs_db, "get_candidate_snapshot",
                     return_value=reviewed), \
                 mock.patch.object(
                     main.jobs_db, "claim_job_for_review",
                     return_value={**job, "status": "reviewing"}), \
                 mock.patch.object(
                     main.jobs_db, "update_job_status", return_value=job), \
                 mock.patch.object(
                     main, "resume_run",
                     return_value={"reviewed_reports": [metadata["report_path"]]}
                 ) as resume:
                result = main.resume(
                    "job-three",
                    main.ResumeRequest(action="approve", notes="top only"))
            resume.assert_called_once()
            self.assertEqual(
                result["review"]["reviewed_reports"],
                [metadata["report_path"]],
            )


class ReviewRecoveryTests(unittest.TestCase):
    def test_startup_reaps_reviewing_as_uncertain_error(self):
        executed = []

        class Cursor:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, params=None):
                executed.append((sql, params))
                self.rowcount = 2 if "status='running'" in sql else 1

        class Connection:
            def cursor(self):
                return Cursor()

        @contextmanager
        def connection(*_args, **_kwargs):
            yield Connection()

        with mock.patch.object(jobs_db, "_conn", side_effect=connection):
            count = jobs_db.reap_orphaned_running_jobs()
        self.assertEqual(count, 3)
        reviewing_sql, reviewing_params = executed[1]
        self.assertIn("WHERE status='reviewing'", reviewing_sql)
        self.assertIn("decision/checkpoint may have had side effects",
                      reviewing_params[0])
        self.assertIn("Attempt context", reviewing_params[0])
        self.assertNotIn("awaiting_review'", reviewing_sql)


if __name__ == "__main__":
    unittest.main()