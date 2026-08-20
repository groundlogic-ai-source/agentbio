"""Machine-v2 mechanism-only lane regressions.

Guards the three conventions the lane relies on:
  1. pool_origin/mechanism_of_action/action_type survive the run_chemist
     projection into chembl_enriched (a 2026-08 review found the projection
     silently dropped them, defeating the lane's disclosure purpose);
  2. a mechanism-only row becomes a MECHANISM ledger record, never a
     null-pChEMBL BIOACTIVITY_ASSAY record (evidence-integrity);
  3. get_mechanism_only_approved_drugs caches empty results only when a
     genuine mechanism payload was observed (ChEMBL degraded-200 empties
     must not freeze as "no mechanism drugs" for 30 days).
"""

import os
import unittest
from unittest.mock import MagicMock, patch

import agents.chemist as chemist_mod
import data_sources.chembl as chembl_mod
from data_sources.multisource_candidates import normalize_chembl_enriched

MECH_ROW = {
    "molecule_chembl_id": "CHEMBL1201585",
    "pref_name": "INFLIXIMAB",
    "max_phase": "4.0",
    "molecule_type": "Antibody",
    "canonical_smiles": None,
    "pchembl_value": None,
    "confidence_score": None,
    "n_activities": 0,
    "source_activity_ids": [],
    "source_assay_ids": [],
    "source_chembl_ids": ["CHEMBL1201585"],
    "pool_origin": "mechanism_only",
    "mechanism_of_action": "Tumor necrosis factor alpha inhibitor",
    "action_type": "ANTIBODY BINDING",
}


def _record_types(records, molecule_id):
    return [r.source_type.value for r in records if r.molecule_id == molecule_id]


class NormalizeRecordTest(unittest.TestCase):
    def test_mechanism_only_row_yields_mechanism_record_not_bioactivity(self):
        enriched = dict(MECH_ROW, drug_name="INFLIXIMAB", smiles=None,
                        inchikey=None, target_symbol="TNF",
                        uniprot_id="P01375", disease_name="X",
                        ot_association_score=0.0,
                        target_discovery_method="genetic_association")
        types = _record_types(normalize_chembl_enriched([enriched]),
                              "CHEMBL1201585")
        self.assertIn("mechanism", types)
        self.assertNotIn("bioactivity_assay", types)
        self.assertIn("regulatory_approval", types)  # max_phase 4 preserved

    def test_normal_activity_row_still_yields_bioactivity_record(self):
        normal = dict(MECH_ROW, molecule_chembl_id="CHEMBL25",
                      drug_name="ASPIRIN", pool_origin=None,
                      pchembl_value=6.5, confidence_score=9,
                      smiles="CC(=O)Oc1ccccc1C(=O)O", inchikey=None,
                      target_symbol="PTGS1", uniprot_id="P23219",
                      disease_name="X", ot_association_score=0.0,
                      target_discovery_method="genetic_association")
        types = _record_types(normalize_chembl_enriched([normal]), "CHEMBL25")
        self.assertIn("bioactivity_assay", types)
        self.assertNotIn("mechanism", types)


class ChemistProjectionTest(unittest.TestCase):
    """End-to-end through run_chemist with network/LLM seams mocked."""

    def _fake_enrich(self, compounds, symbol, uniprot, disease_name,
                     ot_score, disc_method):
        out = []
        for c in compounds:
            d = dict(c)
            d.update({
                "smiles": c.get("canonical_smiles"),
                "inchikey": None,
                "atc_codes": [],
                "pubchem_known_drug": False,
                "is_approved_drug": True,
                "drug_name": c.get("pref_name") or c["molecule_chembl_id"],
                "target_symbol": symbol,
                "uniprot_id": uniprot,
                "ot_association_score": ot_score,
                "disease_name": disease_name,
                "target_discovery_method": disc_method,
            })
            out.append(d)
        return out

    def test_pool_origin_survives_projection_into_ledger_input(self):
        captured: dict = {}

        def fake_collect(**kwargs):
            captured["chembl_enriched"] = list(kwargs["chembl_enriched"])
            return {"candidates": list(kwargs["chembl_enriched"]),
                    "source_status": {}}

        with patch.object(chemist_mod, "get_target_candidate_compounds",
                          return_value={"compounds": [],
                                        "pooled_across_multiple_targets":
                                        False}), \
             patch.object(chemist_mod, "get_mechanism_only_approved_drugs",
                          return_value=[dict(MECH_ROW)]), \
             patch.object(chemist_mod, "_enrich_compounds",
                          side_effect=self._fake_enrich), \
             patch.object(chemist_mod, "get_pathway_neighbor_targets",
                          return_value=[]), \
             patch.object(chemist_mod, "collect_target_candidates",
                          side_effect=fake_collect), \
             patch.object(chemist_mod, "_label_mechanism_record",
                          return_value=None), \
             patch.object(chemist_mod, "provenance", MagicMock()), \
             patch.dict(os.environ, {"AGENTBIO_MAX_LLM_RATIONALES": "0"},
                        clear=False):
            out = chemist_mod.run_chemist(
                {"target": {"uniprot_id": "P01375", "target_symbol": "TNF",
                            "disease_name": "Ulcerative Colitis"}})

        enriched = captured["chembl_enriched"]
        mech = next(r for r in enriched
                    if r["molecule_chembl_id"] == "CHEMBL1201585")
        self.assertEqual(mech.get("pool_origin"), "mechanism_only")
        self.assertEqual(mech.get("mechanism_of_action"),
                         "Tumor necrosis factor alpha inhibitor")
        self.assertEqual(mech.get("action_type"), "ANTIBODY BINDING")

        # The ledger sees the true evidence type for this row.
        types = _record_types(normalize_chembl_enriched(enriched),
                              "CHEMBL1201585")
        self.assertIn("mechanism", types)
        self.assertNotIn("bioactivity_assay", types)

        # And the final candidate list still carries the disclosure fields.
        final = next(c for c in out["candidates"]
                     if c["molecule_chembl_id"] == "CHEMBL1201585")
        self.assertEqual(final.get("pool_origin"), "mechanism_only")


class MechanismLaneCacheTest(unittest.TestCase):
    def _run_lane(self, get_json_side_effect):
        with patch.object(chembl_mod, "get", return_value=None), \
             patch.object(chembl_mod, "cache_set") as mock_set, \
             patch.object(chembl_mod, "_resolve_target_chembl_id",
                          return_value=["CHEMBL2056"]), \
             patch.object(chembl_mod, "_get_json",
                          side_effect=get_json_side_effect):
            result = chembl_mod.get_mechanism_only_approved_drugs("P01375")
        return result, mock_set

    def test_empty_mechanism_payload_is_not_cached(self):
        # Degraded-200 ambiguity: empty mechanisms list, nothing else seen.
        result, mock_set = self._run_lane(
            lambda url, params=None: {"mechanisms": []})
        self.assertEqual(result, [])
        mock_set.assert_not_called()

    def test_genuine_empty_after_nonempty_payload_is_cached(self):
        # Mechanism row exists but the molecule is unapproved → genuine empty.
        def side_effect(url, params=None):
            if "mechanism.json" in url:
                return {"mechanisms": [{
                    "molecule_chembl_id": "CHEMBL999",
                    "mechanism_of_action": "Some target modulator",
                    "action_type": "MODULATOR"}]}
            return {"max_phase": 1.0, "pref_name": "TOOLEARLY",
                    "molecule_type": "Small molecule",
                    "molecule_structures": {"canonical_smiles": "CC"}}
        result, mock_set = self._run_lane(side_effect)
        self.assertEqual(result, [])
        mock_set.assert_called_once()
        self.assertEqual(mock_set.call_args[0][1], [])

    def test_partial_degraded_multi_target_response_is_not_cached(self):
        # UniProt maps to TWO ChEMBL target IDs: one returns rows, the other
        # an ambiguous empty (degraded-200 mode). The aggregate must not be
        # cached — a later run must retry the degraded endpoint.
        def side_effect(url, params=None):
            tid = (params or {}).get("target_chembl_id", "")
            if tid == "CHEMBL2056":
                return {"mechanisms": [{
                    "molecule_chembl_id": "CHEMBL999",
                    "mechanism_of_action": "Some target modulator",
                    "action_type": "MODULATOR"}]}
            return {"mechanisms": []}  # ambiguous empty for the second tid
        with patch.object(chembl_mod, "get", return_value=None), \
             patch.object(chembl_mod, "cache_set") as mock_set, \
             patch.object(chembl_mod, "_resolve_target_chembl_id",
                          return_value=["CHEMBL2056", "CHEMBL9999"]), \
             patch.object(chembl_mod, "_get_json",
                          side_effect=side_effect):
            chembl_mod.get_mechanism_only_approved_drugs("P01375")
        mock_set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
