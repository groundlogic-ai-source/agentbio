"""Regression tests for the cache failure-caching sweep (2026-07-31).

Covers the two bug classes from validation/cache_failure_sweep.md:
  - empty upstream payloads (ambiguous: genuine empty vs degraded 200) must
    NOT be cached;
  - genuine post-filter empties (raw payload seen, filters reduced to zero)
    MUST remain cacheable.

Run: python3 -m unittest validation.test_cache_failures
"""
import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

from cache.cache import make_key, _get_conn  # noqa: E402
from data_sources import chembl  # noqa: E402


def _has(key: str) -> bool:
    return _get_conn().execute(
        "SELECT 1 FROM cache WHERE key=?", (key,)).fetchone() is not None


def _purge(*keys: str) -> None:
    conn = _get_conn()
    for k in keys:
        conn.execute("DELETE FROM cache WHERE key=?", (k,))
    conn.commit()


class TestFetchActivitiesFullRawSeen(unittest.TestCase):
    TID = "CHEMBL_FAKE_SWEEP_A"

    def setUp(self):
        self.key = make_key("_fetch_activities_full_v2", self.TID)
        _purge(self.key)

    def tearDown(self):
        _purge(self.key)

    def test_empty_payload_not_cached(self):
        """Degraded/ambiguous empty payload → ([], False), no cache row."""
        with mock.patch.object(chembl, "_get_json", return_value={"activities": []}):
            kept, raw_seen = chembl._fetch_activities_full(self.TID)
        self.assertEqual(kept, [])
        self.assertFalse(raw_seen)
        self.assertFalse(_has(self.key), "empty payload must not be cached")

    def test_postfilter_empty_is_cached(self):
        """Raw rows exist but all below confidence → ([], True), cacheable."""
        payload = {"activities": [
            {"activity_id": 1, "assay_chembl_id": "ASSAY_X",
             "molecule_chembl_id": "CHEMBL_M1", "canonical_smiles": "CC",
             "pchembl_value": "5.0", "standard_type": "IC50"},
        ]}
        with mock.patch.object(chembl, "_get_json", return_value=payload), \
             mock.patch.object(chembl, "_fetch_assay_confidence",
                               return_value={"ASSAY_X": 5}):
            kept, raw_seen = chembl._fetch_activities_full(self.TID)
        self.assertEqual(kept, [])
        self.assertTrue(raw_seen)
        self.assertTrue(_has(self.key),
                        "genuine post-filter empty must remain cacheable")

    def test_kept_rows_returned(self):
        payload = {"activities": [
            {"activity_id": 2, "assay_chembl_id": "ASSAY_Y",
             "molecule_chembl_id": "CHEMBL_M2", "canonical_smiles": "CC",
             "pchembl_value": "8.5", "standard_type": "Ki"},
        ]}
        with mock.patch.object(chembl, "_get_json", return_value=payload), \
             mock.patch.object(chembl, "_fetch_assay_confidence",
                               return_value={"ASSAY_Y": 9}):
            kept, raw_seen = chembl._fetch_activities_full(self.TID)
        self.assertEqual(len(kept), 1)
        self.assertTrue(raw_seen)


class TestPoolAndCountCacheGates(unittest.TestCase):
    UID = "U_FAKE_SWEEP_B"

    def setUp(self):
        self.pool_key = make_key("get_target_candidate_compounds_v2",
                                 self.UID, 25, True)
        self.count_key = make_key("get_target_bioactivity_count", self.UID)
        _purge(self.pool_key, self.count_key)

    def tearDown(self):
        _purge(self.pool_key, self.count_key)

    def test_pool_empty_not_cached_when_no_raw_payload(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=["CHEMBL_T1"]), \
             mock.patch.object(chembl, "_fetch_activities_full",
                               return_value=([], False)):
            res = chembl.get_target_candidate_compounds(self.UID,
                                                        repurposing_only=True)
        self.assertEqual(res["compounds"], [])
        self.assertFalse(_has(self.pool_key))

    def test_pool_empty_cached_when_raw_seen(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=["CHEMBL_T1"]), \
             mock.patch.object(chembl, "_fetch_activities_full",
                               return_value=([], True)):
            res = chembl.get_target_candidate_compounds(self.UID,
                                                        repurposing_only=True)
        self.assertEqual(res["compounds"], [])
        self.assertTrue(_has(self.pool_key))

    def test_count_zero_not_cached_when_no_raw_payload(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=["CHEMBL_T1"]), \
             mock.patch.object(chembl, "_fetch_activities",
                               return_value=([], False)):
            res = chembl.get_target_bioactivity_count(self.UID)
        self.assertEqual(res["count"], 0)
        self.assertFalse(_has(self.count_key))

    def test_count_zero_cached_when_raw_seen(self):
        with mock.patch.object(chembl, "_resolve_target_chembl_id",
                               return_value=["CHEMBL_T1"]), \
             mock.patch.object(chembl, "_fetch_activities",
                               return_value=([], True)):
            res = chembl.get_target_bioactivity_count(self.UID)
        self.assertEqual(res["count"], 0)
        self.assertTrue(_has(self.count_key))


if __name__ == "__main__":
    unittest.main()
