"""The activity fetch read the first 1000 rows and stopped.

Post-benchmark correction of 2026-09-29. Both ChEMBL activity fetches sent
``limit=1000, offset=0`` and never paged. ChEMBL does not order /activity by
potency, so for any well-studied target the rows kept were an arbitrary slice
and the strongest compounds could be absent entirely -- silently, with nothing
reporting a gap.

Measured coverage under the old behaviour, against live ChEMBL on 2026-09-29:

    UGCG      165 /   165   100%
    THRB      850 /   850   100%
    CYP3A4   1000 /  6880    14.5%
    DRD2     1000 / 14702     6.8%
    EGFR     1000 / 20384     4.9%

DRD2 was a live target in a Niemann-Pick type C run. After the fix, a CYP3A4
fetch retrieves all 6880 records.

The helper keeps the two-tuple signatures of ``_fetch_activities`` and
``_fetch_activities_full`` because frozen validation harnesses
(validation/miss_classifier.py, validation/run_audit_traps.py) unpack them
positionally; truncation is reported through ``activity_fetch_coverage``.
"""

import inspect
import unittest
from unittest import mock

from data_sources import chembl


def _page(n: int, total: int, start_id: int = 0) -> dict:
    return {
        "activities": [
            {"activity_id": start_id + i,
             "assay_chembl_id": f"CHEMBL_A{start_id + i}",
             "molecule_chembl_id": f"CHEMBL_M{start_id + i}",
             "pchembl_value": "7.0"}
            for i in range(n)
        ],
        "page_meta": {"total_count": total},
    }


class PaginationTest(unittest.TestCase):

    def test_a_single_short_page_stops_immediately(self):
        with mock.patch.object(chembl, "_get_json",
                               return_value=_page(165, 165)) as g:
            rows = chembl._fetch_activity_pages("u", {}, "CHEMBL_UGCG")
        self.assertEqual(len(rows), 165)
        self.assertEqual(g.call_count, 1, "a short page means the end")

    def test_multiple_pages_are_all_retrieved(self):
        """The CYP3A4 shape: 6880 records across seven pages."""
        pages = [_page(1000, 6880, i * 1000) for i in range(6)]
        pages.append(_page(880, 6880, 6000))
        with mock.patch.object(chembl, "_get_json", side_effect=pages):
            rows = chembl._fetch_activity_pages("u", {}, "CHEMBL340")
        self.assertEqual(len(rows), 6880)
        coverage = chembl.activity_fetch_coverage("CHEMBL340")
        self.assertEqual(coverage["retrieved"], 6880)
        self.assertFalse(coverage["truncated"])
        self.assertTrue(coverage["complete"])

    def test_offset_advances_by_a_full_page(self):
        pages = [_page(1000, 1500), _page(500, 1500, 1000)]
        with mock.patch.object(chembl, "_get_json", side_effect=pages) as g:
            chembl._fetch_activity_pages("u", {"x": 1}, "CHEMBL_T")
        offsets = [c.args[1]["offset"] for c in g.call_args_list]
        self.assertEqual(offsets, [0, 1000])

    def test_caller_params_are_not_mutated(self):
        """The loop must not leak limit/offset back into the caller's dict."""
        params = {"target_chembl_id": "CHEMBL_T"}
        with mock.patch.object(chembl, "_get_json", return_value=_page(5, 5)):
            chembl._fetch_activity_pages("u", params, "CHEMBL_T")
        self.assertEqual(params, {"target_chembl_id": "CHEMBL_T"})

    def test_an_exactly_full_final_page_terminates(self):
        """A page of exactly limit size followed by an empty page must stop."""
        pages = [_page(1000, 1000), _page(0, 1000, 1000)]
        with mock.patch.object(chembl, "_get_json", side_effect=pages) as g:
            rows = chembl._fetch_activity_pages("u", {}, "CHEMBL_T2")
        self.assertEqual(len(rows), 1000)
        self.assertEqual(g.call_count, 2)


class TruncationIsReportedTest(unittest.TestCase):
    """A partial pool is a coverage gap and must never be silent."""

    def test_hitting_the_ceiling_is_recorded(self):
        with mock.patch.object(chembl, "_MAX_ACTIVITY_RECORDS", 2000), \
             mock.patch.object(chembl, "_get_json",
                               side_effect=[_page(1000, 99999, 0),
                                            _page(1000, 99999, 1000)]):
            rows = chembl._fetch_activity_pages("u", {}, "CHEMBL_BIG")
        coverage = chembl.activity_fetch_coverage("CHEMBL_BIG")
        self.assertEqual(len(rows), 2000)
        self.assertTrue(coverage["truncated"])
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["upstream_total"], 99999)

    def test_coverage_is_empty_for_a_target_never_fetched(self):
        self.assertEqual(chembl.activity_fetch_coverage("CHEMBL_NEVER"), {})

    def test_upstream_total_is_captured_from_the_first_page(self):
        with mock.patch.object(chembl, "_get_json", return_value=_page(10, 10)):
            chembl._fetch_activity_pages("u", {}, "CHEMBL_SMALL")
        self.assertEqual(
            chembl.activity_fetch_coverage("CHEMBL_SMALL")["upstream_total"], 10)


class CacheInvalidationTest(unittest.TestCase):
    """More data behind the same key would replay the truncated pool.

    This repo has shipped a fix inert twice by changing a query without moving
    its cache key. Pagination changes what the cached value contains, so all
    three keys built on the activity ledger had to move.
    """

    def test_activity_cache_key_is_versioned_for_pagination(self):
        self.assertTrue(
            chembl.ACTIVITIES_FULL_CACHE_KEY.endswith("_paged"),
            "the activity ledger key must record that it is paginated")
        source = inspect.getsource(chembl)
        self.assertNotIn('make_key("_fetch_activities_full_v3_ec50"', source)

    def test_downstream_keys_moved_too(self):
        """The three keys exist, differ, and are read from the constants.

        This asserted the literal string "get_target_candidate_compounds_v7_paged".
        The key was later bumped to v8 when route flags joined the cached
        payload -- a correct change, exactly what this test exists to
        encourage -- and the test failed for doing its job properly. Worse,
        it failed for retyping a version string, which CLAUDE.md names as the
        specific mistake that has twice shipped a fix inert.

        A test of cache-key discipline must not itself hardcode the version.
        What matters is that all three keys are distinct constants that
        downstream code references, not what number they currently carry.
        """
        keys = [chembl.ACTIVITIES_FULL_CACHE_KEY,
                chembl.BIOACTIVITY_COUNT_CACHE_KEY,
                chembl.CANDIDATE_POOL_CACHE_KEY]
        self.assertEqual(len(set(keys)), 3,
                         "the three activity-derived keys must be distinct")
        for key in keys:
            self.assertTrue(key.strip(), "a cache key must not be empty")
        source = inspect.getsource(chembl)
        for name in ("ACTIVITIES_FULL_CACHE_KEY", "BIOACTIVITY_COUNT_CACHE_KEY",
                     "CANDIDATE_POOL_CACHE_KEY"):
            self.assertGreaterEqual(
                source.count(name), 2,
                f"{name} is declared but never referenced; a retyped literal "
                "somewhere is how these drift apart")

    def test_no_fetch_path_still_hardcodes_a_single_page(self):
        import inspect
        source = inspect.getsource(chembl)
        self.assertNotIn('"limit": 1000,\n        "offset": 0,', source)


if __name__ == "__main__":
    unittest.main()
