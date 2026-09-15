"""Regression tests for the report-audit corrections."""

import unittest

from agents import reviewer, writer
from api.main import _rewrite_structure_artifacts
from api.report_pdf import _plain_markdown


class ReportContractRepairTest(unittest.TestCase):
    def test_pharmacological_precedent_is_not_direct_disease_association(self):
        candidate = {
            "target_discovery_method": "pharmacological_precedent",
        }
        self.assertEqual(
            reviewer._target_applicability(candidate),
            "CROSS_TARGET_FUNCTIONALLY_SUPPORTED",
        )

    def test_exact_literature_trial_limitation_removes_no_failure_credit(self):
        candidate = {
            "score_components": {
                "efficacy_evidence": 0.9,
                "normalized_ot_association": 0.6,
                "normalized_tanimoto": 0.4,
                "no_failed_trial": 1,
                "qualified_directional_bonus": 0.0,
            },
            "pre_cap_score": 0.9,
            "composite_score": 0.9,
            "strong_match": True,
            "lipinski_penalty_applied": False,
            "unapproved_cap_applied": False,
            "mechanism_cap_applied": False,
            "safety_cap_applied": False,
            "literature_limitation": {
                "evidence": [{
                    "pmid": "40399303",
                    "publication_types": [
                        "Clinical Trial, Phase I",
                        "Journal Article",
                    ],
                    "mechanically_verified": True,
                    "exact_applicability": True,
                    "exact_use_label": "EXPLICIT_LIMITATION",
                }],
            },
        }
        clean_score, _ = reviewer._coverage_aware_composite(
            0.9, 0.6, 0.4, True)
        reviewer._reconcile_literature_trial_evidence(candidate)
        self.assertEqual(candidate["score_components"]["no_failed_trial"], 0)
        self.assertLess(candidate["composite_score"], clean_score)
        self.assertEqual(
            candidate["trial_audit"]["literature_trial_pmids"], ["40399303"])
        self.assertTrue(
            candidate["trial_audit"]["literature_negative_trial_evidence"])

    def test_contract_calls_clinical_evidence_conflicting(self):
        candidate = {
            "drug_name": "synthetic",
            "target_symbol": "SYN1",
            "target_discovery_method": "genetic_association",
            "target_tier": "causal_anchor",
            "target_applicability": "DIRECT_DISEASE_ASSOCIATED",
            "is_approved_drug": True,
            "strong_match": True,
            "externally_prioritizable": True,
            "candidate_source_coverage": {"complete": True},
            "literature_limitation": {
                "evidence": [
                    {
                        "exact_applicability": True,
                        "exact_use_label": "APPLICABLE_SUPPORT",
                        "evidence_level": "case_report_clinical",
                    },
                    {
                        "exact_applicability": True,
                        "exact_use_label": "EXPLICIT_LIMITATION",
                        "evidence_level": "disease_model",
                        "publication_types": ["Clinical Trial, Phase II"],
                    },
                ],
            },
        }
        contract = reviewer._build_dossier_evidence_contract(
            candidate, None, [candidate])
        self.assertEqual(
            contract["scientific_readiness"]["clinical_efficacy_evidence"],
            "MIXED_CONFLICTING",
        )

    def test_bindingdb_affinity_is_not_labeled_as_chembl(self):
        candidate = {
            "pchembl_value": 8.37,
            "_evidence_ledger": {"records": [{
                "qualification_status": "qualified",
                "source_type": "bioactivity_assay",
                "provider": "bindingdb",
                "target_evidence_scope": "target_qualified",
                "measurement_type": "pchembl",
            }]},
            "score_components": {
                "efficacy_evidence_source": "multisource_ledger",
            },
        }
        table = writer._evidence_table(candidate, {})
        self.assertIn("BindingDB", table)
        self.assertNotIn("ChEMBL median pChEMBL affinity", table)

    def test_affinity_and_activity_counts_do_not_overclaim(self):
        candidate = {
            "pchembl_value": 9.15,
            "target_symbol": "HDAC6",
            "uniprot_id": "Q9UBN7",
            "_evidence_ledger": {"records": [
                {
                    "qualification_status": "qualified",
                    "source_type": "bioactivity_assay",
                    "provider": "chembl",
                    "target_evidence_scope": "target_qualified",
                    "target_species": "Homo sapiens",
                    "target_symbol": "HDAC6",
                    "target_accession": "Q9UBN7",
                    "measurement_type": "pchembl",
                    "measurement_value": 9.15,
                    "source_activity_ids": ["3389969", "6371584"],
                },
            ]},
        }
        table = writer._evidence_table(candidate, {})
        activity_note = writer._direct_chembl_activity_note(candidate)
        self.assertIn("Best target-qualified pChEMBL-equivalent affinity", table)
        self.assertNotIn("median pChEMBL-equivalent", table)
        self.assertIn("distinct qualified ChEMBL", activity_note)
        self.assertIn("not an independent-publication", activity_note)

    def test_trial_audit_separates_registry_from_literature_count(self):
        rendered = writer._trial_safety_applicability_audit({
            "trial_audit": {
                "query_status": "OBSERVED",
                "negative_repurposing_result": False,
                "trial_count": 0,
                "literature_trial_count": 1,
                "literature_trial_pmids": ["40399303"],
                "literature_negative_trial_evidence": True,
                "trials": [],
            },
            "safety_layer1": {},
            "safety_layer2": {},
            "availability_gate": {},
            "target_applicability": "CROSS_TARGET_FUNCTIONALLY_SUPPORTED",
        })
        self.assertIn(
            "ClinicalTrials.gov exact drug+disease trial count is registry-scoped",
            rendered,
        )
        self.assertIn("Exact-use clinical-trial publications", rendered)
        self.assertIn("not a global zero", rendered)

    def test_pdf_removes_machine_provenance_comments(self):
        self.assertNotIn("immutable decision provenance", _plain_markdown(
            "<!-- AgentBio immutable decision provenance -->\n"
            "Visible dossier text"
        ))
        self.assertIn("Visible dossier text", _plain_markdown(
            "<!-- hidden -->Visible dossier text"))

    def test_durable_cif_is_not_rewritten_as_unavailable(self):
        url = (
            "https://agentbio.example/api/runs/job-1/artifacts/cif/"
            + "a" * 64
        )
        report, cifs = _rewrite_structure_artifacts(
            f"[Download durable CIF]({url})", "job-1")
        self.assertEqual(report, f"[Download durable CIF]({url})")
        self.assertEqual(cifs, [])


if __name__ == "__main__":
    unittest.main()