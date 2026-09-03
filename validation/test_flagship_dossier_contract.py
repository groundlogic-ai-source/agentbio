"""Deterministic coverage for the flagship dossier evidence contract."""

import unittest
from unittest.mock import patch

from agents.reviewer import _build_dossier_evidence_contract
from agents.writer import build_report_markdown, validate_dossier_inputs


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
    def _preflight_ready_candidate(self):
        candidate = _candidate()
        candidate["uniprot_id"] = "P00001"
        candidate["approval_basis"] = "regulatory_approval_record"
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate,
            {"target": {
                "target_symbol": "AUD1", "uniprot_id": "P00001",
            }},
            [candidate],
        )
        return candidate

    def test_dossier_preflight_accepts_reconciled_inputs(self):
        candidate = self._preflight_ready_candidate()
        validate_dossier_inputs(
            candidate,
            {"target": {"target_symbol": "AUD1", "uniprot_id": "P00001"}},
            {"target_symbol": "AUD1", "uniprot_id": "P00001"},
            repurposing_only=True,
        )

    def test_dossier_preflight_rejects_wrong_target_context(self):
        candidate = self._preflight_ready_candidate()
        with self.assertRaisesRegex(ValueError, "Biologist target"):
            validate_dossier_inputs(
                candidate,
                {"target": {"target_symbol": "OTHER", "uniprot_id": "P99999"}},
                {"target_symbol": "AUD1", "uniprot_id": "P00001"},
                repurposing_only=True,
            )

    def test_dossier_preflight_rejects_scored_precedent_stamp(self):
        candidate = self._preflight_ready_candidate()
        candidate["target_discovery_method"] = "pharmacological_precedent"
        candidate["score_components"].update({
            "normalized_ot_association": 0.9,
            "ot_association_basis": "precedent_stamped_constant",
        })
        with self.assertRaisesRegex(ValueError, "scored/measured Open Targets"):
            validate_dossier_inputs(
                candidate, None, None, repurposing_only=True)

    def test_dossier_preflight_rejects_approval_without_provenance(self):
        candidate = self._preflight_ready_candidate()
        candidate["approval_basis"] = "unknown"
        with self.assertRaisesRegex(ValueError, "approval provenance"):
            validate_dossier_inputs(
                candidate, None, None, repurposing_only=True)

    def test_summary_uses_complete_tanimoto_sentence(self):
        report = build_report_markdown(_candidate(), {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("Tanimoto similarity is", report)
        self.assertNotIn(". and a Tanimoto", report)

    def test_precedent_stamped_ot_value_is_not_rendered_as_measured_evidence(self):
        candidate = _candidate()
        candidate["ot_association_score"] = 0.827
        candidate["target_discovery_method"] = "pharmacological_precedent"
        candidate["target_tier"] = "clinical_precedent"
        candidate["score_components"].update({
            "normalized_ot_association": None,
            "ot_association_basis": "precedent_stamped_constant",
        })
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("target-selection ordering value stamped", report)
        self.assertIn("not a measured Open Targets association", report)
        self.assertNotIn(
            "clinical precedent (approved for this disease concept)", report)

    def test_target_precedent_does_not_claim_disease_approval(self):
        candidate = _candidate()
        candidate["target_tier"] = "clinical_precedent"
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("target-level pharmacological precedent", report)
        self.assertIn(
            "does not establish approval or clinical efficacy for this disease",
            report,
        )

    def test_empty_chembl_mechanism_result_is_source_scoped(self):
        candidate = _candidate()
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, {"druggability_context": {
            "has_approved_drug_for_target": False,
            "approved_drug_count": 0,
        }})
        self.assertIn("no qualifying approved-drug record was returned", report)
        self.assertIn("does **not** mean that no approved drug modulates", report)
        self.assertNotIn(
            "No approved drug currently exists with a known mechanism", report)

    def test_free_form_chemist_rationale_cannot_restate_report_facts(self):
        candidate = _candidate()
        candidate["rationale"] = (
            "CONTRADICTORY FREE-FORM CLAIM with a different BioGRID list.")
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertNotIn("CONTRADICTORY FREE-FORM CLAIM", report)
        self.assertNotIn("_Chemist rationale:_", report)

    def test_report_does_not_mandate_wet_lab_or_clinical_validation(self):
        report = build_report_markdown(_candidate(), {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn(
            "responsible organization determines whether orthogonal experiments",
            report,
        )
        self.assertNotIn("requires wet-lab", report)
        self.assertNotIn("ultimately, clinical validation", report)
        self.assertNotIn("Hypothesis requires experimental validation", report)
        self.assertNotIn("Required channel/iPSC tests", report)

    def test_direction_reason_has_exactly_one_terminal_period(self):
        candidate = _candidate()
        candidate["mechanism_direction"] = {
            "verdict": "DIRECTIONALLY_COMPATIBLE",
            "action_type_used": "INHIBITOR",
            "reason": "Reason already punctuated.",
            "search_citations": "https://example.org/source",
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, None)
        self.assertIn("Reason: Reason already punctuated.", report)
        self.assertNotIn("Reason: Reason already punctuated..", report)

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
        self.assertEqual(
            contract["scientific_readiness"]["status"],
            "HYPOTHESIS_FOR_QUALIFIED_REVIEW")

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

    def test_writer_refresh_cannot_reintroduce_lead_as_comparator(self):
        candidate = _candidate()
        candidate["drug_name"] = "Nisoldipine"
        candidate["molecule_chembl_id"] = "CHEMBL123"
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate, None, [candidate])
        matched = {
            "target": {"target_symbol": "AUD1"},
            "druggability_context": {"approved_drugs": [
                {"name": "Nisoldipine", "molecule_chembl_id": "CHEMBL123"},
                {"name": "Nifedipine", "molecule_chembl_id": "CHEMBL456"},
            ]},
        }
        report = build_report_markdown(candidate, {}, {
            "composite_weights": {}, "formula_version": "v",
        }, matched)
        comparator_section = report.split("### Comparator table", 1)[1]
        self.assertNotIn("| Nisoldipine |", comparator_section)
        self.assertIn("| Nifedipine |", comparator_section)

    def test_failed_structure_prediction_is_not_rendered_as_available(self):
        candidate = _candidate()
        candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            candidate, None, [candidate])
        report = build_report_markdown(candidate, {
            "complex": {"available": False, "error": "timed out"},
        }, {"composite_weights": {}, "formula_version": "v"}, None)
        self.assertIn("Structure evidence:** Not yet available", report)
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

    def test_contract_excludes_lead_from_approved_comparators(self):
        candidate = _candidate()
        candidate["drug_name"] = "Nisoldipine"
        candidate["molecule_chembl_id"] = "CHEMBL1726"
        context = {"target": {"target_symbol": candidate["target_symbol"]},
                   "druggability_context": {"approved_drugs": [
                       {"name": "NISOLDIPINE",
                        "molecule_chembl_id": "CHEMBL1726"},
                       {"name": "NIFEDIPINE",
                        "molecule_chembl_id": "CHEMBL193"},
                   ]}}
        contract = _build_dossier_evidence_contract(
            candidate, context, [candidate])
        self.assertEqual(
            [row["name"] for row in
             contract["comparators"]["target_approved_drugs"]],
            ["NIFEDIPINE"],
        )

    def test_contract_excludes_salt_child_lead_from_parent_comparator(self):
        candidate = _candidate()
        candidate.update({
            "drug_name": "LEAD HYDROCHLORIDE",
            "molecule_chembl_id": "CHEMBL_CHILD",
            "parent_chembl_id": "CHEMBL_PARENT",
            "source_molecule_chembl_ids": [
                "CHEMBL_CHILD", "CHEMBL_PARENT"],
        })
        context = {"target": {"target_symbol": candidate["target_symbol"]},
                   "druggability_context": {"approved_drugs": [
                       {"name": "LEAD",
                        "molecule_chembl_id": "CHEMBL_PARENT",
                        "parent_chembl_id": "CHEMBL_PARENT",
                        "source_molecule_chembl_ids": [
                            "CHEMBL_CHILD", "CHEMBL_PARENT"]},
                       {"name": "NIFEDIPINE",
                        "molecule_chembl_id": "CHEMBL193",
                        "parent_chembl_id": "CHEMBL193"},
                   ]}}
        contract = _build_dossier_evidence_contract(
            candidate, context, [candidate])
        self.assertEqual(
            [row["name"] for row in
             contract["comparators"]["target_approved_drugs"]],
            ["NIFEDIPINE"],
        )

    def test_report_hides_raw_direction_search_and_humanizes_statuses(self):
        candidate = _candidate()
        candidate["mechanism_direction"] = {
            "verdict": "DIRECTIONALLY_COMPATIBLE",
            "action_type_used": "GATING_INHIBITOR",
            "disease_mechanism_summary": (
                "If you want, I can next turn this into a verdict."),
            "reason": "Target-level inhibition opposes gain of function",
            "search_citations": "https://example.org/source",
        }
        candidate["dossier_evidence_contract"] = (
            _build_dossier_evidence_contract(candidate, None, [candidate]))
        report = build_report_markdown(
            candidate, {}, {
                "formula_version": "reviewer-composite-v2",
                "safety_schema_version": "safety-v2",
                "composite_weights": {"efficacy_evidence": .5},
            }, None)
        self.assertNotIn("If you want", report)
        self.assertNotIn("Disease-mechanism summary:", report)
        self.assertIn("Prioritized hypothesis", report)
        self.assertIn("Hypothesis for qualified review", report)
        self.assertIn("Target-level directional compatibility audit", report)
        self.assertIn("Automated score-capping flags", report)


if __name__ == "__main__":
    unittest.main()