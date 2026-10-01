"""Focused deterministic regressions for task 70 dossier provenance rules."""

import unittest
from unittest import mock

from agents.reviewer import (
    _auditable_compatible_direction,
    _build_dossier_evidence_contract,
    _target_matched_chembl_activity_ids,
)
from agents.writer import _citations, _direct_chembl_activity_note
from data_sources.evidence_ledger import merge_candidates
from data_sources import chembl
from data_sources.multisource_candidates import (
    _ot_disease_link,
    normalize_chembl_enriched,
)
from data_sources.evidence_ledger import merge_candidates


class Task70ProvenanceTests(unittest.TestCase):
    def test_activity_ids_exclude_metadata_and_other_providers(self):
        candidate = {
            "target_symbol": "CACNA1C", "uniprot_id": "Q13936",
            "_evidence_ledger": {"records": [
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-pchembl:x", "source_activity_ids": ["1"],
                 "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
                  "target_evidence_scope": "target_qualified",
                 "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-confidence:x", "source_activity_ids": ["1"],
                 "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
                  "target_evidence_scope": "target_qualified",
                 "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "mechanism",
                 "source_id": "CHEMBL_MECH_9", "target_symbol": "CACNA1C"},
                {"provider": "drugcentral", "source_type": "bioactivity_assay",
                 "source_id": "DC_44", "target_symbol": "CACNA1C"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-pchembl:y", "source_activity_ids": ["2"],
                 "target_symbol": "KCNH2", "target_species": "Homo sapiens",
                  "target_evidence_scope": "target_qualified",
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
                  "target_species": "Homo sapiens",
                  "target_evidence_scope": "target_qualified",
                  "qualification_status": "qualified"},
                {"provider": "chembl", "source_type": "bioactivity_assay",
                 "source_id": "chembl-confidence:x", "source_activity_ids": ["1"],
                  "target_symbol": "CACNA1C",
                  "target_species": "Homo sapiens",
                  "target_evidence_scope": "target_qualified",
                  "qualification_status": "qualified"},
            ]},
        }
        self.assertIn("1 distinct qualified", _direct_chembl_activity_note(candidate))

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

    def test_raw_activity_ids_survive_ledger_serialization(self):
        rows = merge_candidates([
            {"provider": "chembl", "source_type": "bioactivity_assay",
             "source_id": "chembl-pchembl:CHEMBL1726:15373265",
             "source_activity_ids": ["15373265"],
             "measurement_type": "pchembl",
             "molecule_id": "CHEMBL1726", "molecule_name": "nisoldipine",
             "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
             "qualification_status": "qualified"},
            {"provider": "chembl", "source_type": "bioactivity_assay",
             "source_id": "chembl-confidence:CHEMBL1726:15373265",
             "source_activity_ids": ["15373265"],
             "measurement_type": "assay_confidence",
             "molecule_id": "CHEMBL1726", "molecule_name": "nisoldipine",
             "target_symbol": "CACNA1C", "target_species": "Homo sapiens",
             "qualification_status": "qualified"},
        ])
        candidate = rows[0]
        self.assertEqual(candidate["source_activity_ids"], ["15373265"])
        self.assertEqual(
            candidate["_evidence_ledger"]["records"][0]["source_activity_ids"],
            ["15373265"],
        )
        self.assertEqual(
            _target_matched_chembl_activity_ids(candidate), ["15373265"])
        self.assertIn("1 distinct qualified",
                      _direct_chembl_activity_note(candidate))

    def test_comparator_table_collapses_salt_forms(self):
        from agents.writer import _comparator_table

        candidate = {"dossier_evidence_contract": {"comparators": {
            "target_approved_drugs": [
                {"name": "AMLODIPINE BENZOATE", "max_phase": 4},
                {"name": "AMLODIPINE BESYLATE", "max_phase": 4},
                {"name": "AMLODIPINE MALEATE", "max_phase": 4},
                {"name": "NIFEDIPINE", "max_phase": 4},
            ],
            "selected_candidates": [],
        }}}
        table = _comparator_table(candidate, None)
        self.assertEqual(table.count("| AMLODIPINE |"), 1)
        self.assertNotIn("BESYLATE", table)
        self.assertIn("| NIFEDIPINE |", table)

    def test_approved_target_drugs_collapse_to_parent_active_moiety(self):
        child_meta = {
            "CHEMBL_CHILD_A": {
                "max_phase": "4.0", "pref_name": "AMLODIPINE BENZOATE",
                "parent_chembl_id": "CHEMBL_PARENT",
            },
            "CHEMBL_CHILD_B": {
                "max_phase": "4.0", "pref_name": "AMLODIPINE BESYLATE",
                "parent_chembl_id": "CHEMBL_PARENT",
            },
        }
        parent_meta = {
            "CHEMBL_PARENT": {
                "max_phase": "4.0", "pref_name": "AMLODIPINE",
                "parent_chembl_id": "CHEMBL_PARENT",
            },
        }
        with (
            mock.patch.object(chembl, "get", return_value=None),
            mock.patch.object(chembl, "cache_set"),
            mock.patch.object(
                chembl, "_resolve_target_chembl_id",
                return_value=["CHEMBL_TARGET"],
            ),
            mock.patch.object(
                chembl, "_get_json",
                return_value={"mechanisms": [
                    {"molecule_chembl_id": "CHEMBL_CHILD_A"},
                    {"molecule_chembl_id": "CHEMBL_CHILD_B"},
                ]},
            ),
            mock.patch.object(
                chembl, "_fetch_molecule_meta",
                side_effect=lambda ids: (
                    parent_meta if ids == ["CHEMBL_PARENT"] else child_meta
                ),
            ),
        ):
            result = chembl.get_approved_drugs_for_target("Q13936")
        self.assertEqual(result["approved_drug_count"], 1)
        self.assertEqual(result["approved_drugs"][0]["name"], "AMLODIPINE")
        self.assertEqual(
            result["approved_drugs"][0]["source_molecule_chembl_ids"],
            ["CHEMBL_CHILD_A", "CHEMBL_CHILD_B"],
        )

    def test_open_targets_fact_has_provider_independent_lineage(self):
        base = {
            "molecule_id": "CHEMBL1726", "molecule_name": "nisoldipine",
            "target_symbol": "CACNA1C", "target_accession": "Q13936",
        }
        chembl_record = _ot_disease_link(
            provider="chembl", base=base, uniprot_id="Q13936",
            disease_name="Timothy syndrome", ot_score=0.827)
        bindingdb_record = _ot_disease_link(
            provider="bindingdb", base=base, uniprot_id="Q13936",
            disease_name="Timothy syndrome", ot_score=0.827)
        self.assertEqual(
            chembl_record.lineage_key(), bindingdb_record.lineage_key())

    def test_parent_identity_survives_chembl_ledger_round_trip(self):
        records = normalize_chembl_enriched([{
            "drug_name": "LEAD HYDROCHLORIDE",
            "molecule_chembl_id": "CHEMBL_CHILD",
            "parent_chembl_id": "CHEMBL_PARENT",
            "source_molecule_chembl_ids": [
                "CHEMBL_CHILD", "CHEMBL_PARENT"],
            "inchikey": "ABCDEFGHIJKLMN-UHFFFAOYSA-N",
            "pchembl_value": 7.2,
            "confidence_score": 9,
            "max_phase": 4,
            "target_symbol": "CACNA1C",
            "uniprot_id": "Q13936",
            "disease_name": "Timothy syndrome",
        }])
        candidate = merge_candidates(records)[0]
        self.assertEqual(candidate["parent_chembl_id"], "CHEMBL_PARENT")
        self.assertEqual(
            candidate["source_molecule_chembl_ids"],
            ["CHEMBL_CHILD", "CHEMBL_PARENT"],
        )
        self.assertTrue(all(
            row["parent_molecule_id"] == "CHEMBL_PARENT"
            for row in candidate["_evidence_ledger"]["records"]
        ))

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