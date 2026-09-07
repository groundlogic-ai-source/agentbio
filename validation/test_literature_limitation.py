"""Adversarial deterministic tests for the post-benchmark literature gate."""

import json
import unittest
from unittest.mock import patch

from data_sources.literature_limitation import (
    VERDICT_CAUTION,
    VERDICT_CONFIRMED,
    VERDICT_CONFLICTING,
    VERDICT_FAILED,
    VERDICT_NONE,
    UNKNOWN_INTEGRITY_FAILED,
    aggregate_findings,
    check_literature_limitation,
)
from main_graph import _select_candidates


def _record(pmid="1", *, title="Study", pub_types=None, abstract=None):
    text = abstract or (
        "Calcium channel blockers are ineffective for Timothy syndrome cardiac "
        "electrophysiology because altered channel inactivation is not corrected."
    )
    return {
        "pmid": pmid,
        "title": title,
        "abstract": text,
        "publication_types": pub_types or [],
        "publication_year": "2026",
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def _classification(record, label="EXPLICIT_LIMITATION", **overrides):
    result = {
        "label": label,
        "quote": record["abstract"],
        "disease_match": True,
        "use_match": True,
        "drug_or_class_match": True,
        "reason": "Exact disease, use, and class.",
    }
    result.update(overrides)
    return result


def _aggregate(records, classifications, **overrides):
    context = {
        "disease_name": "Timothy syndrome",
        "drug_name": "Nisoldipine",
        "drug_class": "calcium channel blocker",
        "target_symbol": "CACNA1C",
        "intended_use": "cardiac electrophysiology in Timothy syndrome",
    }
    context.update(overrides)
    return aggregate_findings(records, classifications, **context)


class LiteratureLimitationTests(unittest.TestCase):
    def test_timothy_consensus_is_confirmed_applicable_limitation(self):
        record = _record(
            title="Timothy syndrome management guidelines consensus statement",
            pub_types=["Practice Guideline"],
        )
        result = _aggregate(
            [record], [_classification(record)], queries=["bounded query"])
        self.assertEqual(result["verdict"], VERDICT_CONFIRMED)
        self.assertTrue(result["blocked"])
        self.assertTrue(result["gate_cleared"])

    def test_two_independent_explicit_records_confirm_limitation(self):
        records = [_record("1"), _record("2")]
        result = _aggregate(
            records, [_classification(row) for row in records])
        self.assertEqual(result["verdict"], VERDICT_CONFIRMED)

    def test_single_low_authority_record_is_caution_not_veto(self):
        record = _record()
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_CAUTION)
        self.assertFalse(result["blocked"])

    def test_support_and_limitation_are_conflicting_not_blocked(self):
        limitation = _record("1")
        support = _record(
            "2",
            abstract=(
                "Nisoldipine improved cardiac electrophysiology in Timothy "
                "syndrome participants in this study."
            ),
        )
        result = _aggregate(
            [limitation, support],
            [_classification(limitation),
             _classification(support, label="SUPPORT")],
        )
        self.assertEqual(result["verdict"], VERDICT_CONFLICTING)
        self.assertFalse(result["blocked"])

    def test_insufficient_evidence_language_is_not_a_limitation(self):
        record = _record(
            abstract=(
                "Evidence is insufficient to determine whether calcium channel "
                "blockers benefit Timothy syndrome."
            ),
        )
        result = _aggregate(
            [record], [_classification(record, label="IRRELEVANT")])
        self.assertEqual(result["verdict"], VERDICT_NONE)
        self.assertFalse(result["blocked"])

    def test_wrong_subtype_or_use_cannot_block(self):
        record = _record(title="Consensus guideline")
        classification = _classification(record, disease_match=False)
        result = _aggregate([record], [classification])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["gate_cleared"])
        self.assertEqual(result["explicit_limitation_count"], 0)

    def test_nonverbatim_or_malformed_citation_cannot_block(self):
        record = _record(title="Consensus guideline")
        classification = _classification(
            record, quote="This sentence was invented by the classifier.")
        result = _aggregate([record], [classification])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["gate_cleared"])
        self.assertEqual(len(result["rejected_extractions"]), 1)
        malformed = _record("not-a-pmid", title="Consensus guideline")
        malformed_result = _aggregate(
            [malformed], [_classification(malformed)])
        self.assertEqual(malformed_result["verdict"], VERDICT_FAILED)
        self.assertFalse(
            malformed_result["rejected_extractions"][0]["citation_verified"])

    def test_classifier_cannot_veto_unrelated_source_by_asserting_matches(self):
        record = _record(
            "9",
            pub_types=["Practice Guideline"],
            abstract=(
                "Calcium channel blockers are ineffective for essential "
                "hypertension in this population."
            ),
        )
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(
            result["rejected_extractions"][0][
                "deterministic_applicability"]["disease"])

    def test_generic_cardiac_language_cannot_establish_exact_use(self):
        record = _record(
            "10",
            pub_types=["Practice Guideline"],
            abstract=(
                "Calcium channel blockers are ineffective for cardiac symptoms "
                "in Timothy syndrome."
            ),
        )
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(
            result["rejected_extractions"][0][
                "deterministic_applicability"]["intended_use"])

    def test_target_only_negative_paper_cannot_veto_drug_or_class(self):
        record = _record(
            "11",
            pub_types=["Practice Guideline"],
            abstract=(
                "CACNA1C suppression is ineffective for cardiac "
                "electrophysiology in Timothy syndrome."
            ),
        )
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(
            result["rejected_extractions"][0][
                "deterministic_applicability"]["drug_or_class"])

    def test_different_drug_class_cannot_veto_candidate_class(self):
        record = _record(
            "12",
            pub_types=["Practice Guideline"],
            abstract=(
                "Beta adrenergic blockers are ineffective for cardiac "
                "electrophysiology in Timothy syndrome."
            ),
        )
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(
            result["rejected_extractions"][0][
                "deterministic_applicability"]["drug_or_class"])

    def test_title_keyword_without_pubmed_type_is_not_authoritative(self):
        record = _record(
            title="A guideline-like discussion of Timothy syndrome",
            pub_types=["Journal Article"],
        )
        result = _aggregate([record], [_classification(record)])
        self.assertEqual(result["verdict"], VERDICT_CAUTION)
        self.assertFalse(result["blocked"])

    @patch("data_sources.literature_limitation.chat_text")
    def test_malformed_batch_json_closes_gate(self, mock_chat):
        mock_chat.return_value = ("not JSON", "test")
        result = check_literature_limitation(
            "Nisoldipine",
            "Timothy syndrome",
            "CACNA1C",
            "BLOCKER",
            "calcium channel blocker",
            "cardiac electrophysiology in Timothy syndrome",
            retriever=lambda *args, **kwargs: (["q"], [_record()]),
        )
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["gate_cleared"])

    @patch("data_sources.literature_limitation.chat_text")
    def test_missing_batch_pmid_closes_gate(self, mock_chat):
        mock_chat.return_value = (
            '{"findings":[]}', "test")
        result = check_literature_limitation(
            "Nisoldipine",
            "Timothy syndrome",
            "CACNA1C",
            "BLOCKER",
            "calcium channel blocker",
            "cardiac electrophysiology in Timothy syndrome",
            retriever=lambda *args, **kwargs: (["q"], [_record()]),
        )
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["gate_cleared"])

    @patch("data_sources.literature_limitation.chat_text")
    def test_unknown_batch_row_is_retried_per_record(self, mock_chat):
        record = _record(
            "77",
            abstract=(
                "Nisoldipine improved cardiac electrophysiology in Timothy "
                "syndrome participants in this study."
            ),
        )
        mock_chat.side_effect = [
            (
                json.dumps({
                    "findings": [{
                        "pmid": "77",
                        "exact_use_label": "UNKNOWN/INTEGRITY_FAILED",
                        "quote": "",
                        "disease_match": True,
                        "subtype_match": True,
                        "use_match": True,
                        "drug_or_class_match": True,
                        "reason": "Unresolved in batch.",
                    }]
                }),
                "batch",
            ),
            (
                json.dumps({
                    "exact_use_label": "APPLICABLE_SUPPORT",
                    "quote": record["abstract"],
                    "disease_match": True,
                    "subtype_match": True,
                    "use_match": True,
                    "drug_or_class_match": True,
                    "evidence_level": "case_report_clinical",
                    "reason": "Exact support.",
                }),
                "record",
            ),
        ]
        result = check_literature_limitation(
            "Nisoldipine",
            "Timothy syndrome",
            "CACNA1C",
            "BLOCKER",
            "calcium channel blocker",
            "cardiac electrophysiology in Timothy syndrome",
            retriever=lambda *args, **kwargs: (["q"], [record]),
        )
        self.assertEqual(result["support_count"], 1)
        self.assertEqual(result["verdict"], VERDICT_NONE)
        self.assertTrue(result["gate_cleared"])

    def test_accented_disease_name_matches_unaccented_pubmed_text(self):
        record = _record(
            "78",
            abstract=(
                "Glibenclamide improved cardiac function in Cantu syndrome "
                "participants in this study."
            ),
        )
        result = _aggregate(
            [record],
            [_classification(record, label="SUPPORT")],
            drug_name="glibenclamide",
            drug_class="sulfonylurea",
            disease_name="Cantú syndrome",
            intended_use="cardiac function in Cantú syndrome",
        )
        self.assertEqual(result["support_count"], 1)
        self.assertTrue(result["gate_cleared"])

    def test_unknown_label_with_verbatim_clinical_limitation_is_recovered(self):
        quote = (
            "However, in the clinical trial, the effects on hypertrichosis were "
            "mixed, and there were no significant changes in cardiac phenotype "
            "or leg edema."
        )
        record = _record(
            "40399303",
            title=(
                "Treatment of overactive KATP channels with glibenclamide in a "
                "clinical trial in humans with Cantu syndrome."
            ),
            abstract=(
                "This study explores the efficacy of glibenclamide for treating "
                "Cantu syndrome. " + quote
            ),
        )
        result = aggregate_findings(
            [record],
            [{
                "pmid": "40399303",
                "exact_use_label": UNKNOWN_INTEGRITY_FAILED,
                "quote": quote,
                "disease_match": True,
                "subtype_match": True,
                "use_match": True,
                "drug_or_class_match": True,
                "reason": "The supplied trial reports a limitation.",
            }],
            disease_name="Cantú syndrome",
            drug_name="glibenclamide",
            drug_class="KATP channel inhibitor",
            intended_use="treatment of Cantú syndrome",
        )
        self.assertEqual(result["verdict"], VERDICT_CAUTION)
        self.assertEqual(result["explicit_limitation_count"], 1)
        self.assertTrue(result["gate_cleared"])

    def test_unknown_label_with_mutation_specific_limitation_is_recovered(self):
        quote = (
            "both Kir6.1(V65M) and Kir6.2(V64M) mutations essentially abolish "
            "high-affinity sensitivity to the KATP blocker glibenclamide"
        )
        record = _record(
            "28842488",
            title="Disease-associated mutations of the ATP-sensitive potassium channel",
            abstract=(
                "Cantu syndrome is associated with mutations in KCNJ8. "
                "Sulfonylurea inhibitors such as glibenclamide are potential "
                "therapies for Cantu syndrome. " + quote + " in cells."
            ),
        )
        result = aggregate_findings(
            [record],
            [{
                "pmid": "28842488",
                "exact_use_label": UNKNOWN_INTEGRITY_FAILED,
                "quote": quote,
                "disease_match": True,
                "subtype_match": False,
                "use_match": True,
                "drug_or_class_match": True,
                "reason": "Mutation-specific loss of sensitivity.",
            }],
            disease_name="Cantú syndrome",
            drug_name="glibenclamide",
            drug_class="KATP blocker",
            target_symbol="KCNJ8",
            intended_use="treatment of Cantú syndrome",
        )
        self.assertEqual(result["verdict"], VERDICT_CAUTION)
        self.assertEqual(result["explicit_limitation_count"], 1)
        self.assertTrue(result["gate_cleared"])

    def test_unknown_label_with_clear_probe_record_becomes_not_applicable(self):
        quote = (
            "Pinacidil was used as an electrophysiology probe to characterize "
            "KATP channel activity in Cantu syndrome."
        )
        record = _record(
            "36980270",
            title="KATP channel electrophysiology in Cantu syndrome",
            abstract=quote,
        )
        result = aggregate_findings(
            [record],
            [{
                "pmid": "36980270",
                "exact_use_label": UNKNOWN_INTEGRITY_FAILED,
                "quote": quote,
                "disease_match": True,
                "subtype_match": True,
                "use_match": False,
                "drug_or_class_match": False,
                "reason": "Probe use, not glibenclamide treatment.",
            }],
            disease_name="Cantú syndrome",
            drug_name="glibenclamide",
            drug_class="KATP blocker",
            intended_use="treatment of Cantú syndrome",
        )
        self.assertEqual(result["verdict"], VERDICT_NONE)
        self.assertEqual(result["classifier_integrity_failures"], 0)
        self.assertTrue(result["gate_cleared"])
        self.assertEqual(result["evidence"][0]["exact_use_label"],
                         "NOT_APPLICABLE_TO_EXACT_DRUG_USE")

    def test_negative_quote_mislabeled_support_cannot_cancel_limitation(self):
        good = _record("20", pub_types=["Practice Guideline"])
        mislabeled = _record("21")
        result = _aggregate(
            [good, mislabeled],
            [
                _classification(good),
                _classification(mislabeled, label="SUPPORT"),
            ],
        )
        self.assertEqual(result["verdict"], VERDICT_CONFIRMED)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["support_count"], 0)

    def test_retrieval_failure_is_unknown_and_closes_paid_gate(self):
        def fail(*args, **kwargs):
            raise TimeoutError("source unavailable")

        result = check_literature_limitation(
            "Drug", "Disease", "GENE", "INHIBITOR", None,
            retriever=fail,
        )
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["blocked"])
        self.assertFalse(result["gate_cleared"])
        self.assertIn("not authorized", result["reason"])

    def test_empty_healthy_search_is_not_novelty_claim(self):
        result = check_literature_limitation(
            "Drug", "Disease", "GENE", "INHIBITOR", None,
            retriever=lambda *args, **kwargs: (["q"], []),
            classifier=lambda *args, **kwargs: {},
        )
        self.assertEqual(result["verdict"], VERDICT_NONE)
        self.assertIn("not evidence of novelty", result["reason"])

    def test_structure_selector_excludes_blocked_and_unassessed_candidates(self):
        blocked = {
            "drug_name": "Blocked", "strong_match": True,
            "literature_limitation_blocked": True,
            "literature_limitation_gate_cleared": True,
        }
        failed = {
            "drug_name": "Failed", "strong_match": True,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": False,
        }
        cleared = {
            "drug_name": "Cleared", "strong_match": True,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
        }
        selected = _select_candidates(
            {"candidates": [blocked, failed, cleared]})
        self.assertEqual([row["drug_name"] for row in selected], ["Cleared"])

    def test_legacy_candidate_without_gate_state_is_fail_closed(self):
        selected = _select_candidates({
            "candidates": [{
                "drug_name": "Pre-gate cached lead",
                "strong_match": True,
            }]
        })
        self.assertEqual(selected, [])

    def test_writer_renders_persisted_limitation_without_fetching(self):
        from agents.reviewer import _build_dossier_evidence_contract
        from agents.writer import build_report_markdown

        candidate = {
            "drug_name": "Nisoldipine",
            "target_symbol": "CACNA1C",
            "disease_name": "Timothy syndrome",
            "strong_match": True,
            "externally_prioritizable": False,
            "literature_limitation_blocked": True,
            "literature_limitation": {
                "schema_version": "literature-limitation-v1",
                "verdict": VERDICT_CONFIRMED,
                "source_status": "HEALTHY",
                "blocked": True,
                "gate_cleared": True,
                "reason": "Consensus limitation.",
                "records_screened": 1,
                "evidence": [{
                    "pmid": "12345", "source_url":
                    "https://pubmed.ncbi.nlm.nih.gov/12345/",
                    "publication_types": ["Guideline"],
                    "publication_year": "2026",
                    "label": "EXPLICIT_LIMITATION",
                    "quote": "Calcium channel blockers are ineffective.",
                }],
            },
            "composite_score": .90,
            "score_components": {},
            "trial_audit": {},
            "safety_layer1": {},
            "safety_layer2": {},
            "_evidence_ledger": {"records": []},
            "provenance": {},
            "is_approved_drug": True,
        }
        candidate["dossier_evidence_contract"] = (
            _build_dossier_evidence_contract(candidate, None, [candidate]))
        with patch(
            "data_sources.literature_limitation.retrieve_literature",
            side_effect=AssertionError("writer must not retrieve literature"),
        ):
            report = build_report_markdown(
                candidate, {}, {"composite_weights": {}}, None)
        self.assertIn("KNOWN LITERATURE LIMITATION", report)
        self.assertIn("Treatment-landscape and literature-limitation gate", report)
        self.assertIn("12345", report)
        self.assertIn("score is retained for auditability", report)


if __name__ == "__main__":
    unittest.main()