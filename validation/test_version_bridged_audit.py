"""Offline guards for supplied-drug audit scope and stable target identity."""
from __future__ import annotations

import unittest
from unittest import mock
from pathlib import Path
import hashlib
import json

from api import audit, triage
from data_sources import chembl


def _identity(
    *,
    genes: list[str],
    uniprots: list[str],
    target_name: str = "Test target",
) -> dict:
    return {
        "target_chembl_id": "CHEMBL_TARGET",
        "target_name": target_name,
        "target_type": "SINGLE PROTEIN",
        "organism": "Homo sapiens",
        "tax_id": 9606,
        "uniprot_ids": uniprots,
        "gene_symbols": genes,
        "mechanisms": [f"{target_name} inhibitor"],
        "action_types": ["INHIBITOR"],
    }


class StructuredMechanismIdentityTest(unittest.TestCase):
    def _lookup(self, target_id: str, accession: str, gene: str, name: str):
        def get_json(url, params=None):
            if "mechanism.json" in url:
                return {"mechanisms": [{
                    "target_chembl_id": target_id,
                    "mechanism_of_action": f"{name} inhibitor",
                    "action_type": "INHIBITOR",
                }]}
            return {
                "target_chembl_id": target_id,
                "pref_name": name,
                "target_type": "SINGLE PROTEIN",
                "organism": "Homo sapiens",
                "tax_id": 9606,
                "target_components": [{
                    "accession": accession,
                    "target_component_synonyms": [{
                        "component_synonym": gene,
                        "syn_type": "GENE_SYMBOL",
                    }],
                }],
            }

        with mock.patch.object(chembl, "get", return_value=None), \
             mock.patch.object(chembl, "cache_set") as cache_set, \
             mock.patch.object(chembl, "_get_json", side_effect=get_json):
            result = chembl.get_drug_mechanism_identities_for_audit(
                "Test drug", "CHEMBL_DRUG")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["targets"][0]["uniprot_ids"], [accession])
        self.assertEqual(result["targets"][0]["gene_symbols"], [gene])
        cache_set.assert_called_once()
        return result

    def test_glucocorticoid_label_aligns_to_nr3c1(self):
        result = self._lookup(
            "CHEMBL2034", "P04150", "NR3C1", "Glucocorticoid receptor")
        diagnostics = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "NR3C1", "uniprot_id": "P04150"}],
            mechanism_evidence=result,
            molecule_type="Small molecule",
        )
        self.assertEqual(diagnostics["audit_scope_status"],
                         "auditable_only_because_supplied")
        self.assertEqual(diagnostics["deterministic_miss_reason"],
                         "ASSAY_POOL_GAP")
        self.assertTrue(diagnostics["pool_target_overlap"])

    def test_tnf_display_label_aligns_to_uniprot(self):
        result = self._lookup(
            "CHEMBL1825", "P01375", "TNF", "Tumor necrosis factor")
        diagnostics = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "TNF", "uniprot_id": "P01375"}],
            mechanism_evidence=result,
            molecule_type="Antibody",
        )
        self.assertEqual(diagnostics["deterministic_miss_reason"],
                         "BIOLOGIC_STRUCTURAL")

    def test_transport_failure_is_not_cached_or_called_no_mechanism(self):
        with mock.patch.object(chembl, "get", return_value=None), \
             mock.patch.object(chembl, "cache_set") as cache_set, \
             mock.patch.object(
                 chembl, "_get_json", side_effect=TimeoutError("degraded")):
            result = chembl.get_drug_mechanism_identities_for_audit(
                "Test drug", "CHEMBL_DRUG")
        self.assertEqual(result["status"], "unavailable")
        cache_set.assert_not_called()
        diagnostics = audit.build_audit_scope_diagnostics(
            "absent", candidates=[], mechanism_evidence=result)
        self.assertEqual(diagnostics["audit_scope_status"], "source_failure")
        self.assertIsNone(diagnostics["deterministic_miss_reason"])


class SuppliedDrugAuditContractTest(unittest.TestCase):
    def test_supplied_only_diagnostics_never_create_rank_score_or_candidate(self):
        evidence = {
            "status": "ok",
            "provider": "chembl",
            "targets": [_identity(genes=["NR3C1"], uniprots=["P04150"])],
        }
        result = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "JAK1", "uniprot_id": "P23458"}],
            mechanism_evidence=evidence,
            molecule_type="Small molecule",
            identity_route="chembl_name_resolution",
        )
        self.assertEqual(result["audit_scope_status"],
                         "auditable_only_because_supplied")
        self.assertEqual(result["deterministic_miss_reason"],
                         "TARGET_NOT_SELECTED")
        self.assertIsNone(result["supplied_drug_discovery_rank"])
        self.assertIsNone(result["supplied_drug_discovery_score"])
        self.assertFalse(result["supplied_drug_candidate_inserted"])

    def test_description_without_stable_identity_is_no_mechanism_data(self):
        result = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "JAK1", "uniprot_id": "P23458"}],
            mechanism_evidence={
                "status": "ok",
                "provider": "chembl",
                "targets": [{
                    "gene_symbols": [],
                    "uniprot_ids": [],
                    "mechanisms": ["Unknown"],
                }],
            },
        )
        self.assertEqual(result["deterministic_miss_reason"],
                         "NO_MECHANISM_DATA")

    def test_nonhuman_symbol_match_is_not_stable_identity_overlap(self):
        result = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "TNF", "uniprot_id": "P01375"}],
            mechanism_evidence={
                "status": "ok",
                "provider": "chembl",
                "targets": [{
                    "target_chembl_id": "CHEMBL_MOUSE_TNF",
                    "target_type": "SINGLE PROTEIN",
                    "organism": "Mus musculus",
                    "tax_id": 10090,
                    "gene_symbols": ["TNF"],
                    "uniprot_ids": ["P06804"],
                    "mechanisms": ["TNF inhibitor"],
                }],
            },
        )
        self.assertEqual(result["pool_target_overlap"], [])
        self.assertEqual(result["stable_identity_status"],
                         "nonhuman_or_nonprotein_only")

    def test_complex_component_match_is_disclosed_but_not_exact_overlap(self):
        result = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{"target_symbol": "TUBB", "uniprot_id": "P07437"}],
            mechanism_evidence={
                "status": "ok",
                "provider": "chembl",
                "targets": [{
                    "target_chembl_id": "CHEMBL_TUBULIN_COMPLEX",
                    "target_type": "PROTEIN COMPLEX",
                    "organism": "Homo sapiens",
                    "tax_id": 9606,
                    "gene_symbols": ["TUBB"],
                    "uniprot_ids": ["P07437"],
                    "mechanisms": ["Tubulin inhibitor"],
                }],
            },
        )
        self.assertEqual(result["pool_target_overlap"], [])
        self.assertTrue(result["pool_component_target_overlap"])
        self.assertEqual(result["stable_identity_status"], "component_only")

    def test_matching_chembl_target_id_is_supported_for_direct_protein(self):
        result = audit.build_audit_scope_diagnostics(
            "absent",
            candidates=[{
                "target_symbol": "OTHER",
                "uniprot_id": "P00000",
                "target_chembl_id": "CHEMBL2034",
            }],
            mechanism_evidence={
                "status": "ok",
                "provider": "chembl",
                "targets": [{
                    "target_chembl_id": "CHEMBL2034",
                    "target_type": "SINGLE PROTEIN",
                    "organism": "Homo sapiens",
                    "tax_id": 9606,
                    "gene_symbols": [],
                    "uniprot_ids": [],
                    "mechanisms": ["Glucocorticoid receptor agonist"],
                }],
            },
        )
        self.assertTrue(result["pool_target_overlap"])


class FrozenInputIntegrityTest(unittest.TestCase):
    def test_target_universe_cache_is_pinned_in_manifest(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads(
            (root / "validation" / "version_bridged_audit_manifest.json").read_text())
        cache = root / "validation" / ".machine_v2_acceptance_cache.json"
        actual = hashlib.sha256(cache.read_bytes()).hexdigest()
        self.assertEqual(actual, manifest["target_universe_cache_sha256"])

    @mock.patch.object(audit, "domain_findings_for", return_value=[])
    @mock.patch.object(audit, "build_audit_context", return_value={"sources": {}})
    @mock.patch.object(audit, "_modality_payload", return_value={
        "chembl_molecule_type": "Small molecule",
        "chembl_oral": True,
        "modality_findings": [],
        "modality_status": "clear",
    })
    @mock.patch.object(
        audit, "get_drug_mechanism_identities_for_audit",
        return_value={
            "status": "ok",
            "provider": "chembl",
            "targets": [_identity(genes=["NR3C1"], uniprots=["P04150"])],
        },
    )
    @mock.patch.object(audit, "_find_molecule_chembl_id",
                       return_value="CHEMBL632")
    @mock.patch.object(audit, "_load_candidates", return_value=[{
        "drug_name": "OTHER",
        "molecule_chembl_id": "CHEMBL_OTHER",
        "target_symbol": "JAK1",
        "uniprot_id": "P23458",
        "composite_score": 0.8,
    }])
    @mock.patch.object(
        audit.jobs_db, "find_completed_job_by_disease",
        return_value={"job_id": "job-1", "disease_name": "Lupus"},
    )
    def test_absent_audit_uses_supplied_drug_mechanism_for_context(
        self, find_job, load, resolve, mechanism, modality, context, findings,
    ):
        result = audit.run_audit(
            "Lupus", "Betamethasone", narrate=False)
        self.assertEqual(result["status"], "absent")
        self.assertEqual(result["audit_scope_status"],
                         "auditable_only_because_supplied")
        self.assertNotIn("rank", result)
        self.assertNotIn("candidate", result)
        self.assertIsNone(result["supplied_drug_discovery_score"])
        self.assertEqual(
            context.call_args.kwargs["mechanism_symbol"], "NR3C1")

    def test_triage_preserves_scope_separately_from_absence(self):
        single = {
            "status": "absent",
            "audit_scope_status": "auditable_only_because_supplied",
            "deterministic_miss_reason": "TARGET_NOT_SELECTED",
            "resolved_chembl_id": "CHEMBL632",
            "stable_mechanism_identities": [
                _identity(genes=["NR3C1"], uniprots=["P04150"])],
            "pool_target_overlap": [],
            "target_coverage_ladder": [],
            "audit_context": {"sources": {}},
        }
        with mock.patch.object(triage, "run_audit", return_value=single), \
             mock.patch.object(
                 triage.triage_db,
                 "save_triage_run",
                 return_value={"id": "run-1", "created_at": None},
             ):
            result = triage.run_triage("Lupus", ["Betamethasone"])
        row = result["verdicts"][0]
        self.assertEqual(row["status"], "absent")
        self.assertEqual(row["audit_scope_status"],
                         "auditable_only_because_supplied")
        self.assertIn("ABSENT_FROM_POOL", row["flags"])
        self.assertIn("AUDITABLE_SUPPLIED_ONLY", row["flags"])
        self.assertEqual(
            result["summary"]["by_audit_scope_status"],
            {"auditable_only_because_supplied": 1},
        )


if __name__ == "__main__":
    unittest.main()