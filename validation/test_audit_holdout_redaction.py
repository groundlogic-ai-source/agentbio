"""Guards for disease-blind redaction of the audit evidence lanes.

The triage discrimination benchmark asks whether the audit layer can tell
a confirmed repurposing apart from the pipeline's own top picks.  That
question is only meaningful if the audit envelope does not already
contain the answer.  It did: probing five confirmed repurposings with the
drug held out recovered the approved indication in 5/5 cases, entirely
through free-text label quotes.

These tests pin the fix so it cannot silently regress:

* production output is untouched when no holdout is active;
* every free-text surface is removed when one is;
* the allowlist is fail-closed, so a newly added source field is dropped
  by default rather than leaking;
* the deterministic detectors produce identical findings either way, so
  redaction buys blindness without changing what the audit measures.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.audit_context import detect_audit_findings  # noqa: E402
from data_sources import holdout  # noqa: E402
from data_sources.audit_redaction import redact_audit_lanes  # noqa: E402

SECRET = "Pulmonary arterial hypertension"


def _label_payload() -> dict:
    """A label lane payload with the indication planted in every free-text
    field, plus structured fields the detectors depend on."""
    return {
        "provider": "openfda",
        "status": "ok",
        "query": {"drug_name": "Sildenafil"},
        "citation_cutoff": "2026-08-10",
        "error": None,
        "retrieved_count": 1,
        "filtered_count": 0,
        "products": [{
            "identity": {"generic_names": ["sildenafil"],
                         "brand_names": ["Revatio"]},
            "regulatory": {
                "active_ingredients": [{"name": "SILDENAFIL CITRATE"}],
                "combination": False,
                "routes": ["ORAL"],
                "dosage_forms": ["TABLET"],
                "product_modality": "small_molecule",
                "modality_basis": "product_type",
                "product_types": ["HUMAN PRESCRIPTION DRUG"],
                "application_numbers": ["NDA021845"],
            },
            "spl": {"set_id": "abc", "version": "3",
                    "effective_date": "20240101", "spl_id": "x",
                    "document_id": "y"},
            "citation_eligible": True,
            "source_url": "https://dailymed.nlm.nih.gov/x",
            "evidence": [
                {"field": f, "quote": f"Indicated for {SECRET}.",
                 "source_id": "abc"}
                for f in ("indications_and_usage", "mechanism_of_action",
                          "clinical_pharmacology", "description",
                          "dosage_and_administration", "purpose")
            ],
        }],
    }


def _literature_payload() -> dict:
    return {
        "provider": "pubtator3",
        "status": "ok",
        "drug": {"name": "Sildenafil"},
        "mechanism": {"name": "PDE5A", "canonical_id": "PDE5A"},
        "retrieved_count": 1,
        "filtered_count": 0,
        "citation_cutoff": "2026-08-10",
        "error": None,
        "assertions": [{
            "source_row_id": "r1", "pmid": "123", "pmcid": "", "doi": "",
            "title": f"Sildenafil in {SECRET}",
            "journal": "J Test", "publication_types": ["Journal Article"],
            "publication_date": "2020-01-01", "citation_eligible": True,
            "drug_entity": {"name": "sildenafil"},
            "mechanism_entity": {"name": "PDE5A"},
            "species": "mouse", "organism": "",
            "experimental_setting": "preclinical_in_vivo",
            "experimental_context": f"murine model of {SECRET}",
            "relation": "PubTator3", "action": "inhibits",
            "direction": "down",
            "evidence_sentence": f"Sildenafil improved {SECRET} in mice.",
            "evidence_location": "highlighted passage",
            "relation_span": f"sildenafil ... {SECRET}",
            "primary_experiment": True,
            "publication_type_status": "admitted",
            "source": "pubtator3", "lineage_id": "l1",
        }],
    }


def _all_text(node, out=None):
    out = [] if out is None else out
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _all_text(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_text(value, out)
    return out


class ProductionPathUntouched(unittest.TestCase):
    def test_no_holdout_means_no_redaction(self):
        """Redaction is a benchmark affordance; ordinary audits must be
        byte-identical to what they were before it existed."""
        self.assertFalse(holdout.is_active())
        label, lit = _label_payload(), _literature_payload()
        out_label, out_lit, disclosure = redact_audit_lanes(label, lit)
        self.assertIs(out_label, label)
        self.assertIs(out_lit, lit)
        self.assertFalse(disclosure["applied"])
        self.assertIn(SECRET, " ".join(_all_text(out_label)))


class RedactionBlocksLeakage(unittest.TestCase):
    def test_indication_absent_from_both_lanes_under_holdout(self):
        with holdout.holdout_active(["Sildenafil"]):
            label, lit, disclosure = redact_audit_lanes(
                _label_payload(), _literature_payload())
        self.assertTrue(disclosure["applied"])
        for lane, name in ((label, "regulatory"), (lit, "literature")):
            joined = " ".join(_all_text(lane)).lower()
            self.assertNotIn(SECRET.lower(), joined, f"{name} lane leaked")
            self.assertNotIn("hypertension", joined, f"{name} lane leaked")

    def test_structured_detector_inputs_survive(self):
        with holdout.holdout_active(["Sildenafil"]):
            label, lit, _ = redact_audit_lanes(
                _label_payload(), _literature_payload())
        regulatory = label["products"][0]["regulatory"]
        self.assertEqual(regulatory["routes"], ["ORAL"])
        self.assertEqual(regulatory["product_modality"], "small_molecule")
        self.assertFalse(regulatory["combination"])
        self.assertTrue(label["products"][0]["citation_eligible"])
        assertion = lit["assertions"][0]
        self.assertEqual(assertion["experimental_setting"],
                         "preclinical_in_vivo")
        self.assertEqual(assertion["pmid"], "123")
        self.assertTrue(assertion["citation_eligible"])

    def test_allowlist_is_fail_closed_for_new_source_fields(self):
        """A field added to a source later must be dropped by default.
        A denylist would leak it until someone remembered to add it."""
        label = _label_payload()
        label["products"][0]["future_free_text"] = f"treats {SECRET}"
        lit = _literature_payload()
        lit["assertions"][0]["future_free_text"] = f"treats {SECRET}"
        with holdout.holdout_active(["Sildenafil"]):
            out_label, out_lit, _ = redact_audit_lanes(label, lit)
        self.assertNotIn("future_free_text", out_label["products"][0])
        self.assertNotIn("future_free_text", out_lit["assertions"][0])

    def test_redaction_is_disclosed_not_silent(self):
        with holdout.holdout_active(["Sildenafil"]):
            label, lit, disclosure = redact_audit_lanes(
                _label_payload(), _literature_payload())
        self.assertEqual(label["holdout_redaction"]["dropped_quote_count"], 6)
        self.assertIn("indications_and_usage",
                      label["holdout_redaction"]["dropped_fields"])
        self.assertEqual(
            lit["holdout_redaction"]["redacted_assertion_count"], 1)
        self.assertEqual(
            [d.lower() for d in disclosure["held_out_drugs"]], ["sildenafil"])


class DetectorsUnaffected(unittest.TestCase):
    """Redaction must buy blindness without changing what the audit
    measures, or the benchmark would score a different instrument than
    the one the product ships."""

    def _findings(self, label, lit, **kwargs):
        return [(f["code"], f["status"], f["title"])
                for f in detect_audit_findings(label, lit, **kwargs)]

    def test_findings_identical_with_and_without_redaction(self):
        plain = self._findings(
            _label_payload(), _literature_payload(),
            claimed_route="oral", claimed_modality="small molecule",
            claimed_context="systemic")
        with holdout.holdout_active(["Sildenafil"]):
            label, lit, _ = redact_audit_lanes(
                _label_payload(), _literature_payload())
        redacted = self._findings(
            label, lit, claimed_route="oral",
            claimed_modality="small molecule", claimed_context="systemic")
        self.assertEqual(plain, redacted)

    def test_dose_finding_degrades_visibly_not_silently(self):
        """The one detector that reads free text is the N4 dose
        comparison. Under redaction it must fall back to `unresolved` --
        an explicit 'not measured', never a silent pass."""
        plain = self._findings(
            _label_payload(), _literature_payload(), claimed_dose="20 mg")
        self.assertIn(
            ("N4", "review",
             "Claimed dose requires comparison with dated label dosing"),
            plain)
        with holdout.holdout_active(["Sildenafil"]):
            label, lit, _ = redact_audit_lanes(
                _label_payload(), _literature_payload())
        redacted = self._findings(label, lit, claimed_dose="20 mg")
        self.assertIn(
            ("N4", "unresolved",
             "Claimed dose lacks structured label dosing evidence"),
            redacted)


if __name__ == "__main__":
    unittest.main()
