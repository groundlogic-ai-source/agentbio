"""Layer 2 web-safety check must separate withdrawal from black-box verdicts.

Regression guard: the v1 classifier asked ONE conflated question ("market
withdrawal or black-box warning?").  A drug carrying a boxed warning but
still marketed (e.g. lamotrigine) rendered YES and inherited the hard safety
cap — silently undoing the Layer 1 fix that made black-box disclosure-only.
v2 asks WITHDRAWAL and BLACK_BOX separately; only WITHDRAWAL: YES may cap.
"""

import os
import unittest
from unittest.mock import MagicMock, patch

from data_sources import safety_check
from agents.reviewer import _sort_reviewed


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _run_check(classify_text: str) -> dict:
    with patch.dict(os.environ, {
        "AI_INTEGRATIONS_ANTHROPIC_BASE_URL": "http://example.invalid",
        "AI_INTEGRATIONS_ANTHROPIC_API_KEY": "test-only",
    }, clear=False), patch.object(
        safety_check, "get", return_value=None
    ), patch.object(
        safety_check, "cache_set"
    ), patch.object(
        safety_check.anthropic, "Anthropic"
    ) as constructor, patch.object(
        # Step 2 classification moved to the provider round-robin helper in
        # Amendment 3 (5f0a55e) — mock it where safety_check looks it up.
        safety_check, "chat_text", return_value=(classify_text, "mock")
    ):
        client = MagicMock()
        client.messages.create.side_effect = [
            _text_response("Search results about the drug's regulatory history."),
        ]
        constructor.return_value = client
        return safety_check.web_safety_check("ControlDrug")


class Layer2SplitVerdictTest(unittest.TestCase):
    def test_black_box_only_does_not_cap(self):
        result = _run_check(
            "WITHDRAWAL: NO\nBLACK_BOX: YES\nCITATION: https://example.com/bbw"
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["verdict"], "NO")
        self.assertEqual(result["black_box_verdict"], "YES")
        self.assertTrue(result["black_box_advisory"])
        self.assertIn(
            "does not confirm the compound is safe", result["disclosure_text"]
        )

    def test_withdrawal_yes_caps(self):
        result = _run_check(
            "WITHDRAWAL: YES\nBLACK_BOX: UNCLEAR\n"
            "CITATION: https://example.com/withdrawn"
        )
        self.assertTrue(result["confirmed"])
        self.assertFalse(result["black_box_advisory"])
        self.assertIn("MARKET WITHDRAWAL", result["disclosure_text"])

    def test_legacy_verdict_line_still_parsed(self):
        # Model ignores the two-question format and answers old-style.
        result = _run_check("VERDICT: YES\nCITATION: https://example.com/x")
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["verdict"], "YES")

    def test_unclear_never_caps(self):
        result = _run_check(
            "WITHDRAWAL: UNCLEAR\nBLACK_BOX: UNCLEAR\nCITATION: none"
        )
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["black_box_advisory"])


class PreCapTieBreakTest(unittest.TestCase):
    def test_capped_ties_sort_by_pre_cap_score(self):
        reviewed = [
            {"composite_score": 0.40, "pre_cap_score": 0.38},   # weak, at floor
            {"composite_score": 0.40, "pre_cap_score": 0.66},   # strong-but-capped
            {"composite_score": 0.85, "pre_cap_score": 0.85},   # uncapped leader
            {"composite_score": 0.40, "pre_cap_score": 0.51},
        ]
        _sort_reviewed(reviewed)
        self.assertEqual(
            [r["pre_cap_score"] for r in reviewed], [0.85, 0.66, 0.51, 0.38]
        )

    def test_missing_pre_cap_falls_back_to_zero(self):
        reviewed = [
            {"composite_score": 0.40},
            {"composite_score": 0.40, "pre_cap_score": 0.55},
        ]
        _sort_reviewed(reviewed)
        self.assertEqual(reviewed[0].get("pre_cap_score"), 0.55)


class OldRowBackwardCompatTest(unittest.TestCase):
    """Rows persisted before this change lack pre_cap_score / black_box fields.
    They must sort without crashing and produce only WARN-level handoff
    complaints — never error-severity failures."""

    _OLD_STYLE_ROW = {
        "drug_name": "LegacyDrug",
        "target_symbol": "SCN1A",
        "disease_name": "Dravet syndrome",
        "composite_score": 0.40,
        "strong_match": False,
        "unapproved_cap_applied": False,
        "mechanism_cap_applied": False,
        "safety_cap_applied": True,
        "trials_query_failed": False,
        "uniprot_id": None,
        "target_discovery_method": "manual_lookup",
        "_evidence_ledger": {},
    }

    def test_old_rows_sort_without_crash(self):
        rows = [dict(self._OLD_STYLE_ROW), dict(self._OLD_STYLE_ROW)]
        _sort_reviewed(rows)  # must not raise

    def test_old_rows_yield_warnings_not_errors(self):
        from agents.schemas import validate_reviewer_handoff
        problems = validate_reviewer_handoff([dict(self._OLD_STYLE_ROW)])
        self.assertTrue(problems, "expected warn-level complaints for missing new fields")
        self.assertFalse(any("ERROR at" in p for p in problems))
        self.assertTrue(all("WARN at" in p for p in problems))

    def test_new_rows_pass_handoff_cleanly(self):
        from agents.schemas import validate_reviewer_handoff
        row = dict(self._OLD_STYLE_ROW)
        row["pre_cap_score"] = 0.66
        row["black_box_advisory"] = True
        row["evidence_weight_coverage"] = 0.85
        row["safety_reconciliation"] = None
        self.assertEqual(validate_reviewer_handoff([row]), [])


if __name__ == "__main__":
    unittest.main()
