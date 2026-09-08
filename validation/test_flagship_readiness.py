"""Deterministic tests for the separate flagship-readiness policy."""

import unittest
from unittest.mock import patch

from agents.flagship_readiness import (
    FLAGSHIP_READINESS_VERSION,
    evaluate_candidate_readiness,
    evaluate_target_preflight,
)
from agents.target_selection import preflight_for_disease
from agents.writer import build_report_markdown


def _stage1_row(**overrides):
    row = {
        "disease_name": "Example disease",
        "target_symbol": "KDR",
        "uniprot_id": "P35968",
        "ot_association_score": 0.82,
        "tractability_score": 0.7,
        "unmet_need_score": 0.8,
        "target_discovery_method": "genetic_association",
        "has_approved_treatment": False,
        "approved_drug_names": [],
        "process_support": ["vascular signaling"],
    }
    row.update(overrides)
    return row


def _candidate(**overrides):
    candidate = {
        "drug_name": "Candidate",
        "disease_name": "NPM1-mutated AML",
        "target_symbol": "FLT3",
        "target_applicability": "DIRECT_DISEASE_ASSOCIATED",
    }
    candidate.update(overrides)
    return candidate


class FlagshipReadinessTests(unittest.TestCase):
    def test_schema_and_positive_candidate_readiness(self):
        result = evaluate_candidate_readiness(
            _candidate(flagship_candidate_advantage="lower exposure burden"),
            {
                "disease_mechanism_context": {
                    "disease_name": "NPM1-mutated AML",
                    "target_symbol": "FLT3",
                },
                "scientific_readiness": {
                    "disease_model_evidence": "OBSERVED_SUPPORT",
                    "clinical_efficacy_evidence": "OBSERVED_SUPPORT",
                },
                "comparators": {
                    "target_approved_drugs": [{"drug_name": "midostaurin"}],
                },
            },
        )
        self.assertEqual(result["schema_version"], FLAGSHIP_READINESS_VERSION)
        self.assertEqual(result["verdict"], "FLAGSHIP_READY")
        self.assertEqual(result["criteria"]["next_experiment"]["status"], "PASS")

    def test_generic_target_is_not_flagship_ready(self):
        result = evaluate_candidate_readiness(
            _candidate(
                disease_name="Angiosarcoma",
                target_symbol="TOP2A",
                target_applicability="DIRECT_DISEASE_ASSOCIATED",
            ),
            {
                "disease_mechanism_context": {
                    "disease_name": "Angiosarcoma",
                    "target_symbol": "TOP2A",
                },
                "scientific_readiness": {},
                "comparators": {
                    "target_approved_drugs": [{"drug_name": "doxorubicin"}],
                },
            },
        )
        self.assertEqual(result["verdict"], "NOT_FLAGSHIP_READY")
        self.assertIn("target_specificity", result["reason_codes"])

    def test_angiosarcoma_like_candidate_is_conditional_when_target_is_specific(self):
        result = evaluate_candidate_readiness(
            _candidate(
                disease_name="Angiosarcoma",
                target_symbol="KDR",
                target_applicability="DIRECT_DISEASE_ASSOCIATED",
            ),
            {
                "disease_mechanism_context": {
                    "disease_name": "Angiosarcoma",
                    "target_symbol": "KDR",
                },
                "scientific_readiness": {
                    "disease_model_evidence": "UNKNOWN",
                    "clinical_efficacy_evidence": "UNKNOWN",
                },
                "comparators": {
                    "target_approved_drugs": [{"drug_name": "pazopanib"}],
                },
            },
        )
        self.assertEqual(result["verdict"], "CONDITIONAL_REVIEW")
        self.assertIn("candidate_advantage", result["reason_codes"])
        self.assertIn("disease-model", " ".join(result["missing_evidence"]))

    def test_missing_candidate_identity_keeps_next_experiment_unknown(self):
        result = evaluate_candidate_readiness(
            _candidate(target_symbol=""),
            {
                "disease_mechanism_context": {
                    "disease_name": "NPM1-mutated AML",
                },
                "scientific_readiness": {},
                "comparators": {},
            },
        )
        self.assertEqual(
            result["criteria"]["next_experiment"]["status"], "UNKNOWN"
        )
        self.assertIn("resolved disease", " ".join(result["missing_evidence"]))

    def test_preflight_reports_insufficient_evidence_without_rows(self):
        result = evaluate_target_preflight([], requested_disease="Unknown disease")
        self.assertEqual(result["verdict"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("leading_target", result["missing_evidence"])

    def test_preflight_marks_broad_context_conditional(self):
        result = evaluate_target_preflight(
            [_stage1_row()],
            requested_disease="Example disease",
        )
        self.assertEqual(result["verdict"], "CONDITIONAL_REVIEW")
        self.assertEqual(
            result["criteria"]["scope_clarity"]["status"], "CONDITIONAL"
        )
        self.assertEqual(
            result["criteria"]["candidate_advantage"]["status"], "UNKNOWN"
        )
        self.assertEqual(result["next_experiment"]["status"], "SPECIFIABLE")

    def test_preflight_rejects_generic_top2a_signal(self):
        result = evaluate_target_preflight(
            [_stage1_row(
                target_symbol="TOP2A",
                disease_name="Angiosarcoma",
                has_approved_treatment=True,
                approved_drug_names=["doxorubicin"],
            )],
            requested_disease="Angiosarcoma",
        )
        self.assertEqual(result["verdict"], "NOT_FLAGSHIP_READY")
        self.assertEqual(
            result["criteria"]["target_specificity"]["status"], "FAIL"
        )
        self.assertEqual(
            result["criteria"]["standard_of_care_overlap"]["status"],
            "CONDITIONAL",
        )

    def test_expert_use_case_is_persisted_but_unverified_in_preflight(self):
        result = evaluate_target_preflight(
            [_stage1_row()],
            requested_disease="Example disease",
            flagship_use_case={
                "subgroup": "genotype-defined subgroup",
                "stage": "relapsed",
                "treatment_setting": "specialist care",
                "proposed_advantage": "lower exposure burden",
            },
        )
        self.assertEqual(
            result["flagship_use_case"]["proposed_advantage"],
            "lower exposure burden",
        )
        self.assertEqual(
            result["flagship_use_case_claims"]["subgroup"]["status"], "UNKNOWN"
        )
        self.assertEqual(
            result["criteria"]["proposed_advantage"]["status"], "UNKNOWN"
        )
        self.assertIn("independent support", " ".join(result["missing_evidence"]))

    def test_expert_use_case_cannot_create_final_score_or_verdict(self):
        result = evaluate_candidate_readiness(
            _candidate(flagship_candidate_advantage=None),
            {
                "disease_mechanism_context": {
                    "disease_name": "NPM1-mutated AML",
                    "target_symbol": "FLT3",
                },
                "scientific_readiness": {
                    "disease_model_evidence": "UNKNOWN",
                    "clinical_efficacy_evidence": "UNKNOWN",
                },
                "comparators": {},
            },
            flagship_use_case={
                "subgroup": "NPM1-mutated",
                "stage": "relapsed",
                "treatment_setting": "salvage therapy",
                "proposed_advantage": "better tolerability",
            },
        )
        self.assertEqual(result["flagship_use_case"]["stage"], "relapsed")
        self.assertEqual(
            result["flagship_use_case_claims"]["proposed_advantage"]["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            result["criteria"]["candidate_advantage"]["status"], "UNKNOWN"
        )
        self.assertEqual(
            result["criteria"]["proposed_advantage"]["status"], "UNKNOWN"
        )
        self.assertEqual(result["verdict"], "CONDITIONAL_REVIEW")

    def test_stage1_preflight_avoids_full_target_expansion(self):
        disease = {
            "name": "Example disease",
            "orpha_code": "123",
            "source": "orphanet",
        }
        with patch(
            "agents.target_selection._matchable_universe",
            return_value=[disease],
        ), patch(
            "agents.target_selection._match_disease",
            return_value=disease,
        ), patch(
            "agents.target_selection._resolve_efo_id",
            return_value="EFO_123",
        ), patch(
            "agents.target_selection._efo_name_overlap",
            return_value=1.0,
        ), patch(
            "agents.target_selection.get_disease_known_drugs",
            return_value={
                "has_approved_treatment": False,
                "approved_drug_names": [],
                "status": "complete",
            },
        ), patch(
            "agents.target_selection.get_target_disease_score",
            return_value=[{
                "target_symbol": "KDR",
                "uniprot_id": "P35968",
                "association_score": 0.8,
            }],
        ), patch(
            "agents.target_selection.select_for_disease",
            side_effect=AssertionError("full selector must not run"),
        ):
            rows = preflight_for_disease("Example disease")
        self.assertEqual(rows[0]["target_symbol"], "KDR")
        self.assertEqual(
            rows[0]["preflight_source_status"]["open_targets_associations"],
            "complete",
        )

    def test_report_renders_flagship_verdict_separately_from_score(self):
        candidate = _candidate(
            composite_score=0.9087,
            flagship_readiness={
                "verdict": "NOT_FLAGSHIP_READY",
                "next_action": "Use only as a qualified research hypothesis.",
                "missing_evidence": ["candidate-specific advantage"],
                "reason_codes": ["target_specificity"],
                "reasons": ["Generic target signal."],
            },
        )
        report = build_report_markdown(
            candidate,
            {},
            {"composite_weights": {}, "formula_version": "test"},
            None,
        )
        self.assertIn("Flagship readiness", report)
        self.assertIn("Not flagship ready", report)
        self.assertIn("composite score", report.casefold())


if __name__ == "__main__":
    unittest.main()