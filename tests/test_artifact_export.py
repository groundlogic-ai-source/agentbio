import hashlib
import io
import json
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

from api import main
from api import jobs_db
from api.policy_contracts import (
    DECISION_CONTRACT_VERSION, LITERATURE_SCHEMA_VERSION,
    REPORT_CONTRACT_VERSION, REVIEWER_FORMULA_VERSION, SAFETY_SCHEMA_VERSION,
    canonical_json_bytes,
)


def artifact(kind, payload, content_type, report_sha, snapshot_sha, filename):
    return {
        "kind": kind, "payload": payload, "filename": filename,
        "content_type": content_type, "size_bytes": len(payload),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_id": hashlib.sha256(payload).hexdigest(),
        "report_sha256": report_sha,
        "candidate_snapshot_sha256": snapshot_sha,
    }


class StructureRewriteTests(unittest.TestCase):
    def test_all_transient_references_are_consumed(self):
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(main, "_STRUCTURES_DIR", root):
            payload = b"data_test\n_atom_site.id 1\n"
            Path(root, "ok.cif").write_bytes(payload)
            Path(root, "not.txt").write_bytes(b"x")
            os.symlink(os.path.join(root, "ok.cif"), os.path.join(root, "link.cif"))
            original = (
                "[CIF](/api/structures/ok.cif) /api/structures/ok.cif "
                "/api/structures/missing.cif /api/structures/link.cif "
                "/api/structures/../../secret /api/structures/not.txt "
                "[absolute](https://old.example/api/structures/ok.cif?token=x) "
                "[Structure](https://bucket.s3.amazonaws.com/a.cif?"
                "X-Amz-Signature=SECRET&X-Amz-Credential=REDACTED"
            )
            rewritten, cifs = main._rewrite_structure_artifacts(original, "job1")
        digest = hashlib.sha256(payload).hexdigest()
        permanent = (
            "https://agentbio.groundlogic.ai/api/runs/job1/artifacts/cif/" + digest)
        self.assertEqual(len(cifs), 1)
        self.assertIn(permanent, rewritten)
        self.assertNotIn("/api/structures/", rewritten)
        for secret in ("old.example", "amazonaws.com", "X-Amz", "SECRET",
                       "TOKEN", "?token", "../../", "missing.cif", "link.cif"):
            self.assertNotIn(secret, rewritten)


class EvidenceZipTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"candidates": [{"drug_name": "x"}]}
        self.snapshot_sha = hashlib.sha256(
            canonical_json_bytes(self.snapshot)).hexdigest()
        self.report = b"# frozen\n"
        self.report_sha = hashlib.sha256(self.report).hexdigest()
        self.pdf = b"%PDF-frozen"
        self.cif_bytes = b"data_x\n"
        self.cif = artifact("cif", self.cif_bytes, "chemical/x-cif",
                            self.report_sha, self.snapshot_sha, "x.cif")
        self.job = {"job_id": "job-z", "report_sha256": self.report_sha,
                    "candidate_snapshot_sha256": self.snapshot_sha}

    def bundle(self):
        return main._deterministic_evidence_zip(
            "job-z", self.report, self.pdf, self.snapshot, [self.cif],
            self.report_sha, self.snapshot_sha)

    def authorities(self, bundle):
        return (
            artifact("report_md", self.report, "text/markdown; charset=utf-8",
                     self.report_sha, self.snapshot_sha, "report.md"),
            artifact("report_pdf", self.pdf, "application/pdf",
                     self.report_sha, self.snapshot_sha, "report.pdf"),
            artifact("evidence_zip", bundle, "application/zip",
                     self.report_sha, self.snapshot_sha, "evidence.zip"),
        )

    def test_deterministic_and_deep_valid(self):
        first, second = self.bundle(), self.bundle()
        self.assertEqual(first, second)
        report, pdf, evidence = self.authorities(first)
        main._validate_evidence_zip(
            self.job, self.snapshot, report, pdf, evidence, [self.cif])
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(
                archive.namelist(),
                ["report.md", "report.pdf", "candidate_snapshot.json",
                 "cif/x.cif", "manifest.json"])
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["report_sha256"], self.report_sha)
        self.assertEqual(manifest["candidate_snapshot_sha256"], self.snapshot_sha)

    def test_each_authoritative_entry_tamper_is_rejected(self):
        bundle = self.bundle()
        report, pdf, evidence = self.authorities(bundle)
        for target in ("report", "pdf", "snapshot", "cif"):
            args = [self.job, self.snapshot, report, pdf, evidence, [self.cif]]
            if target == "report":
                args[2] = {**report, "payload": b"changed"}
            elif target == "pdf":
                args[3] = {**pdf, "payload": b"changed"}
            elif target == "snapshot":
                args[1] = {"changed": True}
            else:
                args[5] = [{**self.cif, "payload": b"changed"}]
            with self.subTest(target=target), self.assertRaises(ValueError):
                main._validate_evidence_zip(*args)

    def test_unsafe_duplicate_and_traversal_entries_rejected(self):
        base = self.bundle()
        report, pdf, _ = self.authorities(base)
        for bad_name in ("report.md", "../escape", r"cif\\escape"):
            out = io.BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(out, "w") as archive:
                    with zipfile.ZipFile(io.BytesIO(base)) as source:
                        for name in source.namelist():
                            archive.writestr(name, source.read(name))
                    archive.writestr(bad_name, b"bad")
            evidence = artifact("evidence_zip", out.getvalue(), "application/zip",
                                self.report_sha, self.snapshot_sha, "evidence.zip")
            with self.subTest(name=bad_name), self.assertRaises(ValueError):
                main._validate_evidence_zip(
                    self.job, self.snapshot, report, pdf, evidence, [self.cif])

    def test_duplicate_manifest_declarations_rejected(self):
        base = self.bundle()
        for mutation in ("file", "cif_id", "cif_filename"):
            out = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(base)) as source, \
                    zipfile.ZipFile(out, "w") as target:
                manifest = json.loads(source.read("manifest.json"))
                if mutation == "file":
                    manifest["files"].append(dict(manifest["files"][0]))
                else:
                    duplicate = dict(manifest["cifs"][0])
                    if mutation == "cif_filename":
                        duplicate["artifact_id"] = "f" * 64
                    manifest["cifs"].append(duplicate)
                for name in source.namelist():
                    value = (canonical_json_bytes(manifest) if name == "manifest.json"
                             else source.read(name))
                    target.writestr(name, value)
            report, pdf, _ = self.authorities(base)
            evidence = artifact("evidence_zip", out.getvalue(), "application/zip",
                                self.report_sha, self.snapshot_sha, "evidence.zip")
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                main._validate_evidence_zip(
                    self.job, self.snapshot, report, pdf, evidence, [self.cif])


class OriginMigrationAndRouteTests(unittest.TestCase):
    def test_origin_validation(self):
        for value in ("http://example.com", "https://example.com/path",
                      "https://example.com?q=x", "https://u:p@example.com"):
            with mock.patch.dict(os.environ, {"PUBLIC_APP_URL": value}):
                with self.assertRaises(RuntimeError):
                    main._public_origin()
        for value in ("https://agentbio.groundlogic.ai:bad",
                      "https://agentbio.groundlogic.ai:99999",
                      "https://:443", "https://user@example.com",
                      "https://agentbio.groundlogic.ai bad",
                      "https://agentbio.groundlogic.ai\n"):
            with self.subTest(value=value), \
                    mock.patch.dict(os.environ, {"PUBLIC_APP_URL": value}):
                with self.assertRaises(RuntimeError):
                    main._public_origin()
        with mock.patch.dict(
                os.environ, {"PUBLIC_APP_URL": "https://agentbio.groundlogic.ai:443"}):
            self.assertEqual(
                main._public_origin(), "https://agentbio.groundlogic.ai:443")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main._public_origin(), "https://agentbio.groundlogic.ai")

    def test_download_headers_and_invalid_hash(self):
        payload = b"zip"
        row = artifact("evidence_zip", payload, "application/zip",
                       "a" * 64, "b" * 64, "evidence.zip")
        with mock.patch.object(main.jobs_db, "get_job_artifact", return_value=row):
            response = main.download_artifact("job", "evidence.zip")
        self.assertEqual(response.body, payload)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertIn("immutable", response.headers["cache-control"])
        self.assertEqual(response.headers["etag"], f'"{row["content_sha256"]}"')
        with self.assertRaises(HTTPException):
            main.download_cif_artifact("job", "../bad")

    def test_migration_has_immutable_contract(self):
        sql = Path("api/migrations/20260820_job_artifacts.sql").read_text()
        self.assertIn("'report_md', 'report_pdf', 'evidence_zip'", sql)
        self.assertIn("BEFORE UPDATE", sql)
        self.assertIn("BEFORE DELETE", sql)


class AtomicBundleTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"candidate": "x"}
        self.snapshot_sha = hashlib.sha256(
            canonical_json_bytes(self.snapshot)).hexdigest()
        self.items = [
            {"kind": "report_md", "payload": b"md", "filename": "report.md",
             "content_type": "text/markdown; charset=utf-8"},
            {"kind": "report_pdf", "payload": b"pdf", "filename": "report.pdf",
             "content_type": "application/pdf"},
            {"kind": "evidence_zip", "payload": b"zip", "filename": "evidence.zip",
             "content_type": "application/zip"},
        ]
        self.report_sha = hashlib.sha256(b"md").hexdigest()

    def call(self, existing, fail_insert=None):
        executed = []
        state = {"insert": 0}

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(_self, sql, params=None):
                executed.append(" ".join(sql.split()))
                if "INSERT INTO job_artifacts" in sql:
                    state["insert"] += 1
                    if state["insert"] == fail_insert:
                        raise RuntimeError("insert failure")
            def fetchone(_self):
                sql = executed[-1]
                if "FROM jobs" in sql:
                    return {"job_id": "j", "report_sha256": self.report_sha,
                            "candidate_snapshot_sha256": self.snapshot_sha,
                            "report_path": "artifact://j/report.md",
                            "decision_contract_version": "d",
                            "report_contract_version": "r",
                            "reviewer_input_fingerprint": "f"}
                if "job_candidate_snapshots" in sql:
                    return {"payload_json": self.snapshot}
                return {"job_id": "j"}
            def fetchall(_self): return existing

        class Connection:
            committed = False
            rolled_back = False
            def cursor(self, **_): return Cursor()
            def commit(self): self.committed = True
            def rollback(self): self.rolled_back = True
            def close(self): pass

        connection = Connection()
        self.last_connection = connection
        self.last_sql = executed
        with mock.patch.object(jobs_db, "_connect", return_value=connection):
            result = jobs_db.finalize_job_artifact_bundle(
                "j", artifacts=self.items, report_path="artifact://j/report.md",
                decision_contract_version="d", report_contract_version="r",
                report_sha256=self.report_sha,
                candidate_snapshot_sha256=self.snapshot_sha,
                reviewer_input_fingerprint="f")
        return result, connection, executed

    def existing_rows(self):
        rows = []
        for item in self.items:
            digest = hashlib.sha256(item["payload"]).hexdigest()
            rows.append({**item, "artifact_id": digest,
                         "content_sha256": digest})
        return rows

    def test_exact_retry_and_lock_order(self):
        _result, connection, sql = self.call(self.existing_rows())
        self.assertTrue(connection.committed)
        joined = "\n".join(sql)
        self.assertLess(joined.index("FROM jobs"), joined.index("job_candidate_snapshots"))
        self.assertLess(joined.index("job_candidate_snapshots"),
                        joined.index("FROM job_artifacts"))
        self.assertNotIn("INSERT INTO job_artifacts", joined)

    def test_partial_and_changed_retry_rejected(self):
        with self.assertRaisesRegex(ValueError, "partial or different"):
            self.call(self.existing_rows()[:1])
        changed = self.existing_rows()
        changed[1] = {**changed[1], "payload": b"tampered"}
        with self.assertRaisesRegex(ValueError, "partial or different"):
            self.call(changed)

    def test_insert_exception_rolls_back_without_metadata_update(self):
        with self.assertRaisesRegex(RuntimeError, "insert failure"):
            self.call([], fail_insert=2)
        # The context manager owns rollback; no UPDATE can occur after failure.
        self.assertTrue(self.last_connection.rolled_back)
        self.assertFalse(any(statement.startswith("UPDATE jobs")
                             for statement in self.last_sql))


class PostgreSQLImmutableTriggerTests(unittest.TestCase):
    def test_update_and_delete_triggers_reject_inside_rollback(self):
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            self.skipTest("DATABASE_URL unavailable")
        try:
            import psycopg2
            connection = psycopg2.connect(database_url)
        except Exception as exc:
            self.skipTest(f"development PostgreSQL unavailable: {exc}")
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT job_id FROM jobs LIMIT 1")
                row = cursor.fetchone()
                if not row:
                    self.skipTest("no fixture job available")
                payload = os.urandom(24)
                digest = hashlib.sha256(payload).hexdigest()
                cursor.execute(
                    """INSERT INTO job_artifacts
                       (job_id,kind,artifact_id,content_sha256,filename,content_type,
                        size_bytes,payload,report_sha256,candidate_snapshot_sha256,created_at)
                       VALUES (%s,'cif',%s,%s,'trigger-test.cif','chemical/x-cif',
                               %s,%s,%s,%s,'test')""",
                    (row[0], digest, digest, len(payload), payload, "a" * 64, "b" * 64))
                for operation in ("UPDATE", "DELETE"):
                    cursor.execute(f"SAVEPOINT before_{operation.lower()}")
                    with self.subTest(operation=operation), self.assertRaises(psycopg2.Error):
                        if operation == "UPDATE":
                            cursor.execute(
                                "UPDATE job_artifacts SET filename='changed.cif' "
                                "WHERE job_id=%s AND kind='cif' AND artifact_id=%s",
                                (row[0], digest))
                        else:
                            cursor.execute(
                                "DELETE FROM job_artifacts WHERE job_id=%s "
                                "AND kind='cif' AND artifact_id=%s", (row[0], digest))
                    cursor.execute(f"ROLLBACK TO SAVEPOINT before_{operation.lower()}")
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()