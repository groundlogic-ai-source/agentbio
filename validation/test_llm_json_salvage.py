"""
Regression tests for LLM JSON extraction.

Observed failure: a generator's reply was truncated mid-array. The balanced-bracket
scan never found the array's closing ']', fell through to the first INNER object,
and extract_json_list() then unwrapped THAT object's first list-valued field. The
caller received one domain's `hypotheses` list in place of the proposal array.

Every element was a valid dict, so nothing raised. Those elements carry no "domain"
key, so they yielded no hypotheses and were also skipped by the dropped-domain
backfill — one of the two generators contributed nothing, with no error anywhere.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_prep"),
)
import llm_clients as L  # noqa: E402
import run_discovery as RD  # noqa: E402


def _proposal(domain: str, n_hyp: int = 3) -> dict:
    return {
        "domain": domain,
        "domain_rationale": "shares structure",
        "hypotheses": [
            {
                "hypothesis_text": f"{domain} claim {i}",
                "mechanistic_justification": "because",
                "feature_spec": {"op": "is_oral"},
                "tag": "READY",
                "needs": "",
            }
            for i in range(n_hyp)
        ],
    }


class TestTruncatedArraySalvage(unittest.TestCase):
    def setUp(self):
        self.full = [_proposal("braess paradox"), _proposal("nucleation"), _proposal("hgt")]
        self.text = json.dumps(self.full, indent=2)

    def test_complete_array_unchanged(self):
        out = L.extract_json_list(self.text)
        self.assertEqual(len(out), 3)
        self.assertEqual([d["domain"] for d in out],
                         ["braess paradox", "nucleation", "hgt"])

    def test_truncated_array_returns_domains_not_hypotheses(self):
        truncated = self.text[: int(len(self.text) * 0.75)]
        out = L.extract_json_list(truncated)
        self.assertTrue(out, "must salvage something from a truncated array")
        for el in out:
            self.assertIn("domain", el,
                          "salvaged an inner hypotheses list instead of proposals")
        self.assertLess(len(out), 3, "this fixture really is truncated")

    def test_truncation_mid_first_element_raises_instead_of_returning_a_fragment(self):
        """
        The exact production shape: cut off inside element 1, so NO element is
        complete. Previously the scan fell through to the innermost complete
        object and returned [{'op': 'is_oral'}] — a feature_spec masquerading as
        the proposal array. With nothing recoverable, failing loudly is the only
        honest outcome.
        """
        cut = self.text.index('"hypotheses"') + 200
        with self.assertRaises(ValueError) as ctx:
            L.extract_json_list(self.text[:cut])
        self.assertIn("truncated", str(ctx.exception))

    def test_salvage_preserves_element_content(self):
        truncated = self.text[: int(len(self.text) * 0.8)]
        out = L.extract_json_list(truncated)
        self.assertEqual(out[0]["domain"], "braess paradox")
        self.assertEqual(len(out[0]["hypotheses"]), 3)

    def test_wrapped_object_still_unwraps(self):
        wrapped = json.dumps({"domains": self.full})
        out = L.extract_json_list(wrapped)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["domain"], "braess paradox")

    def test_fenced_block_still_parses(self):
        out = L.extract_json_list(f"Here you go:\n```json\n{self.text}\n```\nDone.")
        self.assertEqual(len(out), 3)

    def test_prose_around_array_still_parses(self):
        out = L.extract_json_list(f"Sure! {self.text} Hope that helps.")
        self.assertEqual(len(out), 3)

    def test_single_object_reply_still_supported(self):
        out = L.extract_json_list(json.dumps(_proposal("solo")))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["domain"], "solo")

    def test_empty_array_is_empty_not_an_error(self):
        self.assertEqual(L.extract_json_list("[]"), [])

    def test_garbage_still_raises(self):
        with self.assertRaises(ValueError):
            L.extract_json_list("no json here at all")


class TestProposalShapeGuard(unittest.TestCase):
    def test_hypothesis_dicts_are_rejected_as_proposals(self):
        hyps = _proposal("x")["hypotheses"]
        self.assertEqual(RD._as_proposals(hyps, "B"), [],
                         "hypothesis dicts must never pass as domain proposals")

    def test_real_proposals_pass(self):
        props = [_proposal("a"), _proposal("b")]
        self.assertEqual(len(RD._as_proposals(props, "A")), 2)

    def test_mixed_input_keeps_only_proposals(self):
        mixed = [_proposal("a")] + _proposal("x")["hypotheses"]
        out = RD._as_proposals(mixed, "B")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["domain"], "a")

    def test_empty_domain_name_retained_but_flagged(self):
        """Kept (it has hypotheses) but it cannot be de-duplicated — must warn."""
        p = _proposal("")
        out = RD._as_proposals([p], "B")
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
