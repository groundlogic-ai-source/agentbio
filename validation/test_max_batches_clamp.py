"""Tests for the continuous-discovery batch cap (clamp_max_batches).

Continuous mode chains LLM-heavy batches, so the cap is a spend bound. The
previous fixed ceiling (40 batches / 6h) allowed worst-case runs on the order
of $100; the user-facing default must be modest, and any requested value must
be clamped into [1, HARD_MAX_BATCHES]. The cap must never be a search
parameter — it only bounds when a run gives up.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data_prep"))

import run_discovery as RD  # noqa: E402


class ClampMaxBatchesTest(unittest.TestCase):

    def test_none_uses_modest_default(self):
        self.assertEqual(RD.clamp_max_batches(None), RD.DEFAULT_MAX_BATCHES)
        # The default must be meaningfully below the hard ceiling.
        self.assertLess(RD.DEFAULT_MAX_BATCHES, RD.HARD_MAX_BATCHES)

    def test_unparseable_uses_default(self):
        self.assertEqual(RD.clamp_max_batches("abc"), RD.DEFAULT_MAX_BATCHES)
        self.assertEqual(RD.clamp_max_batches(""), RD.DEFAULT_MAX_BATCHES)

    def test_reasonable_value_passes_through(self):
        self.assertEqual(RD.clamp_max_batches(5), 5)
        self.assertEqual(RD.clamp_max_batches(1), 1)
        self.assertEqual(RD.clamp_max_batches(RD.HARD_MAX_BATCHES),
                         RD.HARD_MAX_BATCHES)

    def test_clamped_to_hard_ceiling(self):
        self.assertEqual(RD.clamp_max_batches(10_000), RD.HARD_MAX_BATCHES)

    def test_floor_is_one(self):
        self.assertEqual(RD.clamp_max_batches(0), 1)
        self.assertEqual(RD.clamp_max_batches(-3), 1)

    def test_numeric_string_accepted(self):
        # JSON number fields arrive as int, but tolerate strings defensively.
        self.assertEqual(RD.clamp_max_batches("7"), 7)

    def test_non_finite_floats_fall_back_to_default(self):
        # int(inf)/int(nan) raise OverflowError — must not crash the job thread.
        self.assertEqual(RD.clamp_max_batches(float("inf")), RD.DEFAULT_MAX_BATCHES)
        self.assertEqual(RD.clamp_max_batches(float("nan")), RD.DEFAULT_MAX_BATCHES)

    def test_booleans_rejected_to_default(self):
        # bool is an int subclass; True/False are not batch counts.
        self.assertEqual(RD.clamp_max_batches(True), RD.DEFAULT_MAX_BATCHES)
        self.assertEqual(RD.clamp_max_batches(False), RD.DEFAULT_MAX_BATCHES)


if __name__ == "__main__":
    unittest.main()
