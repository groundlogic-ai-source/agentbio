"""Study C discrimination metric tests.

Regression guard for the architect-review critical finding (2026-08-13):
equal composite scores must score 0.5, never an arbitrary win/loss from
unique list positions.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from validation.run_triage_discrimination_studyc import (  # noqa: E402
    _pool_score, _score_auc)


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


if __name__ == "__main__":
    unittest.main()
