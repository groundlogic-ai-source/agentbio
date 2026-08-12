"""Non-network controls for bounded Reviewer enrichment prefetch."""

import contextlib
import io
import threading
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

    def test_liveness_beat_fires_while_first_item_blocked(self):
        """executor.map yields in input order, so a wedged FIRST item must
        not silence the liveness heartbeat (the production failure mode)."""
        release = threading.Event()
        candidates = [
            {"drug_name": "Stuck", "molecule_chembl_id": "CHEMBL1"},
            {"drug_name": "QuickA", "molecule_chembl_id": "CHEMBL2"},
            {"drug_name": "QuickB", "molecule_chembl_id": "CHEMBL3"},
        ]

        def adverse(drug):
            if drug == "Stuck":
                release.wait(10)
            return {"drug": drug, "adverse_events": []}

        def trials(drug, disease, candidate_chembl_ids=None,
                   candidate_inchikey=None):
            return {"drug": drug, "disease": disease, "trials": []}

        def pubchem(drug):
            return {"drug": drug, "xlogp": None}

        def safety(drug, molecule_id):
            return {"drug": drug, "molecule_id": molecule_id}

        def molecule(drug):
            return {"drug": drug}

        buf = io.StringIO()
        result = {}

        def run():
            with patch.object(reviewer, "_PREFETCH_HEARTBEAT_SECONDS", 0.05), \
                    patch.object(reviewer, "get_adverse_events", adverse), \
                    patch.object(reviewer, "check_prior_trials", trials), \
                    patch.object(reviewer, "get_compound_data", pubchem), \
                    patch.object(reviewer, "get_molecule_safety_flags", safety), \
                    patch.object(reviewer, "get_molecule_data", molecule), \
                    contextlib.redirect_stdout(buf):
                result["rows"] = reviewer._prefetch_candidate_context(
                    candidates, "Control disease"
                )

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.4)  # several beat intervals while "Stuck" is blocked
        release.set()
        worker.join(10)

        self.assertFalse(worker.is_alive())
        self.assertIn("prefetch alive", buf.getvalue())
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(
            [row["adverse"]["drug"] for row in result["rows"]],
            ["Stuck", "QuickA", "QuickB"],
        )

    def test_stalled_prefetch_self_terminates(self):
        """Zero lane progress past the stall budget must trigger the exit
        hook so the study supervisor restarts the run (cache makes the
        retry cheap).  os._exit is mocked; the run then completes."""
        release = threading.Event()
        candidates = [
            {"drug_name": "Stuck", "molecule_chembl_id": "CHEMBL1"},
            {"drug_name": "Quick", "molecule_chembl_id": "CHEMBL2"},
        ]

        def adverse(drug):
            if drug == "Stuck":
                release.wait(10)
            return {"drug": drug, "adverse_events": []}

        def trials(drug, disease, candidate_chembl_ids=None,
                   candidate_inchikey=None):
            return {"drug": drug, "disease": disease, "trials": []}

        def pubchem(drug):
            return {"drug": drug, "xlogp": None}

        def safety(drug, molecule_id):
            return {"drug": drug, "molecule_id": molecule_id}

        def molecule(drug):
            return {"drug": drug}

        exits = []

        def run():
            with patch.object(reviewer, "_PREFETCH_HEARTBEAT_SECONDS", 0.05), \
                    patch.object(reviewer, "_PREFETCH_STALL_EXIT_SECONDS",
                                 0.2), \
                    patch.object(reviewer.os, "_exit", exits.append), \
                    patch.object(reviewer, "get_adverse_events", adverse), \
                    patch.object(reviewer, "check_prior_trials", trials), \
                    patch.object(reviewer, "get_compound_data", pubchem), \
                    patch.object(reviewer, "get_molecule_safety_flags",
                                 safety), \
                    patch.object(reviewer, "get_molecule_data", molecule), \
                    contextlib.redirect_stdout(io.StringIO()):
                reviewer._prefetch_candidate_context(
                    candidates, "Control disease")

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(1.0)  # beats every 0.05s, stall budget 0.2s, one blocked
        self.assertTrue(exits, "stall exit never fired")
        release.set()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertIn(86, exits)

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

    def test_trial_term_is_unobserved_when_holdout_redacted(self):
        # None (coverage gap), NOT False.  False would score an unmade
        # observation exactly like a discovered failed trial.
        self.assertIsNone(reviewer._trial_evidence_term({
            "query_failed": False,
            "holdout_redacted": True,
            "has_negative_repurposing_result": False,
        }))

    def test_trial_term_is_unobserved_when_query_failed(self):
        self.assertIsNone(reviewer._trial_evidence_term({
            "query_failed": True,
            "holdout_redacted": False,
            "has_negative_repurposing_result": False,
        }))

    def test_trial_term_semantics_unchanged_when_visible(self):
        self.assertTrue(reviewer._trial_evidence_term({
            "query_failed": False,
            "holdout_redacted": False,
            "has_negative_repurposing_result": False,
        }))
        # A MEASURED failed trial is still adverse evidence and still False.
        self.assertFalse(reviewer._trial_evidence_term({
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