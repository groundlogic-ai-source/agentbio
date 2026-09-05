"""Focused tests for exact-use and disclosure-only literature semantics."""

import unittest

from agents.reviewer import _observed_literature_support
from data_sources.literature_limitation import (
    VERDICT_FAILED,
    VERDICT_NONE,
    aggregate_findings,
)
from data_sources.literature_semantics import (
    APPLICABLE_SUPPORT,
    DRUG_ALIAS,
    DRUG_CLASS,
    NOT_APPLICABLE_TO_EXACT_DRUG_USE,
)


def _record(pmid, abstract):
    return {
        "pmid": pmid,
        "title": "PubMed record",
        "abstract": abstract,
        "publication_types": ["Journal Article"],
        "publication_year": "2015",
        "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def _finding(pmid, quote, label=APPLICABLE_SUPPORT, **values):
    finding = {
        "pmid": pmid,
        "exact_use_label": label,
        "quote": quote,
        "disease_match": True,
        "subtype_match": True,
        "use_match": True,
        "drug_or_class_match": True,
        "intervention_identity": "EXACT_DRUG",
        "evidence_level": "mechanistic",
        "reason": "Supplied source extraction.",
    }
    finding.update(values)
    return finding


class LiteratureSourceSemanticsTests(unittest.TestCase):
    def test_cantu_pmid_is_related_class_not_repaglinide_support(self):
        # Verbatim sentence from PMID 26392140. It proposes a sulfonylurea/KATP
        # approach; it does not mention repaglinide.
        quote = (
            "It is hypothesized that a topical application of a sulfonylurea "
            "drug, which can inhibit the ATP-sensitive potassium-gated channels "
            "(Kir6.X/SUR) present in human hair bulb tissues, will inhibit hair "
            "growth in a targeted manner."
        )
        abstract = (
            quote + " This approach can also be applied to rare cases of Cantú "
            "syndrome, caused by mutations in ABCC9 (coding for SUR2) or in "
            "KCNJ8 (coding for Kir6.1) that is characterized by congenital "
            "hypertrichosis."
        )
        record = _record("26392140", abstract)
        result = aggregate_findings(
            [record],
            [_finding("26392140", quote, intervention_identity="DRUG_CLASS")],
            drug_name="repaglinide",
            drug_class="ATP-sensitive potassium channel inhibitor",
            disease_name="Cantú syndrome",
            intended_use="congenital hypertrichosis in Cantú syndrome",
            target_symbol="ABCC9",
        )
        self.assertEqual(result["verdict"], VERDICT_NONE)
        self.assertEqual(result["support_count"], 0)
        related = result["related_support"]
        self.assertTrue(related["disclosure_only"])
        self.assertEqual(related["efficacy_score_boost"], 0)
        self.assertEqual(related["evidence"][0]["pmid"], "26392140")
        self.assertEqual(
            related["evidence"][0]["intervention_identity"], DRUG_CLASS)
        self.assertEqual(
            related["evidence"][0]["exact_use_label"],
            NOT_APPLICABLE_TO_EXACT_DRUG_USE,
        )
        self.assertEqual(
            _observed_literature_support({"literature_limitation": result}),
            [],
            "PMID 26392140 class-level support must not create exact-use readiness",
        )

    def test_verified_alias_can_be_exact_applicable_support(self):
        quote = (
            "Glucophage improved glycemic control in type 2 diabetes patients."
        )
        record = _record("123", quote)
        result = aggregate_findings(
            [record], [_finding("123", quote)],
            drug_name="metformin",
            drug_aliases=["Glucophage"],
            drug_class="biguanide",
            disease_name="type 2 diabetes",
            intended_use="glycemic control in type 2 diabetes",
        )
        self.assertEqual(result["support_count"], 1)
        self.assertEqual(result["evidence"][0]["intervention_identity"], DRUG_ALIAS)

    def test_class_support_never_becomes_exact_drug_support(self):
        quote = (
            "Biguanide treatment improved glycemic control in type 2 diabetes."
        )
        record = _record("124", quote)
        result = aggregate_findings(
            [record], [_finding("124", quote, intervention_identity="DRUG_CLASS")],
            drug_name="metformin",
            drug_class="biguanide",
            disease_name="type 2 diabetes",
            intended_use="glycemic control in type 2 diabetes",
        )
        self.assertEqual(result["support_count"], 0)
        self.assertEqual(result["related_support"]["count"], 1)

    def test_reversed_classifier_rows_are_aligned_by_pmid(self):
        one = _record(
            "201", "Metformin improved glycemic control in type 2 diabetes.")
        two = _record(
            "202", "Metformin improved glucose control in type 2 diabetes.")
        result = aggregate_findings(
            [one, two],
            [_finding("202", two["abstract"]), _finding("201", one["abstract"])],
            drug_name="metformin",
            disease_name="type 2 diabetes",
            intended_use="glycemic control in type 2 diabetes",
        )
        self.assertNotEqual(result["verdict"], VERDICT_FAILED)
        self.assertEqual(
            [row["pmid"] for row in result["evidence"]], ["201", "202"])

    def test_duplicate_or_invented_row_fails_closed(self):
        one = _record(
            "301", "Metformin improved glycemic control in type 2 diabetes.")
        two = _record(
            "302", "Metformin improved glucose control in type 2 diabetes.")
        result = aggregate_findings(
            [one, two],
            [_finding("301", one["abstract"]), _finding("301", one["abstract"])],
            drug_name="metformin",
            disease_name="type 2 diabetes",
            intended_use="glycemic control in type 2 diabetes",
        )
        self.assertEqual(result["verdict"], VERDICT_FAILED)
        self.assertFalse(result["gate_cleared"])


if __name__ == "__main__":
    unittest.main()