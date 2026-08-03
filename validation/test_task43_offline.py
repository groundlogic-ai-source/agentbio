import json
import os
import tempfile
import unittest

from validation.task43_offline import (
    analyze_above_known_drug,
    build_task43_report,
    load_acceptance_results,
    summarize_evidence,
)
from validation.run_v2_engineering_acceptance import (
    RANK_CONTEXT_LIMIT,
    _public_rank_context_row,
    _rank_context_snapshot,
)


def _row(rank, name, score, records=None):
    return {
        "drug_name": name,
        "rank": rank,
        "composite_score": score,
        "score_components": {"efficacy_evidence": score},
        "target_symbol": "TGT",
        "_evidence_ledger": {
            "providers": ["chembl"],
            "target_symbols": ["TGT"],
            "efficacy_confidence": 0.8,
            "records": records or [],
            "raw_payload": "must-not-leak",
        },
        "_private": "must-not-leak",
    }


class RankContextCaptureTests(unittest.TestCase):
    def test_global_and_local_window(self):
        rows = [_row(i, f"Drug{i}", 1 - i / 1000) for i in range(1, 224)]
        snapshot = _rank_context_snapshot(rows, 217)
        ranks = [row["rank"] for row in snapshot]
        self.assertEqual(ranks, list(range(1, 21)) + list(range(197, 218)))
        self.assertEqual(len(ranks), 41)

    def test_miss_keeps_global_context_only(self):
        rows = [_row(i, f"Drug{i}", 1 - i / 1000) for i in range(1, 30)]
        self.assertEqual(
            [row["rank"] for row in _rank_context_snapshot(rows, None)],
            list(range(1, RANK_CONTEXT_LIMIT + 1)),
        )
        self.assertEqual(_rank_context_snapshot([], None), [])

    def test_snapshot_is_public_and_does_not_leak_private_ledger(self):
        row = _row(1, "Drug1", 0.9, [{
            "provider": "chembl",
            "source_type": "bioactivity_assay",
            "evidence_role": "efficacy",
            "qualification_status": "qualified",
            "contradiction_status": "none",
            "action": "inhibitor",
            "direction": "inhibitor",
            "raw_payload": "must-not-leak",
        }])
        public = _public_rank_context_row(row, 1)
        encoded = json.dumps(public)
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("_evidence_ledger", public)
        self.assertEqual(public["evidence_ledger"]["record_count"], 1)
        self.assertEqual(public["evidence_ledger"]["records"][0]["action"], "inhibitor")


class OfflineAnalysisTests(unittest.TestCase):
    def test_evidence_classes_are_descriptive(self):
        directional = _row(1, "Directional", 0.9, [{
            "evidence_role": "efficacy",
            "qualification_status": "qualified",
            "contradiction_status": "none",
            "action": "inhibitor",
            "direction": "inhibitor",
        }])
        self.assertEqual(
            summarize_evidence(directional)["evidence_class"],
            "qualified_directional",
        )
        contradicted = _row(2, "Contradicted", 0.8, [{
            "evidence_role": "target_link",
            "qualification_status": "qualified",
            "contradiction_status": "contradicted",
        }])
        self.assertEqual(
            summarize_evidence(contradicted)["evidence_class"],
            "qualified_contradicted",
        )
        self.assertEqual(
            summarize_evidence(_row(3, "Empty", 0.1))["evidence_class"],
            "none_or_identity_only",
        )

    def test_analyzes_only_neighbors_above_known_without_reranking(self):
        case = {
            "drug_name": "Known",
            "disease_name": "Disease",
            "rank": 5,
            "total_candidates": 10,
            "rank_context": [
                _row(1, "A", 0.9),
                _row(2, "B", 0.8),
                _row(3, "C", 0.7),
                _row(4, "D", 0.6),
                _row(5, "Known", 0.5),
            ],
        }
        result = analyze_above_known_drug(case)
        self.assertEqual([r["rank"] for r in result["neighbors_above"]], [1, 2, 3, 4])
        self.assertEqual(result["known_score"], 0.5)
        self.assertEqual(result["neighbors_above"][0]["score_delta_vs_known"], 0.4)

    def test_missing_context_is_not_a_ranking_conclusion(self):
        result = analyze_above_known_drug({
            "drug_name": "Known", "disease_name": "Disease",
            "rank": 217, "total_candidates": 223,
        })
        self.assertEqual(result["diagnostic_status"], "rank_context_not_persisted")
        report = build_task43_report({
            "label": "engineering_acceptance",
            "cases": [{
                "drug_name": "Known", "disease_name": "Disease",
                "rank": 217, "total_candidates": 223,
            }],
        })
        self.assertIn("rank_context_not_persisted", report)
        self.assertIn("does not establish", report)

    def test_loader_requires_acceptance_label(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            json.dump({"label": "benchmark_v2", "cases": []}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_acceptance_results(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()