"""A definitive "no such record" is an answer, not an outage.

Regression cover for the post-benchmark correction of 2026-09-27. Routing
GtoPdb through the shared provider throttle (915001f) put
`response.raise_for_status()` in front of its own 404 handling, so
`if resp.status_code == 404: return None` became unreachable. Five accessions
GtoPdb simply does not carry were reported as source unavailability, and an
Acrodysostosis run failed all five targets before any scoring ran.
"""

import unittest
from unittest import mock

import requests

from data_sources import provider_request_policy as policy


def _response(status: int):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.headers = {}
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Client Error")
    else:
        resp.raise_for_status.return_value = None
    return resp


class PassThroughStatusTest(unittest.TestCase):

    def setUp(self):
        policy._circuit_open_until.clear()
        policy._consecutive_failures.clear()
        policy._next_request_at.clear()

    def test_404_is_returned_when_passed_through(self):
        resp = _response(404)
        out = policy.request(
            "gtopdb", lambda url, **kw: resp, "https://example.test/x",
            pass_through_statuses=frozenset({404, 204}))
        self.assertIs(out, resp)
        self.assertEqual(out.status_code, 404)

    def test_204_is_returned_when_passed_through(self):
        resp = _response(204)
        out = policy.request(
            "gtopdb", lambda url, **kw: resp, "https://example.test/x",
            pass_through_statuses=frozenset({404, 204}))
        self.assertEqual(out.status_code, 204)

    def test_404_still_raises_when_not_passed_through(self):
        """Default behaviour is unchanged for every other caller."""
        resp = _response(404)
        with self.assertRaises(requests.HTTPError):
            policy.request(
                "chembl", lambda url, **kw: resp, "https://example.test/x")

    def test_a_passed_through_404_does_not_open_the_circuit(self):
        """An absent record must not count as a provider failure."""
        for _ in range(5):
            policy.request(
                "gtopdb", lambda url, **kw: _response(404),
                "https://example.test/x",
                pass_through_statuses=frozenset({404, 204}))
        self.assertEqual(policy._consecutive_failures["gtopdb"], 0)

    def test_a_real_server_error_is_still_transient(self):
        """500s keep their retry-and-circuit behaviour, pass-through or not."""
        resp = _response(500)
        with self.assertRaises(Exception):
            policy.request(
                "gtopdb", lambda url, **kw: resp, "https://example.test/x",
                pass_through_statuses=frozenset({404, 204}))

    def test_200_is_unaffected(self):
        resp = _response(200)
        out = policy.request(
            "gtopdb", lambda url, **kw: resp, "https://example.test/x",
            pass_through_statuses=frozenset({404}))
        self.assertEqual(out.status_code, 200)


if __name__ == "__main__":
    unittest.main()
