"""Focused deterministic regressions for task 70 dossier provenance rules."""

import unittest

from agents.reviewer import (
    _auditable_compatible_direction,
    _build_dossier_evidence_contract,
    _target_matched_chembl_activity_ids,
)
from agents.writer import _citations, _direct_chembl_activity_note
from data_sources.evidence_ledger import merge_candidates


class Task70ProvenanceTests(unittest.TestCase):
    def test_activity_ids_exclude_metadata_and_other_providers(self):
        candidate = {
            "target_symbol": "CACNA1C", "uniprot_id": "Q13936",
            "_evidence_ledger": {"records": [
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-pchembl:x", "source_activity_ids": ["1"],
                 "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
                 "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-confidence:x", "source_activity_ids": ["1"],
                 "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
                 "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "mechanism",
                 "source_id": "CHEMBL_MECH_9", "target_symbol": "CACNA1C"},
                {"provider": "drugcentral", "source_type": "bioactivity_assay",
                 "source_id": "DC_44", "target_symbol": "CACNA1C"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-pchembl:y", "source_activity_ids": ["2"],
                 "target_symbol": "KCNH2", "target_species": "Homo sapiens",
                 "qualification_status": "qualified"},
            ]},
        }
        self.assertEqual(_target_matched_chembl_activity_ids(candidate),
                         ["1"])

    def test_writer_never_labels_drugcentral_id_as_chembl_activity(self):
        candidate = {
            "provenance": {"counted_once": [
                {"source_type": "chembl_activity", "source_id": "DC_44"}]},
            "_evidence_ledger": {"records": [{
                "provider": "drugcentral", "source_type": "bioactivity_assay",
                "source_id": "DC_44"}]},
        }
        self.assertEqual(_citations(candidate, None)["chembl_activity_ids"], [])

    def test_direct_note_counts_independent_source_identity(self):
        candidate = {
            "target_symbol": "CACNA1C",
            "_evidence_ledger": {"records": [
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-pchembl:x", "source_activity_ids": ["1"],
                  "target_symbol": "CACNA1C",
                 "target_species": "Homo sapiens", "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-confidence:x", "source_activity_ids": ["1"],
                  "target_symbol": "CACNA1C",
                 "target_species": "Homo sapiens", "qualification_status": "qualified"},
            ]},
        }
        self.assertIn("1 independent qualified", _direct_chembl_activity_note(candidate))

    def test_bonus_requires_complete_compatible_audit(self):
        base = {"verdict": "DIRECTIONALLY_COMPATIBLE", "action_type_used": "BLOCKER",
                "disease_mechanism_summary": "summary", "reason": "reason",
                "search_citations": "PMID:1"}
        self.assertTrue(_auditable_compatible_direction(base))
        base["search_citations"] = ""
        self.assertFalse(_auditable_compatible_direction(base))

    def test_merged_source_activity_ids_are_chembl_activity_only(self):
        rows = merge_candidates([
            {"provider": "chembl", "source_type": "bioactivity_assay",
             "source_id": "ACT1", "molecule_id": "CHEMBL1",
             "molecule_name": "x"},
            {"provider": "drugcentral", "source_type": "mechanism",
             "source_id": "DC1", "molecule_id": "CHEMBL1",
             "molecule_name": "x"},
        ])
        self.assertEqual(rows[0]["source_activity_ids"], ["ACT1"])

    def test_timothy_scope_is_structured_and_explicitly_future(self):
        candidate = {
            "drug_name": "Nisoldipine", "target_symbol": "CACNA1C",
            "disease_name": "Timothy syndrome", "strong_match": False,
            "_evidence_ledger": {"records": []},
        }
        contract = _build_dossier_evidence_contract(candidate, None, [candidate])
        scope = contract["timothy_syndrome_cardiac_scope"]
        self.assertEqual(scope["proposed_variant_scope"], "TS1 CACNA1C p.G406R")
        self.assertEqual(scope["proposed_exon_scope"], "8A")
        self.assertEqual(scope["genotype_confirmation_status"],
                         "UNCONFIRMED_BY_PIPELINE")
        self.assertIn("iPSC", " ".join(scope["required_tests"]))


if __name__ == "__main__":
    unittest.main()