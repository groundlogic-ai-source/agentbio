"""openFDA API key must reach every request site.

Regression test for a real failure (2026-08-14): the key helper existed and
was unit-tested in isolation, but no call site actually used it, so every
request still went out anonymously and the study stayed rate-limited. These
tests assert on the params dict handed to requests.get, not on the helper.
"""
import unittest
from unittest import mock

from data_sources import openfda


class _Resp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class OpenFdaApiKeyTest(unittest.TestCase):

    def _capture(self, payload, fn, *args, **kwargs):
        """Call fn with requests.get patched; return the params it sent."""
        seen = {}

        def fake_get(url, params=None, timeout=None, **_):
            seen["url"] = url
            seen["params"] = params or {}
            return _Resp(payload)

        with mock.patch.object(openfda, "get", return_value=None), \
                mock.patch.object(openfda, "cache_set"), \
                mock.patch.object(openfda.requests, "get", fake_get):
            fn(*args, **kwargs)
        return seen

    def test_adverse_events_sends_key(self):
        with mock.patch.dict("os.environ", {"OPENFDA_API_KEY": "K123"}):
            seen = self._capture({"results": []},
                                 openfda.get_adverse_events, "aspirin")
        self.assertEqual(seen["params"].get("api_key"), "K123")

    def test_label_indications_sends_key(self):
        with mock.patch.dict("os.environ", {"OPENFDA_API_KEY": "K123"}):
            seen = self._capture({"results": []},
                                 openfda.get_label_indications, "aspirin")
        self.assertEqual(seen["params"].get("api_key"), "K123")

    def test_label_mechanism_sends_key(self):
        with mock.patch.dict("os.environ", {"OPENFDA_API_KEY": "K123"}):
            seen = self._capture({"results": []},
                                 openfda.get_label_mechanism, "aspirin")
        self.assertEqual(seen["params"].get("api_key"), "K123")

    def test_label_evidence_sends_key(self):
        with mock.patch.dict("os.environ", {"OPENFDA_API_KEY": "K123"}):
            seen = self._capture({"results": []},
                                 openfda.get_label_evidence, "aspirin")
        self.assertEqual(seen["params"].get("api_key"), "K123")

    def test_no_key_configured_sends_no_api_key_param(self):
        env = {k: v for k, v in __import__("os").environ.items()
               if k != "OPENFDA_API_KEY"}
        with mock.patch.dict("os.environ", env, clear=True):
            seen = self._capture({"results": []},
                                 openfda.get_adverse_events, "aspirin")
        self.assertNotIn("api_key", seen["params"])

    def test_search_params_preserved_alongside_key(self):
        with mock.patch.dict("os.environ", {"OPENFDA_API_KEY": "K123"}):
            seen = self._capture({"results": []},
                                 openfda.get_adverse_events, "aspirin")
        self.assertIn("search", seen["params"])
        self.assertIn("aspirin", seen["params"]["search"])


class OpenFdaThrottleTest(unittest.TestCase):
    """An API key raises the daily quota but not the per-minute ceiling."""

    def test_requests_are_spaced(self):
        stamps = []

        def fake_get(url, params=None, timeout=None, **_):
            stamps.append(__import__("time").monotonic())
            return _Resp({"results": []})

        with mock.patch.object(openfda, "_MIN_REQUEST_INTERVAL", 0.05), \
                mock.patch.object(openfda, "_last_request_at", 0.0), \
                mock.patch.object(openfda.requests, "get", fake_get):
            for _ in range(4):
                openfda._request(openfda.BASE_URL, {})
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(all(g >= 0.045 for g in gaps), gaps)

    def test_429_is_retried_then_surfaced(self):
        calls = {"n": 0}

        def fake_get(url, params=None, timeout=None, **_):
            calls["n"] += 1
            r = _Resp({})
            r.status_code = 429
            r.headers = {"Retry-After": "0"}
            return r

        with mock.patch.object(openfda, "_MIN_REQUEST_INTERVAL", 0.0), \
                mock.patch.object(openfda.requests, "get", fake_get):
            resp = openfda._request(openfda.BASE_URL, {})
        self.assertEqual(calls["n"], openfda._RATE_LIMIT_ATTEMPTS)
        # Still a 429: callers must see an error, never a silent empty result.
        self.assertEqual(resp.status_code, 429)

    def test_429_then_success_returns_success(self):
        seq = [429, 200]

        def fake_get(url, params=None, timeout=None, **_):
            r = _Resp({"results": []})
            r.status_code = seq.pop(0)
            r.headers = {"Retry-After": "0"}
            return r

        with mock.patch.object(openfda, "_MIN_REQUEST_INTERVAL", 0.0), \
                mock.patch.object(openfda.requests, "get", fake_get):
            resp = openfda._request(openfda.BASE_URL, {})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seq, [])

    def test_retry_delay_honours_retry_after(self):
        r = _Resp({})
        r.headers = {"Retry-After": "7"}
        self.assertEqual(openfda._retry_delay(r, 0), 7.0)

    def test_retry_delay_falls_back_to_backoff(self):
        r = _Resp({})
        r.headers = {}
        d0 = openfda._retry_delay(r, 0)
        d2 = openfda._retry_delay(r, 2)
        self.assertGreater(d2, d0)

    def test_deadline_path_does_not_retry(self):
        calls = {"n": 0}

        def fake_get(url, params=None, timeout=None, **_):
            calls["n"] += 1
            r = _Resp({})
            r.status_code = 429
            r.headers = {}
            return r

        with mock.patch.object(openfda, "_MIN_REQUEST_INTERVAL", 0.0), \
                mock.patch.object(openfda.requests, "get", fake_get):
            openfda._request(openfda.LABEL_URL, {}, timeout=5, attempts=1)
        self.assertEqual(calls["n"], 1)

    def test_errors_are_counted_for_the_gate(self):
        before = openfda.call_stats()["errors"]

        def fake_get(url, params=None, timeout=None, **_):
            raise openfda.requests.RequestException("boom")

        with mock.patch.object(openfda, "get", return_value=None), \
                mock.patch.object(openfda, "cache_set"), \
                mock.patch.object(openfda.requests, "get", fake_get):
            openfda.get_adverse_events("aspirin")
        self.assertGreater(openfda.call_stats()["errors"], before)


if __name__ == "__main__":
    unittest.main()
