"""Non-network controls for bounded, holdout-safe direction checks."""

import os
import unittest
from unittest.mock import MagicMock, patch

from data_sources import holdout, mechanism_direction


class MechanismDirectionHoldoutTest(unittest.TestCase):
    def tearDown(self):
        holdout.deactivate()

    def test_client_has_explicit_timeout_and_no_retries(self):
        with patch.dict(os.environ, {
            "AI_INTEGRATIONS_OPENAI_BASE_URL": "http://example.invalid",
            "AI_INTEGRATIONS_OPENAI_API_KEY": "test-only",
        }, clear=False), patch.object(
            mechanism_direction, "OpenAI"
        ) as constructor:
            mechanism_direction._openai_client()
        constructor.assert_called_once_with(
            base_url="http://example.invalid",
            api_key="test-only",
            timeout=mechanism_direction._AI_TIMEOUT_SECONDS,
            max_retries=mechanism_direction._AI_MAX_RETRIES,
        )

    def test_timeout_returns_insufficient_without_cap(self):
        client = MagicMock()
        client.responses.create.side_effect = TimeoutError("bounded timeout")
        with patch.object(
            mechanism_direction, "_openai_client", return_value=client
        ), patch.object(
            mechanism_direction, "get", return_value=None
        ), patch.object(
            mechanism_direction, "cache_set"
        ):
            result = mechanism_direction.check_mechanism_direction(
                "ControlDrug", "TGT", "INHIBITOR", "target action",
                "Control disease",
            )
        self.assertEqual(
            result["verdict"], mechanism_direction.VERDICT_INSUFFICIENT
        )
        self.assertFalse(result["incompatible"])
        self.assertFalse(result["compatible"])
        self.assertIn("bounded timeout", result["reason"])

    def test_salt_family_is_anonymous_in_both_prompts(self):
        holdout.activate(["ParentDrug"])
        holdout.register_molecules(
            {"CHEMBLPARENT", "CHEMBLSALT"}, {"CHEMBLPARENT"}
        )
        holdout.mark_resolved()

        captured = []
        client = MagicMock()

        search_response = MagicMock()
        search_response.output_text = "Retrieved evidence."

        classify_response = MagicMock()
        choice = MagicMock()
        choice.message.content = (
            "VERDICT: INSUFFICIENT_INFO\n"
            "REASON: insufficient evidence\n"
            "CITATIONS: none"
        )
        classify_response.choices = [choice]

        def search(**kwargs):
            captured.append(str(kwargs))
            return search_response

        def classify(**kwargs):
            captured.append(str(kwargs))
            return classify_response

        client.responses.create.side_effect = search
        client.chat.completions.create.side_effect = classify

        with patch.object(
            mechanism_direction, "_openai_client", return_value=client
        ), patch.object(
            mechanism_direction, "get", return_value=None
        ), patch.object(
            mechanism_direction, "cache_set"
        ):
            mechanism_direction.check_mechanism_direction(
                "Different Salt Display Name",
                "TGT",
                "INHIBITOR",
                "target action",
                "Control disease",
                candidate_chembl_ids=["CHEMBLSALT"],
            )

        prompt_text = " ".join(captured)
        self.assertNotIn("Different Salt Display Name", prompt_text)
        self.assertNotIn("ParentDrug", prompt_text)
        self.assertIn("held-out candidate", prompt_text)

    def test_structure_variant_is_anonymous_without_chembl_id(self):
        holdout.activate(["ParentDrug"])
        holdout.register_inchikeys({"ABCDEFGHIJKLMN-ABCDEFGHIJ-N"})
        captured = []
        client = MagicMock()
        search_response = MagicMock()
        search_response.output_text = "Retrieved evidence."
        classify_response = MagicMock()
        choice = MagicMock()
        choice.message.content = (
            "VERDICT: INSUFFICIENT_INFO\n"
            "REASON: insufficient evidence\n"
            "CITATIONS: none"
        )
        classify_response.choices = [choice]
        client.responses.create.side_effect = lambda **kwargs: (
            captured.append(str(kwargs)) or search_response
        )
        client.chat.completions.create.side_effect = lambda **kwargs: (
            captured.append(str(kwargs)) or classify_response
        )

        with patch.object(
            mechanism_direction, "_openai_client", return_value=client
        ), patch.object(
            mechanism_direction, "get", return_value=None
        ), patch.object(
            mechanism_direction, "cache_set"
        ):
            mechanism_direction.check_mechanism_direction(
                "Different Variant Display Name",
                "TGT",
                "INHIBITOR",
                "target action",
                "Control disease",
                candidate_inchikey="ABCDEFGHIJKLMN-ZYXWVUTSRQ-N",
            )

        prompt_text = " ".join(captured)
        self.assertNotIn("Different Variant Display Name", prompt_text)
        self.assertNotIn("ParentDrug", prompt_text)
        self.assertIn("held-out candidate", prompt_text)


if __name__ == "__main__":
    unittest.main()