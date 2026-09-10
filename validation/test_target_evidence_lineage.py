import unittest
from unittest import mock

from agents import target_selection
from data_sources import reactome


class ChemblDisabledScoringTests(unittest.TestCase):
    def test_score_pair_marks_chembl_disabled_without_measuring_zero(self):
        with mock.patch.object(
                target_selection, "get_target_bioactivity_count",
                side_effect=AssertionError(
                    "hypothesis-only scoring must not call ChEMBL")), \
             mock.patch.object(
                 target_selection, "get_structure_confidence",
                 return_value={"has_structure": True, "mean_pLDDT": 90.0}), \
             mock.patch.object(
                 target_selection, "check_prior_trials",
                 return_value={
                     "has_negative_repurposing_result": False,
                     "trial_count": 0,
                 }):
            row = target_selection._score_pair(
                disease_name="Example rare disease",
                target={
                    "target_symbol": "GENE1",
                    "uniprot_id": "P00001",
                },
                association_score=0.8,
                has_approved_treatment=False,
                prevalence=None,
                chembl_enabled=False,
            )

        self.assertEqual(row["chembl_activity_count"], 0)
        self.assertIsNone(row["median_pchembl"])
        self.assertEqual(row["chembl_activity_status"], "disabled")


class PathwayNeighborAssociationTests(unittest.TestCase):
    def test_neighbor_does_not_inherit_or_manufacture_association_score(self):
        direct = [{
            "target_symbol": "ABCC9",
            "uniprot_id": "O60706",
            "association_score": 0.91,
            "target_discovery_method": "genetic_association",
        }]
        neighbor = {
            "gene_name": "KCNJ8",
            "uniprot_id": "Q15842",
            "specificity_tier": "direct",
            "shared_pathway_names": ["ATP-sensitive potassium channel"],
            "pathway_count": 1,
        }
        with mock.patch.object(
                reactome, "get_pathway_neighbors", return_value=[neighbor]):
            expanded = target_selection._expand_pathway_neighbors(
                direct, log=lambda _message: None)

        self.assertEqual(len(expanded), 2)
        added = expanded[1]
        self.assertEqual(added["association_score"], 0.0)
        self.assertIsNone(added["own_target_association_score"])
        self.assertEqual(
            added["own_target_association_status"], "unavailable")
        self.assertIn("does not inherit", added["association_provenance"])

    def test_broad_or_missing_tier_neighbors_never_expand(self):
        direct = [{
            "target_symbol": "ABCC9", "uniprot_id": "O60706",
            "target_discovery_method": "genetic_association",
        }]
        neighbors = [
            {"gene_name": "ABCA10", "uniprot_id": "Q8WWZ7",
             "specificity_tier": "broad_metabolic"},
            {"gene_name": "ABCA12", "uniprot_id": "Q86UK0"},
        ]
        logs = []
        with mock.patch.object(
                reactome, "get_pathway_neighbors", return_value=neighbors):
            expanded = target_selection._expand_pathway_neighbors(
                direct, log=logs.append)
        self.assertEqual(expanded, direct)
        self.assertEqual(len(logs), 2)
        self.assertTrue(all("SKIP pathway-neighbor" in row for row in logs))


if __name__ == "__main__":
    unittest.main()