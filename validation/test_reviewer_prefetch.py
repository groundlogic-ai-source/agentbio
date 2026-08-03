"""Non-network controls for bounded Reviewer enrichment prefetch."""

import time
import unittest
from unittest.mock import patch

from agents import reviewer
from data_sources import clinicaltrials, holdout


class ReviewerPrefetchTest(unittest.TestCase):
    def tearDown(self):
        holdout.deactivate()

    def test_prefetch_preserves_input_order_and_values(self):
        candidates = [
            {"drug_name": "Alpha", "molecule_chembl_id": "CHEMBL1"},
            {"drug_name": "Beta", "molecule_chembl_id": "CHEMBL2"},
            {"drug_name": "Gamma", "molecule_chembl_id": "CHEMBL3"},
        ]

        def adverse(drug):
            time.sleep({"Alpha": 0.03, "Beta": 0.02, "Gamma": 0.01}[drug])
            return {"drug": drug, "adverse_events": []}

        def trials(
            drug, disease, candidate_chembl_ids=None,
            candidate_inchikey=None,
        ):
            return {"drug": drug, "disease": disease, "trials": []}

        def pubchem(drug):
            return {"drug": drug, "xlogp": None}

        def safety(drug, molecule_id):
            return {"drug": drug, "molecule_id": molecule_id}

        with patch.object(reviewer, "get_adverse_events", adverse), \
                patch.object(reviewer, "check_prior_trials", trials), \
                patch.object(reviewer, "get_compound_data", pubchem), \
                patch.object(reviewer, "get_molecule_safety_flags", safety):
            rows = reviewer._prefetch_candidate_context(
                candidates, "Control disease"
            )

        self.assertEqual(
            [row["adverse"]["drug"] for row in rows],
            ["Alpha", "Beta", "Gamma"],
        )
        self.assertEqual(
            [row["trials"]["disease"] for row in rows],
            ["Control disease"] * 3,
        )
        self.assertEqual(
            [row["safety_layer1"]["molecule_id"] for row in rows],
            ["CHEMBL1", "CHEMBL2", "CHEMBL3"],
        )

    def test_heldout_trial_lookup_never_touches_network(self):
        holdout.activate(["ControlDrug"])
        with patch.object(
            clinicaltrials, "_search_trials",
            side_effect=AssertionError("held-out query reached network"),
        ):
            result = clinicaltrials.check_prior_trials(
                "ControlDrug", "Control disease"
            )

        self.assertTrue(result["holdout_redacted"])
        self.assertFalse(result["query_failed"])
        self.assertEqual(result["trials"], [])

    def test_heldout_salt_id_never_touches_trial_network(self):
        holdout.activate(["ParentDrug"])
        holdout.register_molecules(
            {"CHEMBLPARENT", "CHEMBLSALT"}, {"CHEMBLPARENT"}
        )
        holdout.mark_resolved()
        with patch.object(
            clinicaltrials, "_search_trials",
            side_effect=AssertionError("held-out salt query reached network"),
        ):
            result = clinicaltrials.check_prior_trials(
                "Different Salt Display Name",
                "Control disease",
                candidate_chembl_ids=["CHEMBLSALT"],
            )
        self.assertTrue(result["holdout_redacted"])

    def test_heldout_structure_variant_never_touches_trial_network(self):
        holdout.activate(["ParentDrug"])
        holdout.register_inchikeys({"ABCDEFGHIJKLMN-ABCDEFGHIJ-N"})
        with patch.object(
            clinicaltrials, "_search_trials",
            side_effect=AssertionError("held-out structure reached network"),
        ):
            result = clinicaltrials.check_prior_trials(
                "Different Variant Display Name",
                "Control disease",
                candidate_inchikey="ABCDEFGHIJKLMN-ZYXWVUTSRQ-N",
            )
        self.assertTrue(result["holdout_redacted"])

    def test_unrelated_structure_is_not_redacted(self):
        holdout.activate(["ParentDrug"])
        holdout.register_inchikeys({"ABCDEFGHIJKLMN-ABCDEFGHIJ-N"})
        with patch.object(
            clinicaltrials, "_search_trials", return_value=([], False)
        ):
            result = clinicaltrials.check_prior_trials(
                "UnrelatedDrug",
                "Control disease",
                candidate_inchikey="NOPQRSTUVWXYZA-ABCDEFGHIJ-N",
            )
        self.assertFalse(result["holdout_redacted"])

    def test_provider_native_id_not_sent_to_chembl_safety(self):
        candidate = {
            "drug_name": "Control",
            "molecule_chembl_id": "1234",
            "_evidence_ledger": {
                "records": [
                    {"provider": "drugcentral", "molecule_id": "1234"},
                    {"provider": "chembl", "molecule_id": "CHEMBL42"},
                ]
            },
        }
        seen = {}

        def safety(drug, molecule_id):
            seen["molecule_id"] = molecule_id
            return {}

        with patch.object(
            reviewer, "get_adverse_events",
            return_value={"adverse_events": []},
        ), patch.object(
            reviewer, "check_prior_trials",
            return_value={"trials": [], "query_failed": False},
        ), patch.object(
            reviewer, "get_compound_data",
            return_value={"xlogp": None},
        ), patch.object(
            reviewer, "get_molecule_safety_flags", safety
        ):
            reviewer._prefetch_candidate_context(
                [candidate], "Control disease"
            )
        self.assertEqual(seen["molecule_id"], "CHEMBL42")

    def test_trial_credit_withheld_when_holdout_redacted(self):
        self.assertFalse(reviewer._no_failed_trial_credit({
            "query_failed": False,
            "holdout_redacted": True,
            "has_negative_repurposing_result": False,
        }))

    def test_trial_credit_semantics_unchanged_when_visible(self):
        self.assertTrue(reviewer._no_failed_trial_credit({
            "query_failed": False,
            "holdout_redacted": False,
            "has_negative_repurposing_result": False,
        }))
        self.assertFalse(reviewer._no_failed_trial_credit({
            "query_failed": False,
            "holdout_redacted": False,
            "has_negative_repurposing_result": True,
        }))

    def test_holdout_context_visible_in_trial_worker(self):
        candidate = {
            "drug_name": "ControlDrug",
            "molecule_chembl_id": "CHEMBL99",
        }

        def trials(
            drug, disease, candidate_chembl_ids=None,
            candidate_inchikey=None,
        ):
            self.assertTrue(holdout.is_active())
            self.assertTrue(holdout.matches_name(drug))
            return {"trials": [], "holdout_redacted": True}

        holdout.activate(["ControlDrug"])
        with patch.object(
            reviewer, "get_adverse_events",
            return_value={"adverse_events": []},
        ), patch.object(
            reviewer, "check_prior_trials", trials
        ), patch.object(
            reviewer, "get_compound_data",
            return_value={"xlogp": None},
        ), patch.object(
            reviewer, "get_molecule_safety_flags",
            return_value={},
        ):
            rows = reviewer._prefetch_candidate_context(
                [candidate], "Control disease"
            )
        self.assertTrue(rows[0]["trials"]["holdout_redacted"])


if __name__ == "__main__":
    unittest.main()