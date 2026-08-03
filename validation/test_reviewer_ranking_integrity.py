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

    def test_measured_failed_trial_is_still_penalised(self):
        no_credit, _ = reviewer._coverage_aware_composite(0.75, 0.60, None, False)
        honest_credit, _ = reviewer._coverage_aware_composite(0.75, 0.60, None, True)
        self.assertGreater(honest_credit, no_credit)

    def test_unobserved_trial_is_a_coverage_gap_not_a_failed_trial(self):
        # Three DISTINCT states must not collapse into two.
        unobserved, cov_unobserved = reviewer._coverage_aware_composite(
            0.75, 0.60, None, None)
        measured_failure, cov_failure = reviewer._coverage_aware_composite(
            0.75, 0.60, None, False)
        clean, cov_clean = reviewer._coverage_aware_composite(
            0.75, 0.60, None, True)
        # A drug with a real failed trial must stay below one we simply
        # could not look up, which in turn stays below a confirmed-clean one.
        self.assertLess(measured_failure, unobserved)
        self.assertLess(unobserved, clean)
        # The unobserved term leaves BOTH sides of the ratio.
        self.assertAlmostEqual(cov_unobserved, 0.70)
        self.assertAlmostEqual(cov_failure, 0.85)
        self.assertAlmostEqual(cov_clean, 0.85)
        self.assertAlmostEqual(unobserved, (0.5 * 0.75 + 0.2 * 0.60) / 0.70)

    def test_dropping_a_term_renormalizes_and_never_gifts_credit(self):
        unobserved, _ = reviewer._coverage_aware_composite(0.9, 0.9, 0.9, None)
        clean, _ = reviewer._coverage_aware_composite(0.9, 0.9, 0.9, True)
        self.assertLess(unobserved, clean)


class PrecedentStampedAssociationTest(unittest.TestCase):
    """A stamped constant is not a measured target-disease association."""

    def test_precedent_stamped_constant_is_not_a_measurement(self):
        for method in ("pharmacological_precedent",
                       "pharmacological_precedent_via_parent_umbrella"):
            candidate = _candidate()
            candidate["target_discovery_method"] = method
            candidate["ot_association_score"] = 0.90
            self.assertIsNone(reviewer._measured_ot_association(candidate))

    def test_measured_associations_are_still_scored(self):
        for method in ("genetic_association", "pathway_neighbor"):
            candidate = _candidate()
            candidate["target_discovery_method"] = method
            candidate["ot_association_score"] = 0.677
            self.assertAlmostEqual(
                reviewer._measured_ot_association(candidate), 0.677)

    def test_every_stamped_discovery_method_is_excluded(self):
        """Guardrail: target_selection owns the stamped-constant method list.

        If a new precedent-only discovery method is added there without being
        added here, its stamped constant would silently be scored as a real
        measured association again — the exact flat lane-wide advantage this
        exclusion exists to remove.  Fail loudly instead of drifting.
        """
        from agents.target_selection import _PRECEDENT_ONLY_METHODS
        missing = set(_PRECEDENT_ONLY_METHODS) - reviewer._PRECEDENT_STAMPED_DISCOVERY
        self.assertEqual(
            missing, set(),
            f"precedent-only discovery method(s) {sorted(missing)} stamp a "
            "constant association but are still scored as measured evidence; "
            "add them to reviewer._PRECEDENT_STAMPED_DISCOVERY",
        )

    def test_stamped_lane_loses_its_flat_advantage_over_a_measured_lane(self):
        # Identical drug-level evidence.  The stamped 0.90 lane previously beat
        # the genuinely measured 0.677 lane on the constant alone.
        stamped = _candidate()
        stamped["target_discovery_method"] = "pharmacological_precedent"
        stamped["ot_association_score"] = 0.90
        measured = _candidate()
        measured["target_discovery_method"] = "genetic_association"
        measured["ot_association_score"] = 0.677

        stamped_score, _ = reviewer._coverage_aware_composite(
            0.70, reviewer._measured_ot_association(stamped), 0.2, True)
        measured_score, _ = reviewer._coverage_aware_composite(
            0.70, reviewer._measured_ot_association(measured), 0.2, True)
        self.assertGreater(measured_score, stamped_score)

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