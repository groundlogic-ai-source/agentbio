"""Study C checkpoint loader tests.

Regression guard for the 2026-08-17 prod restart: the old loader read the
multi-GB checkpoint via read_text().splitlines(), transiently tripling it
in RAM (suspected OOM). The streamed loader must preserve semantics:
fingerprint/cases-sha validation per record, kind routing, and shedding of
target records for pool-finalized diseases (assembly never re-reads them).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from validation.run_triage_discrimination_studyc import (  # noqa: E402
    CASES_PATH, RULE_FINGERPRINT, _load_checkpoint, _sha256_file)


def _rec(kind: str, **kw) -> dict:
    return {"kind": kind,
            "rule_fingerprint": RULE_FINGERPRINT,
            "cases_sha256": _sha256_file(CASES_PATH), **kw}


def _write_ckpt(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


class TestLoadCheckpoint(unittest.TestCase):

    def _run_load(self, records: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "ckpt.jsonl"
            _write_ckpt(ckpt, records)
            with mock.patch(
                    "validation.run_triage_discrimination_studyc.CKPT_PATH",
                    ckpt):
                return _load_checkpoint()

    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch(
                    "validation.run_triage_discrimination_studyc.CKPT_PATH",
                    Path(td) / "absent.jsonl"):
                done = _load_checkpoint()
        self.assertEqual(done, {"targets": {}, "pools": {}, "excluded": {}})

    def test_inflight_disease_targets_retained(self):
        recs = [
            _rec("target", disease_name="D1", target_symbol="GENE1",
                 candidates=[{"drug_name": "x"}], bio_pmids=["1"]),
            _rec("target", disease_name="D1", target_symbol="GENE2",
                 candidates=[], bio_pmids=[]),
        ]
        done = self._run_load(recs)
        self.assertEqual(set(done["targets"]),
                         {("D1", "GENE1"), ("D1", "GENE2")})
        self.assertEqual(done["pools"], {})

    def test_pooled_disease_targets_shed(self):
        recs = [
            _rec("target", disease_name="D1", target_symbol="GENE1",
                 candidates=[{"drug_name": "x"}], bio_pmids=[]),
            _rec("target", disease_name="D2", target_symbol="GENE9",
                 candidates=[{"drug_name": "y"}], bio_pmids=[]),
            _rec("pool", disease_name="D1", pool_size=3,
                 per_target=[], pool=[{"drug_name": "x"}]),
        ]
        done = self._run_load(recs)
        # D1 finalized -> its target shed; D2 still in flight -> retained.
        self.assertEqual(set(done["targets"]), {("D2", "GENE9")})
        self.assertEqual(set(done["pools"]), {"D1"})
        self.assertEqual(done["pools"]["D1"]["pool_size"], 3)

    def test_disease_excluded_recorded(self):
        recs = [_rec("disease_excluded", disease_name="D9",
                     reason="unscorable: no targets")]
        done = self._run_load(recs)
        self.assertEqual(done["excluded"], {"D9": "unscorable: no targets"})

    def test_stale_fingerprint_refused(self):
        recs = [{"kind": "target", "disease_name": "D1",
                 "target_symbol": "GENE1", "candidates": [],
                 "bio_pmids": [],
                 "rule_fingerprint": "STALE",
                 "cases_sha256": _sha256_file(CASES_PATH)}]
        with self.assertRaises(SystemExit):
            self._run_load(recs)

    def test_stale_cases_sha_refused(self):
        recs = [{"kind": "pool", "disease_name": "D1", "pool": [],
                 "pool_size": 0, "per_target": [],
                 "rule_fingerprint": RULE_FINGERPRINT,
                 "cases_sha256": "0" * 64}]
        with self.assertRaises(SystemExit):
            self._run_load(recs)

    def test_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            ckpt = Path(td) / "ckpt.jsonl"
            ckpt.write_text(
                "\n" + json.dumps(_rec("pool", disease_name="D1", pool=[],
                                       pool_size=0, per_target=[]))
                + "\n\n")
            with mock.patch(
                    "validation.run_triage_discrimination_studyc.CKPT_PATH",
                    ckpt):
                done = _load_checkpoint()
        self.assertEqual(set(done["pools"]), {"D1"})


if __name__ == "__main__":
    unittest.main()
