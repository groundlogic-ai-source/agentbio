import unittest
from unittest import mock

from agents import target_selection
from data_sources import reactome


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
            "specificity_tier": "specific",
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


if __name__ == "__main__":
    unittest.main()