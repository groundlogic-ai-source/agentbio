"""
Tests for the indication-level phase_threshold feature op.

The repoDB 'phase' column (highest trial phase of an unapproved drug–
indication pair) existed in the labeled dataset from the start but had no
DSL op — which made the phase-mix confound on the oncology finding
untestable. The op exists for confound adjustment and is flagged
label-confounded so it is never used as a hypothesis predictor.
"""
from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_prep"),
)
import features as F  # noqa: E402


def _df(phases):
    return pd.DataFrame({"phase": phases})


class PhaseThresholdComputeTest(unittest.TestCase):
    PHASES = ["Phase 3", "Phase 1/Phase 2", "Phase 2/Phase 3", "Phase 0",
              None, "Phase 1"]

    def test_threshold_k2(self):
        out = F.compute(_df(self.PHASES), {"op": "phase_threshold", "params": {"k": 2}})
        self.assertEqual(list(out), [1, 1, 1, 0, 0, 0])

    def test_threshold_k3(self):
        out = F.compute(_df(self.PHASES), {"op": "phase_threshold", "params": {"k": 3}})
        self.assertEqual(list(out), [1, 0, 1, 0, 0, 0])

    def test_combined_phase_uses_highest_reached(self):
        out = F.compute(_df(["Phase 1/Phase 2", "Phase 2/Phase 3"]),
                        {"op": "phase_threshold", "params": {"k": 3}})
        self.assertEqual(list(out), [0, 1])

    def test_approved_nan_codes_zero(self):
        # Approved pairs carry no trial phase — they must NOT read as
        # "reached late stage".
        out = F.compute(_df([None]), {"op": "phase_threshold", "params": {"k": 1}})
        self.assertEqual(list(out), [0])

    def test_unparseable_codes_zero(self):
        out = F.compute(_df(["Terminated", ""]), {"op": "phase_threshold", "params": {"k": 1}})
        self.assertEqual(list(out), [0, 0])


class PhaseThresholdPipelineTest(unittest.TestCase):
    SPEC = {"op": "phase_threshold", "params": {"k": 2}}

    def test_supported(self):
        self.assertTrue(F.is_supported(self.SPEC))

    def test_binary_op_allows_moderator_slot(self):
        self.assertIn("phase_threshold", F._BINARY_OPS)

    def test_label_confounded_never_a_hypothesis_predictor(self):
        self.assertTrue(F.is_confounded(self.SPEC))

    def test_predictor_kind_is_binary(self):
        self.assertEqual(F.predictor_kind(self.SPEC), "binary")

    def test_real_dataset_column_present(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data_prep", "output", "labeled_dataset.csv",
        )
        if not os.path.exists(path):
            self.skipTest("labeled_dataset.csv not built")
        df = pd.read_csv(path, low_memory=False)
        out = F.compute(df, self.SPEC)
        self.assertEqual(len(out), len(df))
        self.assertGreater(int(out.sum()), 0)  # late-stage attempts exist


if __name__ == "__main__":
    unittest.main()
