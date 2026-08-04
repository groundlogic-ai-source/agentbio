"""
Tests for the modality base-rate mode (registry finding run-704c0cb4-H05),
the render_spec composition provenance fix, and the archived-registry reset.
"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "data_prep"))
sys.path.insert(0, _ROOT)

import hypothesis_report as HR  # noqa: E402
from api import domain_findings as DF  # noqa: E402
from api import dossier as Dossier  # noqa: E402
from agents.reviewer import _modality_flag  # noqa: E402

# The exact feature_spec of confirmed hypothesis run-704c0cb4-H05.
H05_SPEC = {
    "op": "all_of",
    "params": {
        "terms": [
            {"op": "not_op", "params": {"term": {"op": "is_small_molecule"}}},
            {"op": "not_op", "params": {"term": {"op": "is_oral"}}},
        ]
    },
}


class TestRenderSpecComposition(unittest.TestCase):
    """Regression: H05's saved report rendered "unrecognized op: 'all_of'"."""

    def test_h05_spec_renders_literal_proxy(self):
        out = HR.render_spec(H05_SPEC)
        self.assertNotIn("unrecognized op", out)
        self.assertIn("AND", out)
        self.assertIn("NOT", out)
        self.assertIn("small molecule", out)
        self.assertIn("orally administered", out)

    def test_any_of_uses_or(self):
        spec = {"op": "any_of", "params": {"terms": [
            {"op": "is_oral"}, {"op": "established"},
        ]}}
        out = HR.render_spec(spec)
        self.assertIn(" OR ", out)
        self.assertNotIn("unrecognized op", out)

    def test_nested_composition_renders(self):
        spec = {"op": "not_op", "params": {"term": H05_SPEC}}
        out = HR.render_spec(spec)
        self.assertTrue(out.startswith("NOT ("))
        self.assertNotIn("unrecognized op", out)

    def test_interaction3_renders_all_three_terms(self):
        spec = {"op": "interaction3", "params": {
            "base": {"op": "xlogp_threshold", "params": {"k": 5}},
            "moderator": {"op": "is_oral"},
            "moderator2": {"op": "established"},
        }}
        out = HR.render_spec(spec)
        self.assertNotIn("unrecognized op", out)
        self.assertIn("three-way", out)

    def test_unknown_op_still_reports_unrecognized(self):
        self.assertIn("unrecognized op", HR.render_spec({"op": "nonsense"}))


class TestModalityMatching(unittest.TestCase):
    def test_nonoral_biologic_matches(self):
        self.assertEqual(DF.modality_match("Antibody", False), "Antibody")

    def test_small_molecule_never_matches(self):
        self.assertIsNone(DF.modality_match("Small molecule", False))
        self.assertIsNone(DF.modality_match("Small molecule", True))

    def test_oral_biologic_does_not_match(self):
        self.assertIsNone(DF.modality_match("Enzyme", True))

    def test_missingness_propagates(self):
        self.assertIsNone(DF.modality_match(None, False))
        self.assertIsNone(DF.modality_match("Antibody", None))
        self.assertIsNone(DF.modality_match(None, None))

    def test_methodology_is_surfaced_and_honest(self):
        m = DF.modality_finding_for("Antibody", False)[0]["methodology"]
        # The tested predicate, not the metaphor, must be stated.
        self.assertIn("NOT small molecule", m["predicate"])
        self.assertEqual(m["records_tested"], 2642)
        self.assertTrue(m["steps"])
        # The analogy must be explicitly labelled as carrying no evidence.
        self.assertIn("no evidentiary weight", m["analogy_status"])
        # Prior art must not be overclaimed as novel, and must be citable.
        self.assertIn("Not a novel claim", m["prior_art"])
        self.assertTrue(m["prior_art_citation"]["url"].startswith("https://"))
        self.assertTrue(m["prior_art_citation"]["label"])

    def test_methodology_n_matches_headline_stats(self):
        """Guard against the narrative drifting from the quoted statistics."""
        f = DF.modality_finding_for("Antibody", False)[0]
        self.assertEqual(f["methodology"]["records_tested"], f["stats"]["n"])

    def test_methodology_deepcopied_per_call(self):
        a = DF.modality_finding_for("Antibody", False)[0]
        a["methodology"]["steps"].append("mutated")
        b = DF.modality_finding_for("Antibody", False)[0]
        self.assertNotIn("mutated", b["methodology"]["steps"])

    def test_finding_payload_shape_and_copy(self):
        out = DF.modality_finding_for("Antibody", False)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["matched_term"], "Antibody")
        self.assertEqual(out[0]["provenance"]["hypothesis_id"], "run-704c0cb4-H05")
        out[0]["stats"]["odds_ratio"] = 999
        self.assertNotEqual(DF.MODALITY_REPURPOSING_PENALTY["stats"]["odds_ratio"], 999)

    def test_saved_h05_dossier_can_recompute_claims(self):
        """A historical saved H05 dossier opens even after its registry reset."""
        import hypothesis_registry as R  # noqa: E402
        saved = {
            "id": "saved-h05",
            "hypothesis_id": "run-704c0cb4-H05",
            "hypothesis_text": "Historical modality finding",
            "facts": {"passed_both": True, "framings": []},
            "report_markdown": "# Frozen report",
            "saved_at": None,
            "generated_at": None,
        }
        ledger = Dossier.dossier_claims("run-704c0cb4-H05", R, HR, saved)
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger["hypothesis_id"], "run-704c0cb4-H05")
        self.assertEqual(ledger["audit_status"], "unverifiable")
        self.assertIn("no longer", ledger["status_reasons"][0])

    def test_dossier_claims_route_returns_snapshot_for_reset_hypothesis(self):
        """The actual FastAPI route must not turn a historical card into 500."""
        from fastapi.testclient import TestClient
        from api.main import app

        saved = {
            "id": "saved-h05",
            "hypothesis_id": "run-704c0cb4-H05",
            "hypothesis_text": "Historical modality finding",
            "facts": {"passed_both": True, "framings": []},
            "report_markdown": "# Frozen report",
            "saved_at": None,
            "generated_at": None,
        }
        with mock.patch("api.main.saved_reports_db.list_reports", return_value=[saved]):
            response = TestClient(app).get(
                "/api/audit/dossiers/run-704c0cb4-H05/claims"
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["audit_status"], "unverifiable")

    def test_reviewer_flag_matches_finding_logic(self):
        self.assertTrue(_modality_flag("Antibody", False))
        self.assertFalse(_modality_flag("Small molecule", False))
        self.assertFalse(_modality_flag("Antibody", True))
        self.assertIsNone(_modality_flag(None, False))
        self.assertIsNone(_modality_flag("Antibody", None))

    def test_writer_cell_never_infers_route_from_missing_data(self):
        from agents.writer import _modality_cell
        # oral=None with a known type must stay unresolved, not render "non-oral".
        self.assertIn("unresolved", _modality_cell(
            {"chembl_molecule_type": "Antibody", "chembl_oral": None}))
        self.assertIn("unresolved", _modality_cell(
            {"chembl_molecule_type": None, "chembl_oral": False}))
        self.assertIn("unresolved", _modality_cell({}))
        flagged = _modality_cell({
            "chembl_molecule_type": "Antibody", "chembl_oral": False,
            "nonoral_biologic_flag": True,
        })
        self.assertIn("Antibody / non-oral", flagged)
        self.assertIn("⚠", flagged)
        clear = _modality_cell({
            "chembl_molecule_type": "Small molecule", "chembl_oral": True,
            "nonoral_biologic_flag": False,
        })
        self.assertEqual(clear, "Small molecule / oral")


class _FakeCursor:
    """Records statements; serves fixed counts for the dry-run SELECTs."""

    def __init__(self, counts):
        self.statements = []
        self._counts = list(counts)
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.statements.append(sql)
        self.rowcount = 7

    def fetchone(self):
        return (self._counts.pop(0),)


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


class TestDeleteArchivedRegistry(unittest.TestCase):
    def _run(self, dry_run, counts=(10, 20)):
        import api.main as main
        cursor = _FakeCursor(counts)
        conn = _FakeConn(cursor)
        fake_psycopg2 = types.SimpleNamespace(connect=lambda url: conn)
        with mock.patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
            with mock.patch.dict(os.environ, {"DATABASE_URL": "postgres://x"}):
                return main.delete_archived_registry(dry_run=dry_run), cursor, conn

    def test_dry_run_counts_only(self):
        out, cursor, conn = self._run(dry_run=True, counts=(330, 186))
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_delete"]["bisociation_history"], 330)
        self.assertEqual(out["would_delete"]["hypothesis_log"], 186)
        self.assertFalse(any("DELETE" in s for s in cursor.statements))
        self.assertFalse(conn.committed)

    def test_confirmed_rows_are_never_deleted(self):
        """The protective exception must be in every history-row criterion."""
        out, cursor, conn = self._run(dry_run=False)
        self.assertFalse(out["dry_run"])
        # Every statement whose row-selection mentions the archived flag must
        # carry the confirmed-row guard. (The log-orphan criterion references
        # the history table WITHOUT the archived flag — it runs post-delete,
        # where "no surviving row" already encodes the guard.)
        guarded = [s for s in cursor.statements if "archived" in s]
        self.assertTrue(guarded)
        for s in guarded:
            if "confirmation_pass IS NOT TRUE" in s or "confirmation_pass = TRUE" in s:
                continue
            self.fail(f"history statement missing the confirmed-row guard: {s}")
        deletes = [s for s in cursor.statements if s.strip().startswith("DELETE")]
        self.assertEqual(len(deletes), 2)
        # Backup must precede delete for BOTH tables.
        kinds = [
            ("backup" if "registry_reset_backup" in s else "delete", s)
            for s in cursor.statements
            if "registry_reset_backup" in s or s.strip().startswith("DELETE")
        ]
        self.assertEqual([k for k, _ in kinds],
                         ["backup", "delete", "backup", "delete"])
        self.assertTrue(conn.committed)

    def test_orphaned_log_rows_deleted_after_history(self):
        out, cursor, _ = self._run(dry_run=False)
        log_delete = [s for s in cursor.statements
                      if s.strip().startswith("DELETE") and "hypothesis_log" in s][0]
        self.assertIn("NOT IN", log_delete)
        self.assertEqual(out["deleted"]["hypothesis_log"], 7)


if __name__ == "__main__":
    unittest.main()
