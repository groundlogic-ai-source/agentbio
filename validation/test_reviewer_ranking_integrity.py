"""General regression controls for coverage-aware ranking and safety reconciliation.

These controls intentionally use synthetic, non-fixture drug/disease names.
They protect ranking policies, not any known benchmark pair:
  * a missing structural similarity measurement is not fabricated as 0;
  * a genuinely measured zero remains a zero;
  * an L1 withdrawal flag is hard-capped unless an independent L2 check
    explicitly contradicts it; a contradiction stays visible in output.
"""

import unittest
from unittest.mock import patch

from agents import reviewer


class CoverageAwareCompositeTest(unittest.TestCase):
    def test_missing_similarity_is_not_measured_zero(self):
        # Identical qualified disease/efficacy/trial evidence.  The first
        # candidate simply lacks a similarity measurement; it must not be
        # treated as structurally dissimilar.
        missing, coverage_missing = reviewer._coverage_aware_composite(
            0.75, 0.60, None, False
        )
        measured_zero, coverage_zero = reviewer._coverage_aware_composite(
            0.75, 0.60, 0.0, False
        )
        self.assertGreater(missing, measured_zero)
        self.assertEqual(coverage_missing, 0.85)
        self.assertEqual(coverage_zero, 1.0)
        # The omitted feature is normalized, never turned into a bonus.
        self.assertAlmostEqual(missing, (0.5 * 0.75 + 0.2 * 0.60) / 0.85)

    def test_measured_similarity_still_affects_score(self):
        low, _ = reviewer._coverage_aware_composite(0.75, 0.60, 0.0, False)
        high, _ = reviewer._coverage_aware_composite(0.75, 0.60, 0.8, False)
        self.assertGreater(high, low)

    def test_redacted_trial_is_not_normalized_away(self):
        no_credit, _ = reviewer._coverage_aware_composite(0.75, 0.60, None, False)
        honest_credit, _ = reviewer._coverage_aware_composite(0.75, 0.60, None, True)
        self.assertGreater(honest_credit, no_credit)

    def test_qualified_directional_evidence_resolves_a_floor_tie(self):
        directional = _candidate("DirectionalControl")
        directional["_evidence_ledger"]["records"] = [{
            "qualification_status": "qualified",
            "action": "inhibitor",
            "direction": "inhibitor",
        }]
        undirected = _candidate("UndirectedControl")
        undirected["_evidence_ledger"]["records"] = [{
            "qualification_status": "qualified",
            "action": "",
            "direction": "unknown",
        }]
        self.assertTrue(reviewer._has_qualified_directional_evidence(directional))
        self.assertFalse(reviewer._has_qualified_directional_evidence(undirected))
        self.assertEqual(reviewer.QUALIFIED_DIRECTIONAL_BONUS, 0.05)


def _candidate(name: str = "SyntheticDrug") -> dict:
    return {
        "drug_name": name,
        "molecule_chembl_id": "CHEMBL_SYNTHETIC",
        "target_symbol": "SYN1",
        "smiles": "CCO",
        "pchembl_value": 7.0,
        "confidence_score": 9,
        "efficacy_confidence": 0.8,
        "ot_association_score": 0.7,
        "tanimoto_score": 0.2,
        "is_approved_drug": True,
        "source_activity_ids": [],
        "source_chembl_ids": [],
        "source_types": ["chembl"],
        "source_health": {"chembl": True},
        "_evidence_ledger": {"records": []},
    }


class WithdrawalReconciliationTest(unittest.TestCase):
    def _run(self, layer2: dict):
        l1 = {
            "confirmed": True,
            "black_box_advisory": False,
            "api_error": False,
            "source_url": "https://example.test/chembl",
        }
        context = [{
            "adverse": {"adverse_events": []},
            "trials": {"trials": [], "query_failed": False},
            "pubchem": {"xlogp": None},
            "safety_layer1": l1,
        }]
        with patch.object(reviewer, "_prefetch_candidate_context", return_value=context), \
                patch.object(reviewer, "web_safety_check", return_value=layer2), \
                patch.object(reviewer, "get_drug_action_type", return_value={"source": "not_found"}), \
                patch.object(reviewer, "check_mechanism_direction", return_value={
                    "incompatible": False, "verdict": "INSUFFICIENT_INFO"
                }), \
                patch.object(reviewer.provenance, "log_many"):
            return reviewer.run_reviewer(
                {"target": {"disease_name": "Synthetic disease"},
                 "candidates": [_candidate()]},
            )[0]

    def test_explicit_independent_no_releases_l1_only_cap_and_discloses_dispute(self):
        row = self._run({
            "confirmed": False, "verdict": "NO",
            "black_box_advisory": False, "citation": "https://example.test/l2",
        })
        self.assertFalse(row["safety_cap_applied"])
        self.assertEqual(row["safety_reconciliation"]["status"], "disputed")
        self.assertGreater(row["composite_score"], reviewer.SAFETY_CAP)

    def test_unclear_independent_result_keeps_conservative_cap(self):
        row = self._run({
            "confirmed": False, "verdict": "UNCLEAR",
            "black_box_advisory": False, "citation": None,
        })
        self.assertTrue(row["safety_cap_applied"])
        self.assertIsNone(row["safety_reconciliation"])
        self.assertEqual(row["composite_score"], reviewer.SAFETY_CAP)

    def test_independent_withdrawal_confirmation_keeps_cap(self):
        row = self._run({
            "confirmed": True, "verdict": "YES",
            "black_box_advisory": False, "citation": "https://example.test/l2",
        })
        self.assertTrue(row["safety_cap_applied"])
        self.assertEqual(row["composite_score"], reviewer.SAFETY_CAP)


if __name__ == "__main__":
    unittest.main()