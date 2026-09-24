"""GtoPdb must go through the shared provider policy, not raw requests.

A reviewer pass over a large candidate pool fans out to GtoPdb once per
(candidate, target). Unthrottled that draws sustained HTTP 429s, and because
those land in candidate_source_coverage.failures they block report persistence
for the entire run — observed live on 2026-09-24, where a 210-candidate
NR3C2 pool left 94 candidates with incomplete coverage and no dossier.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources import gtopdb  # noqa: E402
from data_sources import provider_request_policy as policy  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise gtopdb.requests.HTTPError(f"HTTP {self.status_code}")


class GtoPdbThrottleTests(unittest.TestCase):
    def setUp(self):
        policy._reset_for_tests()

    def tearDown(self):
        policy._reset_for_tests()

    def test_gtopdb_has_a_provider_policy(self):
        self.assertIn("gtopdb", policy.POLICIES)
        p = policy.POLICIES["gtopdb"]
        self.assertGreaterEqual(p.min_interval_seconds, 0.25)
        self.assertEqual(p.concurrency, 1)

    def test_request_is_routed_through_the_policy_layer(self):
        with mock.patch.object(
            gtopdb, "provider_request",
            return_value=_Resp(200, {"ok": True}),
        ) as routed:
            result = gtopdb._get_json("/ligands/7434/structure")
        self.assertEqual(result, {"ok": True})
        routed.assert_called_once()
        self.assertEqual(routed.call_args.args[0], "gtopdb")

    def test_transient_429_is_retried_before_surfacing(self):
        # The shared policy retries transient statuses; a 429 followed by a
        # success must resolve rather than becoming a coverage failure.
        calls = []

        def fake_get(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                return _Resp(429, headers={"Retry-After": "0"})
            return _Resp(200, {"recovered": True})

        with mock.patch.object(gtopdb.requests, "get", side_effect=fake_get):
            result = gtopdb._get_json("/ligands/7434/structure")
        self.assertEqual(result, {"recovered": True})
        self.assertEqual(len(calls), 2)

    def test_exhausted_retries_surface_as_source_unavailable(self):
        with mock.patch.object(
            gtopdb.requests, "get",
            return_value=_Resp(429, headers={"Retry-After": "0"}),
        ):
            with self.assertRaises(gtopdb._SourceUnavailable):
                gtopdb._get_json("/ligands/7434/structure")

    def test_open_circuit_is_reported_as_unavailable_not_crash(self):
        with mock.patch.object(
            gtopdb, "provider_request",
            side_effect=policy.ProviderCircuitOpen("gtopdb circuit open"),
        ):
            with self.assertRaises(gtopdb._SourceUnavailable):
                gtopdb._get_json("/ligands/7434/structure")


if __name__ == "__main__":
    unittest.main()
