"""Focused safety-v3 source-layer regression tests."""

import json
import unittest
from unittest.mock import MagicMock, patch

from data_sources.safety_check import (
    SCHEMA_VERSION, _fetch_regulator_source, classify_safety_evidence,
    normalize_drug_identity,
)
from agents.reviewer import _availability_gate


# Exact false-positive evidence pattern from the Cantú/glibenclamide review:
# a branded commercial discontinuation while generic active ingredient remains.
CANTU_GLIBENCLAMIDE_FALSE_POSITIVE = (
    "Sanofi has discontinued Daonil 5mg tablets (glibenclamide). "
    "Other manufacturers continue to supply generic glibenclamide tablets. "
    "There has been no formal withdrawal of glibenclamide."
)


def _answer(**overrides):
    answer = {
        "safety_status": "WITHDRAWN_FOR_SAFETY",
        "withdrawal": "YES",
        "black_box": "NO",
        "source_name": "FDA",
        "source_url": "https://www.fda.gov/drugs/example",
        "exact_quote": "FDA withdrew glyburide tablets for safety reasons.",
        "jurisdiction": "United States",
        "formulation": "oral tablets",
        "matched_identity": "glyburide",
        "contradictions": [],
    }
    answer.update(overrides)
    return json.dumps(answer)


class SafetyV3Test(unittest.TestCase):
    def setUp(self):
        self.source_patch = patch(
            "data_sources.safety_check._fetch_regulator_source",
            return_value={
                "verified": True,
                "text": (
                    "FDA withdrew glyburide tablets for safety reasons. "
                    "United States oral tablets."
                ),
                "reason": None,
            },
        )
        self.source_patch.start()

    def tearDown(self):
        self.source_patch.stop()

    def test_schema_and_alias_normalization(self):
        self.assertEqual(SCHEMA_VERSION, "safety-v3")
        self.assertEqual(
            normalize_drug_identity("glyburide"),
            normalize_drug_identity("glibenclamide"),
        )

    def test_scoped_regulator_quote_can_confirm_alias(self):
        text = (
            "FDA withdrew glyburide tablets for safety reasons. "
            "https://www.fda.gov/drugs/example"
        )
        result = classify_safety_evidence("glibenclamide", text, _answer())
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["verdict"], "YES")
        self.assertTrue(result["scope"]["identity_matches"])
        self.assertEqual(result["schema_version"], "safety-v3")

    def test_cantu_brand_discontinuation_is_not_safety_withdrawal(self):
        answer = _answer(
            safety_status="BRAND_DISCONTINUED",
            source_name="Sanofi",
            source_url="https://www.sanofi.example/notice",
            exact_quote="Sanofi has discontinued Daonil 5mg tablets",
            jurisdiction="United Kingdom",
            formulation="Daonil 5mg tablets",
            matched_identity="glibenclamide",
        )
        result = classify_safety_evidence(
            "glyburide", CANTU_GLIBENCLAMIDE_FALSE_POSITIVE, answer
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["safety_status"], "BRAND_DISCONTINUED")
        self.assertIn("no formal withdrawal", result["contradictions"])

    def test_adversarial_fake_authority_cannot_confirm(self):
        quote = "Blog author says the medicine was withdrawn for safety."
        result = classify_safety_evidence(
            "glyburide",
            quote + " https://drug-news.example/post",
            _answer(
                source_name="FDA",
                source_url="https://drug-news.example/post",
                exact_quote=quote,
            ),
        )
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["confidence_status"], "UNCLEAR")
        self.assertFalse(
            result["authoritative_source"]["verified_regulator_domain"]
        )

    def test_fabricated_regulator_url_cannot_confirm(self):
        quote = "FDA withdrew glyburide tablets for safety reasons."
        with patch(
            "data_sources.safety_check._fetch_regulator_source",
            return_value={
                "verified": False, "text": "", "reason": "http_status_404"},
        ):
            result = classify_safety_evidence("glyburide", quote, _answer())
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["confidence_status"], "UNCLEAR")

    def test_model_search_quote_cannot_replace_fetched_source_quote(self):
        quote = "FDA withdrew glyburide tablets for safety reasons."
        search_text = f"{quote} https://www.fda.gov/drugs/example"
        with patch(
            "data_sources.safety_check._fetch_regulator_source",
            return_value={
                "verified": True,
                "text": (
                    "FDA product page for glyburide oral tablets in the "
                    "United States. No withdrawal statement is present."
                ),
                "reason": None,
            },
        ):
            result = classify_safety_evidence(
                "glyburide", search_text, _answer())
        self.assertFalse(result["confirmed"])
        self.assertFalse(result["quote_verified_in_fetched_source"])

    def test_unreachable_or_unreadable_authority_cannot_confirm(self):
        for reason in ("request_failed:Timeout", "unreadable_content_type"):
            with self.subTest(reason=reason), patch(
                "data_sources.safety_check._fetch_regulator_source",
                return_value={"verified": False, "text": "", "reason": reason},
            ):
                result = classify_safety_evidence(
                    "glyburide", "model search text", _answer())
                self.assertFalse(result["confirmed"])
                self.assertEqual(result["confidence_status"], "UNCLEAR")
                self.assertEqual(
                    result["authoritative_source"]["retrieval_status"],
                    "UNVERIFIED",
                )

    def test_contradiction_overrides_well_formed_yes(self):
        quote = "FDA withdrew glyburide tablets for safety reasons."
        text = (
            f"{quote} Generic products remain available and there was no "
            "formal withdrawal. https://www.fda.gov/drugs/example"
        )
        result = classify_safety_evidence("glyburide", text, _answer())
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["verdict"], "CONFLICT")
        self.assertEqual(result["confidence_status"], "CONFLICT")

    def test_commercial_and_warning_dispositions_never_confirm(self):
        for status in (
            "BRAND_DISCONTINUED", "MANUFACTURER_DISCONTINUED", "NOT_MARKETED",
            "INGREDIENT_UNAVAILABLE", "ORDINARY_WARNING", "BOXED_WARNING",
        ):
            with self.subTest(status=status):
                result = classify_safety_evidence(
                    "glyburide",
                    "FDA withdrew glyburide tablets for safety reasons. "
                    "https://www.fda.gov/drugs/example",
                    _answer(safety_status=status),
                )
                self.assertFalse(result["confirmed"])

    def test_verified_global_ingredient_unavailability_uses_separate_gate(self):
        unavailable_quote = (
            "Glyburide oral tablets are unavailable worldwide.")
        with patch(
            "data_sources.safety_check._fetch_regulator_source",
            return_value={
                "verified": True,
                "text": unavailable_quote,
                "reason": None,
            },
        ):
            result = classify_safety_evidence(
                "glibenclamide",
                "model-provided search text is not trusted",
                _answer(
                    safety_status="INGREDIENT_UNAVAILABLE",
                    withdrawal="NO",
                    exact_quote=unavailable_quote,
                    jurisdiction="worldwide",
                    formulation="oral tablets",
                    matched_identity="glyburide",
                ),
            )
        self.assertFalse(result["confirmed"])
        gate = _availability_gate({}, result)
        self.assertTrue(gate["blocks_prioritization"])
        self.assertEqual(
            gate["status"], "GLOBALLY_UNAVAILABLE_ACTIVE_INGREDIENT")

    def test_missing_scope_or_non_verbatim_quote_is_unclear(self):
        text = (
            "FDA withdrew glyburide tablets for safety reasons. "
            "https://www.fda.gov/drugs/example"
        )
        for changes in (
            {"jurisdiction": None},
            {"formulation": None},
            {"matched_identity": "glipizide"},
            {"exact_quote": "A quote not present in the evidence"},
        ):
            with self.subTest(changes=changes):
                result = classify_safety_evidence(
                    "glyburide", text, _answer(**changes)
                )
                self.assertFalse(result["confirmed"])
                self.assertEqual(result["confidence_status"], "UNCLEAR")

    def test_legacy_projection_fields_are_retained(self):
        result = classify_safety_evidence("glyburide", "", "")
        for field in (
            "confirmed", "black_box_advisory", "layer", "verdict",
            "black_box_verdict", "citation", "search_summary",
            "disclosure_text",
        ):
            self.assertIn(field, result)

    def test_regulator_fetch_rejects_redirect_without_following_it(self):
        response = MagicMock(status_code=302)
        with patch(
            "data_sources.safety_check.requests.get",
            return_value=response,
        ) as request:
            result = _fetch_regulator_source(
                "https://www.fda.gov/drugs/control")
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "http_status_302")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_regulator_fetch_rejects_pdf_as_unreadable(self):
        response = MagicMock(
            status_code=200,
            url="https://www.fda.gov/drugs/control.pdf",
            encoding="utf-8",
        )
        response.headers = {"content-type": "application/pdf"}
        with patch(
            "data_sources.safety_check.requests.get",
            return_value=response,
        ):
            result = _fetch_regulator_source(
                "https://www.fda.gov/drugs/control.pdf")
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "unreadable_content_type")

    def test_regulator_fetch_never_requests_non_allowlisted_url(self):
        with patch(
            "data_sources.safety_check.requests.get",
        ) as request:
            result = _fetch_regulator_source(
                "https://fda.gov.attacker.example/control")
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "url_not_allowlisted")
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()