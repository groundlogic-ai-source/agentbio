"""
Guard tests for the inter-rater scoring harness's output-path isolation.

Sheet fields are untrusted: a crafted participant_id/arm/list_id must never
let a study artifact escape validation/interrater_results/ — that is the
protocol's "study results never merge with engineering artifacts" rule,
enforced at write time. Regression suite for the code-review finding.
"""
from __future__ import annotations

import json
import os
import unittest

from validation import run_interrater_scoring as scoring


def _base():
    return os.path.realpath(scoring.RESULTS_DIR)


class OutputPathContainmentTest(unittest.TestCase):
    def _assert_inside(self, out: str):
        self.assertEqual(
            os.path.commonpath([_base(), os.path.realpath(out)]), _base()
        )

    def test_traversal_participant_id_is_neutralized(self):
        out = scoring._output_path({
            "participant_id": "../../../validation/audit_trap_results",
            "arm": "assisted", "list_id": "A1",
        })
        self._assert_inside(out)
        self.assertNotIn("..", os.path.basename(out))
        self.assertNotEqual(
            os.path.basename(out), "audit_trap_results.json"
        )

    def test_absolute_path_and_separator_fields_are_neutralized(self):
        for evil in ("/etc/passwd", "..\\..\\windows\\system32", "a/b/c"):
            with self.subTest(evil=evil):
                out = scoring._output_path({
                    "participant_id": "P1", "arm": evil, "list_id": "A1",
                })
                self._assert_inside(out)

    def test_dots_only_field_is_refused(self):
        with self.assertRaises(SystemExit):
            scoring._output_path({
                "participant_id": "../..", "arm": "x", "list_id": "y",
            })

    def test_end_to_end_write_stays_inside_results_dir(self):
        # Full score + path + write cycle with a hostile participant id;
        # the frozen A1 list supplies real ground truth.
        sheet = {
            "participant_id": "../../audit_trap_results",
            "arm": "assisted", "list_id": "A1", "minutes": 5.0,
            "responses": [{"drug_name": "Asprin", "flagged": True}],
        }
        result = scoring.score_sheet(sheet)
        out = scoring._output_path(result)
        self._assert_inside(out)
        os.makedirs(scoring.RESULTS_DIR, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        try:
            self.assertTrue(os.path.isfile(out))
            # The engineering artifact must be untouched and unrelated.
            eng = os.path.join(os.path.dirname(_base()), "audit_trap_results.json")
            self.assertNotEqual(os.path.realpath(out), os.path.realpath(eng))
        finally:
            os.remove(out)


if __name__ == "__main__":
    unittest.main()
