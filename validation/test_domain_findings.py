"""
Guard tests for api/domain_findings.py and its audit-surface wiring.

The oncology repurposing penalty is a disclosure-only finding: these tests
pin the classifier's deterministic behavior and guarantee the finding rides
along on triage responses (and persisted summaries) without ever touching
verdicts, scores, or statuses.
"""
from __future__ import annotations

import unittest
from unittest import mock

import api.triage as triage
from api.domain_findings import (
    ONCOLOGY_REPURPOSING_PENALTY,
    domain_findings_for,
    oncology_match,
)


def _stub_audit(disease, drug, **kwargs):
    return {"status": "unresolved", "drug_name": drug}


def _stub_save(**kwargs):
    return {"id": "stub-run", "created_at": None}


class OncologyClassifierTest(unittest.TestCase):
    def test_positive_cases(self):
        positives = [
            "Multiple myeloma",
            "breast cancer",
            "Hepatocellular carcinoma",
            "Glioblastoma",
            "Diffuse large B-cell lymphoma",
            "malignant melanoma",
            "colorectal adenocarcinoma",
            "EwIng SaRcOmA",  # case-insensitive
            "pancreatic tumor",
        ]
        for name in positives:
            with self.subTest(name=name):
                self.assertIsNotNone(oncology_match(name), f"should match: {name}")

    def test_negative_cases(self):
        negatives = [
            "Idiopathic pulmonary arterial hypertension",
            "chronic pancreatitis",
            "Pompe disease",
            "MEN2A",  # cancer-predisposing but no keyword — conservative by design
            "",
        ]
        for name in negatives:
            with self.subTest(name=name):
                self.assertIsNone(oncology_match(name), f"should not match: {name}")

    def test_sarcoidosis_word_boundary(self):
        # Regression guard: naive substring matching would flag sarcoidosis.
        self.assertIsNone(oncology_match("sarcoidosis"))


class FindingPayloadTest(unittest.TestCase):
    def test_payload_is_fresh_copy_with_provenance(self):
        findings = domain_findings_for("Multiple myeloma")
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["id"], ONCOLOGY_REPURPOSING_PENALTY["id"])
        self.assertEqual(f["matched_term"].lower(), "myeloma")
        self.assertEqual(f["disease_name"], "Multiple myeloma")
        self.assertEqual(f["provenance"]["hypothesis_id"], "run-518207db-H26")
        # Caller mutation must not leak into the module constant
        f["title"] = "mutated"
        self.assertNotEqual(ONCOLOGY_REPURPOSING_PENALTY["title"], "mutated")

    def test_confound_record_is_honest(self):
        f = domain_findings_for("lung cancer")[0]
        statuses = {c["name"]: c["status"] for c in f["confounds"]}
        # The two computable confounds survive; the untestable one is
        # disclosed, never silently dropped or marked as survived.
        self.assertEqual(statuses["oncology trial phase-mix skew"], "not_testable")
        self.assertEqual(sum(1 for s in statuses.values() if s == "survives"), 2)


class TriageWiringTest(unittest.TestCase):
    def test_triage_attaches_domain_findings_for_oncology(self):
        with mock.patch.object(triage, "run_audit", _stub_audit), \
             mock.patch.object(triage.triage_db, "save_triage_run", _stub_save):
            res = triage.run_triage("Multiple myeloma", ["Asprin"])
        self.assertEqual(res["status"], "ok")
        self.assertEqual(
            res["domain_findings"][0]["id"], "oncology_repurposing_penalty"
        )
        # Persisted summary carries the finding so retrieved runs show it too
        self.assertEqual(
            res["summary"]["domain_findings"][0]["matched_term"].lower(), "myeloma"
        )
        # Verdict itself is untouched by the finding
        self.assertEqual(res["verdicts"][0]["status"], "unresolved")
        self.assertNotIn(
            "ONCOLOGY", " ".join(res["verdicts"][0]["flags"])
        )

    def test_triage_no_findings_for_non_oncology(self):
        with mock.patch.object(triage, "run_audit", _stub_audit), \
             mock.patch.object(triage.triage_db, "save_triage_run", _stub_save):
            res = triage.run_triage("chronic pancreatitis", ["Asprin"])
        self.assertEqual(res["domain_findings"], [])
        self.assertNotIn("domain_findings", res["summary"])


if __name__ == "__main__":
    unittest.main()
