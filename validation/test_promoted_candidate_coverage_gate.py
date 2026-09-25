"""Coverage completeness is required of promoted candidates, not the whole pool.

Regression cover for the post-benchmark correction of 2026-09-24. A transient
provider failure on an exploratory pathway-neighbor target used to discard an
entire run at the persistence gate, even when the promoted candidate sat on the
direct disease target with complete coverage. See `_promoted_candidates`.
"""

import os
import tempfile
import unittest
from unittest import mock

from api import main
from api.policy_contracts import (
    LITERATURE_SCHEMA_VERSION,
    REPORT_CONTRACT_VERSION,
    REVIEWER_FORMULA_VERSION,
    SAFETY_SCHEMA_VERSION,
)


def _candidate(name: str, *, complete: bool, promoted: bool) -> dict:
    """A snapshot candidate row, with coverage and promotion set independently."""
    return {
        "drug_name": name,
        "candidate_source_coverage": {
            "complete": complete,
            "failures": [] if complete else [{
                "source": "gtopdb",
                "status": "unavailable",
                "error": "transient HTTP 429",
                "target_symbol": "NR3C1",
                "uniprot_id": "P04150",
            }],
        },
        "dossier_evidence_contract": {
            "contract_version": REPORT_CONTRACT_VERSION,
        },
        "paid_validation_eligible": promoted,
        "headline_eligible": promoted,
        "externally_prioritizable": promoted,
        "exclusion_reasons": (
            [] if complete else ["candidate_source_coverage_incomplete"]),
    }


def _snapshot(candidates: list[dict]) -> dict:
    """A snapshot whose every gate input but candidate coverage is healthy.

    `source_coverage_complete` stays True and `source_failure_details` empty
    because an exploratory neighbor's failure is scoped to a warning at the
    snapshot level, where `_candidate_source_coverage` is called with no
    candidate.
    """
    return {
        "formula": {"formula_version": REVIEWER_FORMULA_VERSION},
        "safety_schema_version": SAFETY_SCHEMA_VERSION,
        "literature_schema_version": LITERATURE_SCHEMA_VERSION,
        "report_contract_version": REPORT_CONTRACT_VERSION,
        "reviewer_input_fingerprint": "fingerprint-neighbor",
        "source_coverage_complete": True,
        "source_failure_details": [],
        "candidates": candidates,
    }


class PromotedCandidateCoverageGateTest(unittest.TestCase):

    def _persist(self, snapshot: dict):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = os.path.join(tmp, "report.md")
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write("# Dossier\n")
            with mock.patch.object(
                main.jobs_db, "get_candidate_snapshot",
                return_value=snapshot,
            ), mock.patch.object(
                main.jobs_db, "finalize_job_artifact_bundle"
            ), mock.patch.object(
                main, "_JOB_REPORT_ROOT", os.path.join(tmp, "job_reports")
            ):
                return main._persist_actionable_report("job-x", report_path)

    def test_incomplete_neighbor_candidate_does_not_block_the_report(self):
        """The pooled-but-excluded casualty of a neighbor failure is tolerated."""
        snapshot = _snapshot([
            _candidate("Drospirenone", complete=True, promoted=True),
            _candidate("Neighbor casualty", complete=False, promoted=False),
        ])
        metadata = self._persist(snapshot)
        self.assertTrue(metadata["report_path"].startswith("artifact://"))

    def test_incomplete_promoted_candidate_still_blocks_the_report(self):
        """The invariant that matters is unchanged: nothing promoted may be thin."""
        snapshot = _snapshot([
            _candidate("Drospirenone", complete=False, promoted=True),
            _candidate("Fully covered", complete=True, promoted=False),
        ])
        with self.assertRaises(RuntimeError) as caught:
            self._persist(snapshot)
        self.assertIn("incomplete", str(caught.exception))

    def test_report_with_no_promoted_candidate_still_persists(self):
        """"No candidate can be authorized" is a legitimate, citable outcome."""
        snapshot = _snapshot([
            _candidate("Neighbor casualty", complete=False, promoted=False),
            _candidate("Below threshold", complete=True, promoted=False),
        ])
        metadata = self._persist(snapshot)
        self.assertTrue(metadata["report_path"].startswith("artifact://"))

    def test_superseded_candidate_contract_still_blocks_every_row(self):
        """Contract versioning stays pool-wide; only coverage was rescoped."""
        candidates = [_candidate("Drospirenone", complete=True, promoted=True),
                      _candidate("Pooled", complete=True, promoted=False)]
        candidates[1]["dossier_evidence_contract"] = {
            "contract_version": "superseded-v0"}
        with self.assertRaises(RuntimeError):
            self._persist(_snapshot(candidates))

    def test_promoted_selection_uses_any_promotion_flag(self):
        candidate = _candidate("Headline only", complete=True, promoted=False)
        candidate["headline_eligible"] = True
        self.assertEqual(
            [row["drug_name"] for row in main._promoted_candidates([candidate])],
            ["Headline only"])


if __name__ == "__main__":
    unittest.main()
