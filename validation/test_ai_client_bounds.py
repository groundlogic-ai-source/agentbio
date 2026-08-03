"""Operational bounds for AI-backed Reviewer evidence gates."""

import os
import unittest
from unittest.mock import MagicMock, patch

from data_sources import clinicaltrials, safety_check


class AIClientBoundsTest(unittest.TestCase):
    def test_clinicaltrials_client_is_bounded(self):
        with patch.dict(os.environ, {
            "AI_INTEGRATIONS_ANTHROPIC_BASE_URL": "http://example.invalid",
            "AI_INTEGRATIONS_ANTHROPIC_API_KEY": "test-only",
        }, clear=False), patch.object(
            clinicaltrials.anthropic, "Anthropic"
        ) as constructor:
            clinicaltrials._anthropic_client()
        constructor.assert_called_once_with(
            base_url="http://example.invalid",
            api_key="test-only",
            timeout=clinicaltrials._AI_TIMEOUT_SECONDS,
            max_retries=clinicaltrials._AI_MAX_RETRIES,
        )

    def test_clinicaltrials_timeout_is_unclear_not_failure(self):
        client = MagicMock()
        client.messages.create.side_effect = TimeoutError("bounded timeout")
        result = clinicaltrials._classify_why_stopped(
            "The trial was stopped.", client
        )
        self.assertEqual(result, "UNCLEAR")

    def test_safety_client_is_bounded_and_timeout_does_not_cap(self):
        with patch.dict(os.environ, {
            "AI_INTEGRATIONS_ANTHROPIC_BASE_URL": "http://example.invalid",
            "AI_INTEGRATIONS_ANTHROPIC_API_KEY": "test-only",
        }, clear=False), patch.object(
            safety_check, "get", return_value=None
        ), patch.object(
            safety_check, "cache_set"
        ), patch.object(
            safety_check.anthropic, "Anthropic"
        ) as constructor:
            client = MagicMock()
            client.messages.create.side_effect = TimeoutError(
                "bounded timeout"
            )
            constructor.return_value = client
            result = safety_check.web_safety_check("ControlDrug")

        constructor.assert_called_once_with(
            base_url="http://example.invalid",
            api_key="test-only",
            timeout=safety_check._AI_TIMEOUT_SECONDS,
            max_retries=safety_check._AI_MAX_RETRIES,
        )
        self.assertEqual(result["verdict"], "ERROR")
        self.assertFalse(result["confirmed"])
        self.assertIn("no cap applied", result["disclosure_text"])


if __name__ == "__main__":
    unittest.main()