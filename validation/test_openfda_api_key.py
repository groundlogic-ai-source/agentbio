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


if __name__ == "__main__":
    unittest.main()
