"""Synthetic contract tests for audit sources and N1–N4 detectors.

No scored audit-study identity, citation, source id, label, or expected outcome
appears here.  All entities and records are invented development fixtures.
"""
from __future__ import annotations

import unittest
from unittest import mock

import requests

from api.audit_context import detect_audit_findings
from data_sources import openfda
from data_sources import pubtator_assertions as pubtator


class _Response:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


def _label_row(**overrides):
    row = {
        "id": "development-document-a",
        "effective_time": "20240115",
        "active_ingredient": [
            "Velunadine 10 mg; Quorazene 5 mg",
        ],
        "indications_and_usage": ["For development-fixture use only."],
        "openfda": {
            "generic_name": ["Velunadine / Quorazene"],
            "brand_name": ["Dualexa"],
            "substance_name": ["Velunadine", "Quorazene"],
            "product_ndc": ["00000-0001"],
            "route": ["TOPICAL"],
            "dosage_form": ["CREAM"],
            "product_type": ["LICENSED BIOLOGICAL PRODUCT"],
            "application_number": ["BLADEV001"],
            "spl_set_id": ["development-set-a"],
            "spl_id": ["development-spl-a"],
            "version": ["7"],
        },
    }
    row.update(overrides)
    return row


class OpenFdaStructuredLabelTest(unittest.TestCase):
    @mock.patch.object(openfda, "get", return_value=None)
    @mock.patch.object(openfda, "cache_set")
    @mock.patch.object(openfda.requests, "get")
    def test_extracts_regulatory_product_and_exact_provenance(
        self, request, cache_set, cache_get,
    ):
        request.return_value = _Response(
            payload={"results": [_label_row()]})
        result = openfda.get_label_evidence("Dualexa")
        self.assertEqual(result["status"], "ok")
        product = result["products"][0]
        self.assertTrue(product["regulatory"]["combination"])
        self.assertEqual(
            {x["name"] for x in product["regulatory"]["active_ingredients"]},
            {"Velunadine", "Quorazene"},
        )
        self.assertEqual(product["regulatory"]["routes"], ["TOPICAL"])
        self.assertEqual(product["regulatory"]["dosage_forms"], ["CREAM"])
        self.assertEqual(product["regulatory"]["product_modality"], "biologic")
        self.assertIn(
            product["regulatory"]["modality_basis"],
            {"openfda.product_type", "openfda.application_number BLA prefix"},
        )
        self.assertEqual(product["spl"]["set_id"], "development-set-a")
        self.assertEqual(product["spl"]["version"], "7")
        self.assertEqual(product["spl"]["effective_date"], "20240115")
        self.assertTrue(product["citation_eligible"])
        self.assertIn("development-set-a", product["source_url"])
        quotes = {e["quote"] for e in product["evidence"]}
        self.assertIn("Velunadine 10 mg; Quorazene 5 mg", quotes)
        cache_set.assert_called_once()

    @mock.patch.object(openfda, "get", return_value=None)
    @mock.patch.object(openfda, "cache_set")
    @mock.patch.object(openfda.requests, "get")
    def test_healthy_empty_and_filtered_empty_are_distinct_and_cached(
        self, request, cache_set, cache_get,
    ):
        request.return_value = _Response(status_code=404)
        empty = openfda.get_label_evidence("Nulladine")
        self.assertEqual(empty["status"], "empty")
        self.assertEqual(cache_set.call_count, 1)

        cache_set.reset_mock()
        request.return_value = _Response(payload={"results": [_label_row()]})
        filtered = openfda.get_label_evidence("Otheradine")
        self.assertEqual(filtered["status"], "filtered_empty")
        self.assertEqual(filtered["filtered_count"], 1)
        self.assertEqual(cache_set.call_count, 1)

    @mock.patch.object(openfda, "get", return_value=None)
    @mock.patch.object(openfda, "cache_set")
    @mock.patch.object(openfda.requests, "get")
    def test_parse_transport_and_degraded_states_never_cache(
        self, request, cache_set, cache_get,
    ):
        request.return_value = _Response(payload={"results": "bad"})
        self.assertEqual(
            openfda.get_label_evidence("Malformedine")["status"],
            "parse_failed",
        )
        cache_set.assert_not_called()

        request.side_effect = requests.Timeout("development timeout")
        self.assertEqual(
            openfda.get_label_evidence("Timeoutadine")["status"],
            "unavailable",
        )
        cache_set.assert_not_called()

        request.side_effect = None
        missing_provenance = _label_row(effective_time=None)
        request.return_value = _Response(
            payload={"results": [missing_provenance]})
        self.assertEqual(
            openfda.get_label_evidence("Dualexa")["status"],
            "degraded",
        )
        cache_set.assert_not_called()


class PubTatorAssertionTest(unittest.TestCase):
    def _run(self, search_row):
        drug = [{
            "_id": "@CHEMICAL_Velunadine",
            "biotype": "chemical",
            "db_id": "DEV:C1",
            "name": "Velunadine",
        }]
        gene = [{
            "_id": "@GENE_QRX1",
            "biotype": "gene",
            "db_id": "DEV:G1",
            "name": "QRX1",
        }]
        payloads = [drug, gene, {"results": [search_row]}]
        with mock.patch.object(pubtator, "get", return_value=None), \
             mock.patch.object(pubtator, "cache_set") as cache_set, \
             mock.patch.object(
                 pubtator,
                 "_metadata_for_pmids",
                 return_value=("ok", {
                     str(search_row.get("pmid")): {
                         "publication_types": ["Journal Article"],
                         "publication_date": search_row.get("date"),
                     },
                 }, None),
             ), \
             mock.patch.object(
                 pubtator, "_json_get", side_effect=payloads):
            result = pubtator.search_drug_mechanism_assertions(
                "Velunadine", "QRX1")
        return result, cache_set

    def test_preserves_entities_direction_context_sentence_and_lineage(self):
        result, cache_set = self._run({
            "pmid": 99000001,
            "pmcid": "PMCDEV001",
            "title": "A development-fixture experiment",
            "journal": "Synthetic Methods",
            "date": "2024-02-03T00:00:00Z",
            "text_hl": (
                "In cultured cells, @<m>CHEMICAL_Velunadine</m> "
                "@CHEMICAL_DEV:C1 @@@Velunadine@@@ inhibits "
                "@<m>GENE_QRX1</m> @GENE_DEV:G1 @@@QRX1@@@ signaling."
            ),
        })
        self.assertEqual(result["status"], "ok")
        assertion = result["assertions"][0]
        self.assertEqual(assertion["direction"], "inhibitor")
        self.assertEqual(assertion["species"], "unknown")
        self.assertEqual(assertion["experimental_setting"], "in_vitro")
        self.assertEqual(assertion["pmid"], "99000001")
        self.assertEqual(assertion["pmcid"], "PMCDEV001")
        self.assertEqual(assertion["publication_types"], ["Journal Article"])
        self.assertTrue(assertion["primary_experiment"])
        self.assertIn("inhibits", assertion["evidence_sentence"])
        self.assertIsNotNone(assertion["drug_entity"]["start"])
        self.assertIsNotNone(assertion["mechanism_entity"]["start"])
        self.assertIsNotNone(assertion["relation_span"]["start"])
        self.assertIn("pub:99000001", assertion["lineage_id"])
        self.assertEqual(assertion["source_row_id"], "99000001")
        cache_set.assert_called_once()

    def test_missing_entity_highlight_is_filtered_not_fabricated(self):
        result, _ = self._run({
            "pmid": 99000005,
            "title": "A missing-span development control",
            "date": "2024-02-03T00:00:00Z",
            "text_hl": (
                "@<m>CHEMICAL_Velunadine</m> @@@Velunadine@@@ inhibits "
                "QRX1 signaling in cultured cells."
            ),
        })
        self.assertEqual(result["status"], "filtered_empty")
        self.assertEqual(result["assertions"], [])

    def test_negation_is_filtered_not_admitted(self):
        result, _ = self._run({
            "pmid": 99000002,
            "title": "A negative development control",
            "date": "2024-02-03T00:00:00Z",
            "text_hl": (
                "@<m>CHEMICAL_Velunadine</m> @@@Velunadine@@@ did not "
                "inhibit @<m>GENE_QRX1</m> @@@QRX1@@@ in cultured cells."
            ),
        })
        self.assertEqual(result["status"], "filtered_empty")
        self.assertEqual(result["assertions"], [])

    def test_unavailable_and_malformed_never_cache(self):
        with mock.patch.object(pubtator, "get", return_value=None), \
             mock.patch.object(pubtator, "cache_set") as cache_set, \
             mock.patch.object(
                 pubtator, "_json_get",
                 side_effect=requests.Timeout("development timeout")):
            unavailable = pubtator.search_drug_mechanism_assertions(
                "Timeoutadine", "QRX2")
        self.assertEqual(unavailable["status"], "unavailable")
        cache_set.assert_not_called()

        with mock.patch.object(pubtator, "get", return_value=None), \
             mock.patch.object(pubtator, "cache_set") as cache_set, \
             mock.patch.object(pubtator, "_json_get", side_effect=[
                 [{"_id": "@CHEMICAL_Malformedine",
                   "name": "Malformedine", "biotype": "chemical"}],
                 [{"_id": "@GENE_QRX3", "name": "QRX3", "biotype": "gene"}],
                 {"results": "bad"},
             ]):
            malformed = pubtator.search_drug_mechanism_assertions(
                "Malformedine", "QRX3")
        self.assertEqual(malformed["status"], "parse_failed")
        cache_set.assert_not_called()

    def test_metadata_failure_is_degraded_not_empty_and_not_cached(self):
        drug = [{
            "_id": "@CHEMICAL_Degradadine",
            "biotype": "chemical",
            "name": "Degradadine",
        }]
        gene = [{
            "_id": "@GENE_QRX4",
            "biotype": "gene",
            "name": "QRX4",
        }]
        row = {
            "pmid": 99000004,
            "date": "2024-02-03T00:00:00Z",
            "text_hl": (
                "@<m>CHEMICAL_Degradadine</m> @@@Degradadine@@@ inhibits "
                "@<m>GENE_QRX4</m> @@@QRX4@@@ in cultured cells."
            ),
        }
        with mock.patch.object(pubtator, "get", return_value=None), \
             mock.patch.object(pubtator, "cache_set") as cache_set, \
             mock.patch.object(
                 pubtator, "_json_get",
                 side_effect=[drug, gene, {"results": [row]}],
             ), \
             mock.patch.object(
                 pubtator, "_metadata_for_pmids",
                 return_value=("unavailable", {}, "development outage"),
             ):
            result = pubtator.search_drug_mechanism_assertions(
                "Degradadine", "QRX4")
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["assertions"], [])
        self.assertEqual(
            result["source_status"]["europepmc"]["status"], "unavailable")
        cache_set.assert_not_called()


class DetectorContractTest(unittest.TestCase):
    def test_all_four_detector_classes_with_synthetic_evidence(self):
        regulatory = {
            "status": "ok",
            "products": [{
                "citation_eligible": True,
                "spl": {
                    "set_id": "development-set-a",
                    "version": "7",
                    "effective_date": "20240115",
                },
                "regulatory": {
                    "combination": True,
                    "active_ingredients": [
                        {"name": "Velunadine"},
                        {"name": "Quorazene"},
                    ],
                    "product_modality": "biologic",
                    "routes": ["topical"],
                    "dosage_forms": ["cream"],
                },
            }],
        }
        literature = {
            "status": "ok",
            "assertions": [{
                "citation_eligible": True,
                "pmid": "99000001",
                "pmcid": "PMCDEV001",
                "species": "animal",
                "experimental_setting": "animal_in_vivo",
                "experimental_context": "animal",
                "direction": "inhibitor",
                "evidence_sentence": "Velunadine inhibited QRX1 in mice.",
                "lineage_id": "development-lineage-1",
            }],
        }
        findings = detect_audit_findings(
            regulatory,
            literature,
            claimed_route="oral",
            claimed_modality="small molecule",
            claimed_context="systemic plasma exposure",
        )
        by_code = {}
        for finding in findings:
            by_code.setdefault(finding["code"], []).append(finding)
            self.assertEqual(finding["effect"], "disclosure_only")
        self.assertEqual(set(by_code), {"N1", "N2", "N3", "N4"})
        self.assertTrue(any(f["status"] == "flagged" for f in by_code["N1"]))
        self.assertTrue(any(f["status"] == "flagged" for f in by_code["N2"]))
        self.assertTrue(any(f["status"] == "flagged" for f in by_code["N3"]))
        self.assertTrue(any(f["status"] == "flagged" for f in by_code["N4"]))
        n4_text = " ".join(f["rationale"] for f in by_code["N4"])
        self.assertIn("does not by itself prove", n4_text)

    def test_human_cells_are_preclinical_not_human_clear(self):
        findings = detect_audit_findings(
            {"status": "empty", "products": []},
            {
                "status": "ok",
                "assertions": [{
                    "citation_eligible": True,
                    "pmid": "99000006",
                    "species": "human",
                    "experimental_setting": "in_vitro",
                    "experimental_context": "human, in_vitro",
                    "direction": "inhibitor",
                    "evidence_sentence": (
                        "Velunadine inhibited QRX1 in cultured human cells."),
                    "lineage_id": "development-lineage-human-cells",
                }],
            },
        )
        n3 = [f for f in findings if f["code"] == "N3"]
        self.assertEqual(len(n3), 1)
        self.assertEqual(n3[0]["status"], "flagged")
        self.assertIn("preclinical-only", n3[0]["title"])

    def test_failure_states_are_unresolved_not_clear(self):
        findings = detect_audit_findings(
            {"status": "unavailable", "products": []},
            {"status": "parse_failed", "assertions": []},
        )
        self.assertEqual(
            {f["code"] for f in findings}, {"N1", "N2", "N3", "N4"})
        self.assertTrue(all(f["status"] == "unresolved" for f in findings))


if __name__ == "__main__":
    unittest.main()