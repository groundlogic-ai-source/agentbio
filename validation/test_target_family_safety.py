"""Regression tests for related-target safety-liability disclosure."""

import unittest

from agents.writer import build_report_markdown
from data_sources.target_family_safety import assess_target_family_safety


class TestTargetFamilySafety(unittest.TestCase):
    def test_kcnh2_mechanism_flags_kcnh1_candidate(self):
        result = assess_target_family_safety(
            "KCNH1",
            mechanism_envelope={
                "status": "ok",
                "targets": [{
                    "target_chembl_id": "CHEMBL240",
                    "target_name": "hERG potassium channel",
                    "gene_symbols": ["KCNH2"],
                    "uniprot_ids": ["Q12809"],
                    "mechanisms": ["potassium channel blocker"],
                    "action_types": ["INHIBITOR"],
                }],
            },
        )
        self.assertEqual(result["status"], "FLAGGED")
        self.assertEqual(result["liability_target"], "KCNH2/hERG (Kv11.1)")
        self.assertEqual(result["score_effect"], "none")

    def test_empty_successful_lookup_is_not_called_safe(self):
        result = assess_target_family_safety(
            "KCNH1",
            mechanism_envelope={"status": "empty", "targets": []},
        )
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertIn("not evidence of selectivity", result["relationship"])

    def test_unavailable_lookup_is_explicitly_unknown(self):
        result = assess_target_family_safety(
            "KCNH1",
            mechanism_envelope={"status": "unavailable", "targets": []},
        )
        self.assertEqual(result["status"], "UNKNOWN")

    def test_non_kcnh_target_is_not_applicable(self):
        result = assess_target_family_safety(
            "HDAC6",
            mechanism_envelope={"status": "unavailable"},
        )
        self.assertEqual(result["status"], "NOT_APPLICABLE")

    def test_report_surfaces_flag_without_score_change_claim(self):
        candidate = {
            "drug_name": "Quinidine",
            "target_symbol": "KCNH1",
            "disease_name": "Zimmermann-Laband syndrome",
            "strong_match": True,
            "composite_score": 0.84,
            "is_approved_drug": True,
            "score_components": {
                "efficacy_evidence": 0.8,
                "normalized_ot_association": 0.5,
                "normalized_tanimoto": 0.3,
                "no_failed_trial": 1,
                "evidence_weight_coverage": 1.0,
            },
            "provenance": {"counted_once": [], "collapsed_as_duplicate": []},
            "target_family_safety_liability": {
                "status": "FLAGGED",
                "liability_target": "KCNH2/hERG (Kv11.1)",
                "evidence": [{
                    "mechanisms": ["potassium channel blocker"],
                    "action_types": ["INHIBITOR"],
                }],
            },
        }
        markdown = build_report_markdown(candidate, {}, {}, None)
        self.assertIn("Target-family cross-reactivity caution", markdown)
        self.assertIn("does not affect the composite score", markdown)
        self.assertNotIn("hard-capped", markdown)

    def test_report_requires_review_when_family_lookup_is_empty(self):
        candidate = {
            "drug_name": "Quinidine",
            "target_symbol": "KCNH1",
            "disease_name": "Zimmermann-Laband syndrome",
            "strong_match": True,
            "composite_score": 0.84,
            "score_components": {},
            "target_family_safety_liability": {
                "status": "NOT_FOUND",
                "liability_target": "KCNH2/hERG (Kv11.1)",
            },
        }
        markdown = build_report_markdown(candidate, {}, {}, None)
        self.assertIn("Target-family selectivity review required", markdown)
        self.assertIn("not establish selectivity or safety", markdown)


if __name__ == "__main__":
    unittest.main()