"""Study C discrimination metric tests.

Regression guard for the architect-review critical finding (2026-08-13):
equal composite scores must score 0.5, never an arbitrary win/loss from
unique list positions.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from validation import run_triage_discrimination_studyc as studyc  # noqa: E402
from validation.run_triage_discrimination_studyc import (  # noqa: E402
    CASES_PATH, RULE_FINGERPRINT, DiseaseUnscorable, _build_pool,
    _load_checkpoint, _pool_score, _score_auc, _sha256_file)


class TestScoreAuc(unittest.TestCase):

    def test_exact_ties_score_half(self):
        self.assertEqual(_score_auc([0.5], [0.5]), 0.5)
        self.assertEqual(_score_auc([0.5, 0.5], [0.5, 0.5]), 0.5)
        # Pairs: (0.7,0.5)=win, (0.7,0.9)=loss, (0.5,0.5)=tie, (0.5,0.9)=loss
        # -> (1 + 0 + 0.5 + 0) / 4 = 0.375
        self.assertEqual(_score_auc([0.7, 0.5], [0.5, 0.9]), 0.375)

    def test_strict_ordering(self):
        self.assertEqual(_score_auc([0.9], [0.1]), 1.0)
        self.assertEqual(_score_auc([0.1], [0.9]), 0.0)

    def test_empty_side_is_none(self):
        self.assertIsNone(_score_auc([], [0.1]))
        self.assertIsNone(_score_auc([0.1], []))
        self.assertIsNone(_score_auc([], []))

    def test_pool_score_casefold_and_absent(self):
        pool = [{"drug_name": "  Mercaptopurine ", "composite_score": 0.581}]
        self.assertEqual(_pool_score(pool, "mercaptopurine"), 0.581)
        self.assertEqual(_pool_score(pool, "MERCAPTOPURINE"), 0.581)
        self.assertIsNone(_pool_score(pool, "tretinoin"))
        self.assertIsNone(_pool_score([], "mercaptopurine"))


class TestUnscorableExclusion(unittest.TestCase):
    """Regression guard for the 2026-08-15 prod crash-loop: a case disease
    with zero OT genetic associations AND zero ChEMBL MOA targets raised a
    plain RuntimeError out of _build_pool, killing the runner; the supervisor
    then retried the deterministic failure every 5 min forever. The marker
    path must raise DiseaseUnscorable (so main() records a permanent,
    disclosed exclusion) while every other RuntimeError still propagates."""

    def test_nothing_to_score_raises_dedicated(self):
        with mock.patch.object(
                studyc, "select_for_disease",
                side_effect=RuntimeError(
                    "Open Targets returned no genetically-associated targets "
                    "and no approved-drug MOA targets were found in ChEMBL; "
                    "there is nothing to score.")):
            with self.assertRaises(DiseaseUnscorable):
                _build_pool("Nowhere Syndrome", {})

    def test_other_runtime_errors_propagate(self):
        with mock.patch.object(studyc, "select_for_disease",
                               side_effect=RuntimeError("transient HTTP 500")):
            with self.assertRaises(RuntimeError):
                _build_pool("Nowhere Syndrome", {})

    def test_out_of_universe_raises_dedicated(self):
        """A case outside the rare/NTD universe can never finalize; it must
        become a disclosed exclusion, not strand in `skipped` forever and
        block results (observed 2026-08-18: 'Urinary Incontinence')."""
        with mock.patch.object(
                studyc, "select_for_disease",
                side_effect=studyc.DiseaseNotInUniverse("not in universe")):
            with self.assertRaises(DiseaseUnscorable):
                _build_pool("Urinary Incontinence", {})

    def test_checkpoint_loads_exclusions(self):
        rec = {"kind": "disease_excluded",
               "disease_name": "Nowhere Syndrome",
               "reason": "there is nothing to score",
               "rule_fingerprint": RULE_FINGERPRINT,
               "cases_sha256": _sha256_file(CASES_PATH)}
        fd, tmp = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(rec) + "\n")
            with mock.patch.object(studyc, "CKPT_PATH", studyc.Path(tmp)):
                done = _load_checkpoint()
        finally:
            os.unlink(tmp)
        self.assertEqual(done["excluded"],
                         {"Nowhere Syndrome": "there is nothing to score"})
        self.assertEqual(done["pools"], {})
        self.assertEqual(done["targets"], {})


if __name__ == "__main__":
    unittest.main()
