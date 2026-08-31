"""Deterministic coverage for the flagship dossier evidence contract."""

import unittest
from unittest.mock import patch

from agents.reviewer import _build_dossier_evidence_contract
from agents.writer import build_report_markdown


def _candidate():
    return {
        "drug_name": "AuditDrug",
        "molecule_chembl_id": "CHEMBL1",
        "target_symbol": "AUD1",
        "disease_name": "Audit disease",
        "strong_match": True,
        "composite_score": 0.75,
        "is_approved_drug": True,
        "pchembl_value": 7.0,
        "confidence_score": 8,
        "source_activity_ids": [101],
        "source_chembl_ids": ["CHEMBL1"],
        "score_components": {
            "efficacy_evidence": .8, "normalized_ot_association": .4,
            "normalized_tanimoto": None, "no_failed_trial": None,
            "evidence_weight_coverage": .7,
            "tanimoto_basis": "no resolvable structure comparison",
            "trial_evidence_basis": "query_failed",
        },
        "trial_audit": {"query_status": "OBSERVED", "trials": [{
            "nct_id": "NCT00000001", "status": "COMPLETED",
            "has_results": False, "why_stopped_classification": None,
        }], "negative_repurposing_result": False},
        "safety_layer1": {}, "safety_layer2": None,
        "descriptors": {}, "provenance": {"counted_once": [],
                                           "collapsed_as_duplicate": []},
        "_evidence_ledger": {"records": [{
            "provider": "chembl", "source_type": "bioactivity_assay",
            "source_id": "101", "assay_id": "CHEMBLASSAY1",
            "measurement_type": "pchembl", "measurement_value": 7.0,
            "measurement_unit": "", "target_species": "Homo sapiens",
            "target_symbol": "AUD1", "target_accession": "P00001",
            "qualification_status": "qualified",
        }]},
    }


class FlagshipDossierContractTests(unittest.TestCase):
    def test_contract_is_versioned_and_explicit_about_missingness(self):
        candidate = _candidate()
        contract = _build_dossier_evidence_contract(
            candidate, {"druggability_context": {"approved_drugs": [{
                "name": "ReferenceDrug", "max_phase": 4,
            }]}}, [candidate])
        self.assertEqual(contract["contract_version"], "flagship-dossier-evidence-v1")
        self.assertEqual(contract["scientific_readiness"]["structure_prediction"],
                         "NOT_YET_AVAILABLE")
        self.assertEqual(contract["trial_audit"]["query_status"], "OBSERVED")
        self.assertEqual(
            contract["scientific_readiness"]["clinical_efficacy_evidence"],
            "UNKNOWN")

    def test_failed_trial_query_does_not_become_a_negative_result(self):
        candidate = _candidate()
        candidate["trial_audit"] = {
            "query_status": "UNKNOWN", "trials": [], "trial_count": None,
            "negative_repurposing_result": None,
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("Trial query state: `UNKNOWN`", report)
        self.assertIn("Negative repurposing result: `UNKNOWN`", report)
        self.assertIn("Exact drug+disease trials returned: UNKNOWN", report)

    def test_nonhuman_or_unqualified_assay_is_not_human_target_support(self):
        candidate = _candidate()
        candidate["_evidence_ledger"]["records"][0]["target_species"] = "Mus musculus"
        contract = _build_dossier_evidence_contract(candidate, None, [candidate])
        self.assertEqual(
            contract["scientific_readiness"][
                "qualified_human_target_assay_evidence"],
            "UNKNOWN")

    def test_off_target_assay_is_not_target_support(self):
        candidate = _candidate()
        candidate["_evidence_ledger"]["records"][0]["target_symbol"] = "OTHER"
        candidate["_evidence_ledger"]["records"][0]["target_accession"] = "P99999"
        contract = _build_dossier_evidence_contract(candidate, None, [candidate])
        self.assertEqual(
            contract["scientific_readiness"][
                "qualified_human_target_assay_evidence"],
            "UNKNOWN")

    def test_comparators_are_limited_to_the_candidate_target(self):
        candidate = _candidate()
        other = {**_candidate(), "drug_name": "WrongTarget", "target_symbol": "OTHER"}
        wrong_bio = {
            "target": {"target_symbol": "OTHER"},
            "druggability_context": {"approved_drugs": [{"name": "WrongDrug"}]},
        }
        contract = _build_dossier_evidence_contract(
            candidate, wrong_bio, [candidate, other])
        self.assertEqual(contract["comparators"]["target_approved_drugs"], [])
        self.assertEqual(contract["comparators"]["selected_candidates"], [])

    def test_contract_persists_casefolded_target_context(self):
        candidate = _candidate()
        candidate["target_symbol"] = "aud1"
        matched = {
            "target": {"target_symbol": "AUD1"},
            "literature_hits": [{"pmid": "12345"}],
            "druggability_context": {
                "approved_drugs": [{"name": "RightDrug"}],
            },
        }
        contract = _build_dossier_evidence_contract(
            candidate, matched, [candidate])
        self.assertEqual(
            contract["disease_mechanism_context"]["literature_pmids"],
            ["12345"])
        self.assertEqual(
            contract["comparators"]["target_approved_drugs"][0]["name"],
            "RightDrug")

    def test_writer_refreshes_contract_from_matched_target_context(self):
        candidate = _candidate()
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate, None, [candidate])
        matched = {
            "target": {"target_symbol": "AUD1"},
            "literature_hits": [{"pmid": "12345"}],
            "druggability_context": {
                "approved_drugs": [{"name": "RightDrug", "max_phase": 4}],
            },
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, matched)
        self.assertIn("RightDrug", report)
        self.assertEqual(
            candidate["dossier_evidence_contract"]["disease_mechanism_context"][
                "literature_pmids"], [])

    def test_failed_structure_prediction_is_not_rendered_as_available(self):
        candidate = _candidate()
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate, None, [candidate])
        report = build_report_markdown(candidate, {
            "complex": {"available": False, "error": "timed out"},
        }, {"composite_weights": {}, "formula_version": "v"}, None)
        self.assertIn("Structure evidence:** `NOT_YET_AVAILABLE`", report)
        self.assertNotIn("Structure evidence:** `AVAILABLE", report)

    def test_off_target_assay_does_not_create_direct_summary_claim(self):
        candidate = _candidate()
        candidate["_evidence_ledger"]["records"][0]["target_symbol"] = "OTHER"
        candidate["_evidence_ledger"]["records"][0]["target_accession"] = "P99999"
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("does not claim direct target-assay support", report)

    def test_trial_table_renders_title_and_stop_reason(self):
        candidate = _candidate()
        candidate["trial_audit"]["trials"][0].update({
            "title": "Stopped efficacy study",
            "why_stopped": "Did not meet efficacy endpoint",
        })
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("Stopped efficacy study", report)
        self.assertIn("Did not meet efficacy endpoint", report)

    def test_safety_states_distinguish_unresolved_error_and_skipped(self):
        candidate = _candidate()
        candidate["safety_layer1"] = {
            "chembl_id": None,
            "api_error": False,
            "disclosure_text": "Identity unresolved.",
        }
        candidate["safety_layer2"] = {
            "verdict": "SKIPPED",
            "disclosure_text": "Not run.",
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("UNRESOLVED / UNKNOWN", report)
        self.assertIn("SKIPPED / UNKNOWN", report)

    def test_safety_matrix_uses_disclosure_text(self):
        candidate = _candidate()
        candidate["safety_layer1"] = {
            "disclosure_text": "Structured safety finding.", "confirmed": True,
        }
        candidate["safety_layer2"] = {
            "disclosure_text": "Independent safety finding.", "verdict": "YES",
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("Structured safety finding.", report)
        self.assertIn("Independent safety finding.", report)

    def test_report_renders_audits_without_a_new_trial_lookup(self):
        candidate = _candidate()
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate, None, [candidate])
        with patch("agents.writer.check_prior_trials",
                   side_effect=AssertionError("writer must not fetch trials")):
            report = build_report_markdown(
                candidate, {}, {
                    "formula_version": "reviewer-composite-v2",
                    "safety_schema_version": "safety-v2",
                    "composite_weights": {"efficacy_evidence": .5,
                                          "ot_association": .2,
                                          "tanimoto": .15,
                                          "no_failed_trial": .15},
                }, None)
        for expected in ("Evidence-stage verdict", "Assay evidence audit",
                         "CHEMBLASSAY1", "Comparator table",
                         "Novelty and prior-trial audit",
                         "Safety and applicability matrix",
                         "NOT ASSESSED", "Reproducibility contract"):
            self.assertIn(expected, report)


if __name__ == "__main__":
    unittest.main()