"""Regression coverage for shared external-provider request hardening."""

import unittest
import threading
import time
from unittest.mock import Mock, patch

import requests

from data_sources import chembl, pubmed
from data_sources.provider_request_policy import (
    ProviderCircuitOpen,
    _reset_for_tests,
    request,
    source_metrics,
)
import data_sources.provider_request_policy as request_policy


class _Response:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class ProviderRequestPolicyTests(unittest.TestCase):
    def setUp(self):
        _reset_for_tests()

    @patch("data_sources.provider_request_policy.time.sleep")
    def test_retries_transient_429_using_retry_after(self, sleep):
        healthy = _Response()
        call = Mock(side_effect=[_Response(429, {"Retry-After": "2"}), healthy])
        self.assertIs(request("ncbi", call, "https://example.test"), healthy)
        self.assertEqual(call.call_count, 2)
        sleep.assert_any_call(2.0)
        metrics = source_metrics("ncbi")["ncbi"]
        self.assertEqual(metrics["retries"], 1)
        self.assertEqual(metrics["successes"], 1)

    @patch("data_sources.provider_request_policy.time.sleep")
    def test_circuit_opens_after_bounded_transient_failures(self, _sleep):
        call = Mock(return_value=_Response(503))
        for _ in range(3):
            with self.assertRaises(requests.HTTPError):
                request("chembl", call, "https://example.test")
        with self.assertRaises(ProviderCircuitOpen):
            request("chembl", call, "https://example.test")
        self.assertEqual(call.call_count, 9)  # three bounded attempts per call
        self.assertTrue(source_metrics("chembl")["chembl"]["circuit_open"])

    def test_queued_caller_rechecks_circuit_after_semaphore_admission(self):
        entered = threading.Event()
        release = threading.Event()
        second_waiting_for_slot = threading.Event()
        queued_result = []
        original_semaphore = request_policy._semaphores["ncbi"]

        class ObservedSemaphore:
            def __init__(self):
                self.entries = 0
                self.entries_lock = threading.Lock()

            def __enter__(self):
                with self.entries_lock:
                    self.entries += 1
                    if self.entries == 2:
                        second_waiting_for_slot.set()
                original_semaphore.acquire()
                return self

            def __exit__(self, *args):
                original_semaphore.release()

        request_policy._semaphores["ncbi"] = ObservedSemaphore()

        def blocking_request(_url, **_kwargs):
            entered.set()
            release.wait(timeout=2)
            return _Response()

        try:
            first = threading.Thread(
                target=lambda: request("ncbi", blocking_request, "https://example.test"))
            first.start()
            self.assertTrue(entered.wait(timeout=1))
            queued = threading.Thread(
                target=lambda: queued_result.append(
                    self._request_exception("ncbi", "https://example.test")))
            queued.start()
            # The second caller has passed its initial circuit check and is now
            # blocked on NCBI's one request slot.
            self.assertTrue(second_waiting_for_slot.wait(timeout=1))
            with request_policy._lock:
                request_policy._circuit_open_until["ncbi"] = time.monotonic() + 5
            release.set()
            first.join(timeout=2)
            queued.join(timeout=2)
            self.assertEqual(queued_result, [ProviderCircuitOpen])
        finally:
            release.set()
            request_policy._semaphores["ncbi"] = original_semaphore

    @staticmethod
    def _request_exception(provider, url):
        try:
            request(provider, Mock(return_value=_Response()), url)
        except Exception as exc:  # test records the observable exception class
            return type(exc)
        return None

    def test_pubmed_transient_failure_is_not_cached(self):
        with patch.object(pubmed, "get", return_value=None), \
             patch.object(pubmed, "cache_set") as cache_set, \
             patch.object(pubmed, "_esearch", side_effect=TimeoutError("down")):
            self.assertEqual(pubmed.fetch_raw_abstracts("query"), {})
        cache_set.assert_not_called()

    def test_relationship_gate_failure_is_degraded_and_not_cached(self):
        with patch.object(pubmed, "get", return_value=None), \
             patch.object(pubmed, "cache_set") as cache_set, \
             patch.object(pubmed, "_anthropic_client", return_value=Mock()), \
             patch.object(pubmed, "_esearch", return_value=["1"]), \
             patch.object(pubmed, "_efetch", return_value={"1": "abstract"}), \
             patch.object(pubmed, "chat_text", side_effect=RuntimeError("LLM down")):
            result = pubmed.search_literature("Drug", "GENE", "Disease")
        self.assertEqual(result["source_status"], "DEGRADED")
        self.assertEqual(result["n_kept"], 0)
        self.assertIn("LLM down", result["error"])
        cache_set.assert_not_called()

    def test_chembl_ambiguous_empty_approved_result_is_not_cached(self):
        with patch.object(chembl, "get", return_value=None), \
             patch.object(chembl, "cache_set") as cache_set, \
             patch.object(chembl, "_resolve_target_chembl_id", return_value=[]):
            result = chembl.get_approved_drugs_for_target("P12345")
        self.assertEqual(result["approved_drug_count"], 0)
        cache_set.assert_not_called()

    def test_literature_efetch_uses_shared_ncbi_policy(self):
        xml = b"<PubmedArticleSet />"
        response = Mock(content=xml)
        with patch("data_sources.literature_limitation.provider_request",
                   return_value=response) as provider_call:
            from data_sources.literature_limitation import _fetch_records
            self.assertEqual(_fetch_records(["123"]), [])
        self.assertEqual(provider_call.call_args.args[:3],
                         ("ncbi", unittest.mock.ANY,
                          "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"))


if __name__ == "__main__":
    unittest.main()