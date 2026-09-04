"""Offline contracts for shared LLM routing and safe telemetry."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data_sources import llm_failover as failover


def _events(stdout: str) -> list[dict[str, object]]:
    prefix = "[llm_telemetry] "
    return [
        json.loads(line[len(prefix):])
        for line in stdout.splitlines()
        if line.startswith(prefix)
    ]


class LlmFailoverTelemetryTests(unittest.TestCase):
    def test_scheduler_enforces_per_provider_concurrency_deterministically(self) -> None:
        scheduler = failover._ProviderAdmissionScheduler(
            max_concurrent=2, min_interval_seconds=0)
        with scheduler._condition:
            self.assertIsNone(scheduler._try_acquire_locked("openai", 10.0))
            self.assertIsNone(scheduler._try_acquire_locked("openai", 10.0))
            # A full provider has no time-only admission deadline: release
            # notification, rather than a global lock sleep, is required.
            self.assertEqual(scheduler._try_acquire_locked("openai", 10.0), 0.0)
            self.assertIsNone(scheduler._try_acquire_locked("anthropic", 10.0))
        scheduler.release("openai")
        with scheduler._condition:
            self.assertIsNone(scheduler._try_acquire_locked("openai", 10.0))

    def test_scheduler_reserves_minimum_start_interval_deterministically(self) -> None:
        scheduler = failover._ProviderAdmissionScheduler(
            max_concurrent=3, min_interval_seconds=1.5)
        with scheduler._condition:
            self.assertIsNone(scheduler._try_acquire_locked("openai", 100.0))
        scheduler.release("openai")
        with scheduler._condition:
            self.assertAlmostEqual(
                scheduler._try_acquire_locked("openai", 100.4) or 0, 1.1)
            self.assertIsNone(scheduler._try_acquire_locked("anthropic", 100.4))
            self.assertIsNone(scheduler._try_acquire_locked("openai", 101.5))

    def test_chat_records_model_operation_attempt_and_sdk_usage(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=11,
                                  total_tokens=18))
        output = io.StringIO()
        with patch.object(failover, "_available_providers", return_value=["openai"]), \
             patch.object(failover, "_openai_text", return_value=("ok", response)):
            with redirect_stdout(output):
                text, provider = failover.chat_text(
                    "never log this prompt", operation_label="bounded-screen")

        self.assertEqual((text, provider), ("ok", "openai"))
        event = _events(output.getvalue())[0]
        self.assertEqual(event["provider"], "openai")
        self.assertEqual(event["model"], failover.OPENAI_STANDARD_TEXT_MODEL)
        self.assertEqual(event["operation"], "bounded-screen")
        self.assertEqual(event["attempt"], 1)
        self.assertTrue(event["success"])
        self.assertEqual(event["token_usage"], {
            "input_tokens": 7, "output_tokens": 11, "total_tokens": 18})
        self.assertNotIn("never log this prompt", output.getvalue())

    def test_chat_error_telemetry_excludes_error_payload_and_prompt(self) -> None:
        output = io.StringIO()
        with patch.object(failover, "_available_providers", return_value=["anthropic"]), \
             patch.object(failover, "_anthropic_text",
                          side_effect=ValueError("secret prompt payload")):
            with redirect_stdout(output), self.assertRaises(ValueError):
                failover.chat_text("secret prompt payload")

        event = _events(output.getvalue())[0]
        self.assertFalse(event["success"])
        self.assertEqual(event["error"], "ValueError")
        self.assertNotIn("secret prompt payload", output.getvalue())

    def test_backoff_telemetry_normalizes_dict_usage(self) -> None:
        response = SimpleNamespace(
            usage={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})
        output = io.StringIO()
        scheduler = MagicMock()
        with patch.object(failover, "_provider_scheduler", scheduler), \
             redirect_stdout(output):
            self.assertIs(failover.call_with_backoff(
                lambda: response, label="provider-tool", provider="openai",
                model="gpt-5.4"), response)

        event = _events(output.getvalue())[0]
        self.assertEqual(event["operation"], "provider-tool")
        self.assertEqual(event["token_usage"]["total_tokens"], 7)
        scheduler.acquire.assert_called_once_with("openai")
        scheduler.release.assert_called_once_with("openai")

    def test_cheap_tier_is_explicit_and_defaults_remain_capable(self) -> None:
        self.assertEqual(
            failover.text_model_for("anthropic"),
            failover.ANTHROPIC_CRITICAL_TEXT_MODEL)
        self.assertEqual(
            failover.text_model_for("openai"),
            failover.OPENAI_CRITICAL_TEXT_MODEL)
        self.assertEqual(
            failover.text_model_for("openai", failover.MODEL_TIER_CHEAP),
            failover.OPENAI_CHEAP_TEXT_MODEL)
        with self.assertRaises(ValueError):
            failover.text_model_for("openai", "unapproved-tier")


if __name__ == "__main__":
    unittest.main()