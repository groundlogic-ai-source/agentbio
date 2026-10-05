"""A failed direction check is retried, never remembered.

Regression cover for the post-benchmark correction of 2026-09-26. Failures were
cached for a day, so an API outage memoized INSUFFICIENT_INFO and every later
run that day read the failure back without calling the model - losing the
qualified directional bonus for reasons unrelated to the biology.
"""

import unittest
from unittest import mock

from data_sources import mechanism_direction as md


class DirectionFailureCachingTest(unittest.TestCase):

    def setUp(self):
        self.written: list[tuple] = []
        patcher = mock.patch.object(
            md, "cache_set",
            side_effect=lambda key, value, **kw: self.written.append(
                (key, value, kw)))
        patcher.start()
        self.addCleanup(patcher.stop)
        get_patcher = mock.patch.object(md, "get", return_value=None)
        get_patcher.start()
        self.addCleanup(get_patcher.stop)

    def _check(self):
        return md.check_mechanism_direction(
            "CAPIVASERTIB", "AKT2", "INHIBITOR", "AKT inhibitor",
            "Hypoinsulinemic hypoglycemia and body hemihypertrophy")

    def test_unconfigured_client_is_not_cached(self):
        with mock.patch.object(md, "_openai_client", return_value=None):
            result = self._check()
        self.assertEqual(result["verdict"], md.VERDICT_INSUFFICIENT)
        self.assertEqual(
            self.written, [], "a skipped check must not be memoized")

    def test_api_exception_is_not_cached(self):
        client = mock.MagicMock()
        client.responses.create.side_effect = RuntimeError(
            "Error code: 400 - credit balance is too low")
        with mock.patch.object(md, "_openai_client", return_value=client):
            result = self._check()
        self.assertEqual(result["verdict"], md.VERDICT_INSUFFICIENT)
        self.assertIn("credit balance", result["reason"])
        self.assertEqual(
            self.written, [], "an outage must not be memoized")

    def test_a_failure_leaves_no_entry_for_the_next_run_to_read(self):
        """The exact shape of the incident: outage, then a same-day re-run."""
        client = mock.MagicMock()
        client.responses.create.side_effect = RuntimeError("503 upstream")
        with mock.patch.object(md, "_openai_client", return_value=client):
            self._check()
        cached_keys = [key for key, _value, _kw in self.written]
        self.assertEqual(cached_keys, [])

    def test_successful_verdicts_are_still_cached_for_thirty_days(self):
        """The fix must not disable caching of real answers."""
        import inspect
        source = inspect.getsource(md.check_mechanism_direction)
        self.assertIn("ttl_days=30", source)
        self.assertNotIn(
            "ttl_days=1", source,
            "a failure path is still memoizing at a short TTL")


if __name__ == "__main__":
    unittest.main()
