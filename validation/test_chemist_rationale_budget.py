"""Unit tests for the chemist LLM-rationale budget gate.

The gate caps _llm_rationale calls (one API call per candidate) to a
deterministic top-K per target pool. Rationales are disclosure-only prose —
no score, rank, cap, or reviewer verdict consumes them — so the gate must be
deterministic and must never alter candidate content, only which candidates
get LLM prose vs the templated fallback.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.chemist import _llm_rationale_budget, _llm_rationale_eligible  # noqa: E402


def _cand(name, approved=False, pchembl=None, confidence=None):
    return {
        "drug_name": name,
        "is_approved_drug": approved,
        "pchembl_value": pchembl,
        "confidence_score": confidence,
    }


class TestRationaleBudget(unittest.TestCase):
    def test_default_budget_is_25(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_llm_rationale_budget(), 25)

    def test_env_override_and_bad_value(self):
        with mock.patch.dict(os.environ,
                             {"AGENTBIO_MAX_LLM_RATIONALES": "7"}):
            self.assertEqual(_llm_rationale_budget(), 7)
        with mock.patch.dict(os.environ,
                             {"AGENTBIO_MAX_LLM_RATIONALES": "junk"}):
            self.assertEqual(_llm_rationale_budget(), 25)

    def test_zero_budget_disables_llm_rationales(self):
        cands = [_cand("a", approved=True, pchembl=9.0)]
        self.assertEqual(_llm_rationale_eligible(cands, 0), set())

    def test_negative_budget_restores_unbudgeted_behavior(self):
        cands = [_cand(str(i)) for i in range(50)]
        self.assertEqual(_llm_rationale_eligible(cands, -1), set(range(50)))

    def test_ranking_prefers_approved_then_pchembl_then_confidence(self):
        cands = [
            _cand("low_conf", approved=True, pchembl=8.0, confidence=3),
            _cand("hi_conf", approved=True, pchembl=8.0, confidence=9),
            _cand("best_affinity", approved=False, pchembl=9.5, confidence=9),
            _cand("no_pchembl", approved=True, pchembl=None, confidence=9),
        ]
        # Budget 2: both approved+measured-affinity candidates beat the
        # higher-affinity non-approved one and the unmeasured one.
        self.assertEqual(_llm_rationale_eligible(cands, 2), {0, 1})

    def test_budget_caps_at_pool_size(self):
        cands = [_cand("a", pchembl=7.0), _cand("b", pchembl=8.0)]
        self.assertEqual(_llm_rationale_eligible(cands, 25), {0, 1})

    def test_deterministic_on_ties(self):
        cands = [_cand(str(i), pchembl=7.0, confidence=5) for i in range(10)]
        first = _llm_rationale_eligible(cands, 3)
        second = _llm_rationale_eligible(cands, 3)
        self.assertEqual(first, second)
        self.assertEqual(first, {0, 1, 2})


if __name__ == "__main__":
    unittest.main()
