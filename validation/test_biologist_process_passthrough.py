"""Biologist must preserve disease-derived process provenance for Chemist."""

import unittest
from unittest.mock import patch

from agents import biologist


class BiologistProcessPassthroughTest(unittest.TestCase):
    def test_process_metadata_survives_target_handoff(self):
        support = [{
            "pmid": "12345",
            "title": "Cited process evidence",
            "evidence_sentence": "A qualified process assertion.",
        }]
        target = {
            "target_symbol": "GABRA1",
            "uniprot_id": "P14867",
            "disease_name": "Lennox-Gastaut syndrome",
            "target_discovery_method": "literature_mechanism_class",
            "mechanism_class": "inhibitory_neurotransmission_gaba_a",
            "therapeutic_role": "symptom_treatment",
            "process_support": support,
            "process_query": "disease-only query",
            "process_source_status": "ok",
            "process_ontology_version": "v6",
            "process_target_priority": 1,
            "process_class_priority": 2,
        }
        with patch.object(
            biologist, "get_interactions",
            return_value={"query_status": "ok", "interactions": []},
        ), patch.object(
            biologist, "search_literature",
            return_value={"literature_hits": []},
        ), patch.object(
            biologist, "_anthropic_client", return_value=None,
        ), patch.object(
            biologist, "get_druggability_literature",
            return_value={
                "has_approved_drug_for_target": False,
                "approved_drug_count": 0,
                "approved_drugs": [],
                "difficulty_summary": None,
                "supporting_pmids": [],
                "druggability_flag": "insufficient literature signal",
            },
        ), patch.object(
            biologist, "get_pathway_neighbor_targets", return_value=[],
        ), patch.object(
            biologist.provenance, "log_many",
        ):
            output = biologist.run_biologist(target)

        returned = output["target"]
        for key in (
            "mechanism_class",
            "therapeutic_role",
            "process_support",
            "process_query",
            "process_source_status",
            "process_ontology_version",
            "process_target_priority",
            "process_class_priority",
        ):
            self.assertEqual(returned[key], target[key])


if __name__ == "__main__":
    unittest.main()