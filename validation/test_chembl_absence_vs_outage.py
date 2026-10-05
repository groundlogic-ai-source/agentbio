"""A protein ChEMBL does not track is an observation, not an outage.

Regression cover for the post-benchmark correction of 2026-09-27.

An empty ChEMBL result was treated as `unavailable` everywhere, because a
genuine absence and a degraded HTTP 200 look identical in isolation - the
MTOR/TSC incident of 2026-07. But a failed target fails the whole run, and
ChEMBL does not track most proteins, so any disease whose target list included
one was unrunnable. An Acrodysostosis case died this way with its real target,
PDE4D, fully resolved and carrying 9,541 activities, while FAM20A and DEPDC1B
(0 ChEMBL target records each) failed the run around it.

Probing a control accession known to be dense separates the two cases.
"""

import unittest
from unittest import mock

from data_sources import chembl


class LivenessProbeTest(unittest.TestCase):

    def setUp(self):
        chembl._liveness_cached = None
        chembl._liveness_until = 0.0
        self.addCleanup(self._reset)

    def _reset(self):
        chembl._liveness_cached = None
        chembl._liveness_until = 0.0

    def test_control_with_targets_means_live(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=["CHEMBL203"]):
            self.assertTrue(chembl.chembl_is_serving_data())

    def test_empty_control_means_degraded(self):
        """The MTOR/TSC shape: ChEMBL serving empty 200s."""
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=[]):
            self.assertFalse(chembl.chembl_is_serving_data())

    def test_raising_control_means_degraded(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               side_effect=RuntimeError("boom")):
            self.assertFalse(chembl.chembl_is_serving_data())

    def test_probe_is_cached_within_its_ttl(self):
        calls = []

        def _probe(acc):
            calls.append(acc)
            return ["CHEMBL203"]

        with mock.patch.object(chembl, "_resolve_target_chembl_id", _probe):
            chembl.chembl_is_serving_data()
            chembl.chembl_is_serving_data()
            chembl.chembl_is_serving_data()
        self.assertEqual(len(calls), 1, "liveness probed more than once")

    def test_probe_uses_the_documented_control_accession(self):
        """CLAUDE.md designates EGFR P00533 as the data-rich validation entity."""
        self.assertEqual(chembl._LIVENESS_ACCESSION, "P00533")


class AbsenceIsNotUnavailableTest(unittest.TestCase):
    """The statuses that decide whether a target -- and so a run -- survives.

    `main_graph.chembl_target_failed` treats anything outside
    {ok, empty, healthy, available, success, complete, disabled} as a failed
    target, so "empty" survives and "unavailable" does not.
    """

    def test_empty_is_a_healthy_state_but_unavailable_is_not(self):
        from main_graph import HEALTHY_SOURCE_STATES as healthy
        self.assertIn("empty", healthy)
        self.assertNotIn("unavailable", healthy)

    def test_absent_target_reports_empty_when_chembl_is_live(self):
        """FAM20A/DEPDC1B: zero ChEMBL target records, ChEMBL healthy."""
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=[]), \
             mock.patch.object(chembl, "chembl_is_serving_data",
                               return_value=True), \
             mock.patch.object(chembl, "get", return_value=None), \
             mock.patch.object(chembl, "cache_set"):
            out = chembl.get_target_candidate_compounds("Q96MK3")
        self.assertEqual(out["source_status"], "empty")
        self.assertIsNone(out["source_error"])
        self.assertTrue(out["target_absent_from_chembl"])

    def test_absent_target_reports_unavailable_when_chembl_is_degraded(self):
        """Fail-closed still holds when the control is empty too."""
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=[]), \
             mock.patch.object(chembl, "chembl_is_serving_data",
                               return_value=False), \
             mock.patch.object(chembl, "get", return_value=None), \
             mock.patch.object(chembl, "cache_set"):
            out = chembl.get_target_candidate_compounds("Q96MK3")
        self.assertEqual(out["source_status"], "unavailable")
        self.assertIn("liveness control", out["source_error"])


if __name__ == "__main__":
    unittest.main()
