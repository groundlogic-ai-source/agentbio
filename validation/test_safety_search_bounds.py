"""Safety Layer 2 must stay bounded and avoid redundant provider fan-out."""

import unittest

from agents.reviewer import (
    MAX_SAFETY_LAYER2_CANDIDATES,
    _safety_layer2_shortlist,
    _should_run_safety_layer2,
)
from data_sources import safety_check


class SafetySearchBoundsTest(unittest.TestCase):
    def test_provider_transcript_is_hard_bounded(self):
        text = "x" * (safety_check._SEARCH_TEXT_MAX_CHARS + 100)
        bounded = safety_check._bound_search_text(text)
        self.assertLessEqual(
            len(bounded),
            safety_check._SEARCH_TEXT_MAX_CHARS
            + len("\n[search transcript truncated; omitted text is not classified]"),
        )
        self.assertTrue(bounded.startswith("x" * 100))
        self.assertIn("omitted text is not classified", bounded)

    def test_observed_clear_structured_result_skips_redundant_search(self):
        self.assertFalse(
            _should_run_safety_layer2(
                "panobinostat",
                {"panobinostat"},
                {
                    "chembl_id": "CHEMBL483254",
                    "api_error": False,
                    "black_box_advisory": False,
                    "confirmed": False,
                },
            )
        )

    def test_unresolved_top_k_candidate_still_gets_redundant_search(self):
        self.assertTrue(
            _should_run_safety_layer2(
                "unresolved-drug",
                {"unresolved-drug"},
                {
                    "chembl_id": None,
                    "api_error": False,
                    "black_box_advisory": False,
                    "confirmed": False,
                },
            )
        )

    def test_structured_error_warning_and_withdrawal_still_get_layer2(self):
        for layer1 in (
            {"chembl_id": "CHEMBL1", "api_error": True},
            {"chembl_id": "CHEMBL1", "black_box_advisory": True},
            {"chembl_id": "CHEMBL1", "confirmed": True},
        ):
            with self.subTest(layer1=layer1):
                self.assertTrue(
                    _should_run_safety_layer2("drug", set(), layer1)
                )

    def test_structured_outage_cannot_fan_out_beyond_total_budget(self):
        reviewed = [
            {
                "drug_name": f"drug-{index}",
                "_prefetched_safety_layer1": {
                    "chembl_id": f"CHEMBL{index}",
                    "api_error": True,
                },
            }
            for index in range(MAX_SAFETY_LAYER2_CANDIDATES + 4)
        ]
        selected = _safety_layer2_shortlist(
            reviewed,
            {row["drug_name"] for row in reviewed[:3]},
        )
        self.assertEqual(len(selected), MAX_SAFETY_LAYER2_CANDIDATES)
        self.assertEqual(
            selected,
            {
                f"drug-{index}"
                for index in range(MAX_SAFETY_LAYER2_CANDIDATES)
            },
        )


if __name__ == "__main__":
    unittest.main()