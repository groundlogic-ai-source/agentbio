"""Agonist potency is measured as EC50, and the pipeline must be able to see it.

Post-benchmark correction of 2026-09-28. The activity fetch filtered
`standard_type__in="IC50,Ki"`. IC50 and Ki measure inhibition and binding;
EC50 is the standard potency measure for an AGONIST. So every disease whose
therapy must ACTIVATE its target had its best candidates scored as carrying no
assay support at all -- a systematic blind spot, not a rounding error.

Resistance to thyroid hormone is the worked example. The therapeutic logic is
agonism potent enough to overcome a dominant-negative block. Resmetirom carries
7 EC50 records against THRB (human, assay confidence 9), and the dossier still
read "No qualified ChEMBL human bioactivity ledger row matched this target",
because none of them were IC50 or Ki. At THRB the filter read 659 records and
ignored 191.
"""

import unittest

from data_sources import chembl


class PotencyStandardTypesTest(unittest.TestCase):

    def test_ec50_is_accepted(self):
        self.assertIn("EC50", chembl._POTENCY_STANDARD_TYPES)

    def test_inhibition_and_binding_measures_are_retained(self):
        for measure in ("IC50", "Ki"):
            self.assertIn(measure, chembl._POTENCY_STANDARD_TYPES)

    def test_filter_is_a_single_named_constant(self):
        """Two fetch paths existed; both must use the same definition."""
        import inspect
        source = inspect.getsource(chembl)
        self.assertEqual(
            source.count('"standard_type__in": _POTENCY_STANDARD_TYPES'), 2,
            "both activity fetch paths must share the potency-type constant")
        self.assertNotIn(
            '"standard_type__in": "IC50,Ki"', source,
            "a hardcoded inhibition-only filter has come back")

    def test_types_are_comma_separated_for_the_chembl_in_filter(self):
        parts = chembl._POTENCY_STANDARD_TYPES.split(",")
        self.assertEqual(parts, [p.strip() for p in parts],
                         "ChEMBL __in filters must not contain spaces")
        self.assertGreaterEqual(len(parts), 3)


class CacheInvalidationTest(unittest.TestCase):
    """Changing the filter must invalidate the caches that stored its results.

    The first attempt at this fix changed the query and not the cache keys.
    `_fetch_activities_full_v2` holds a 7-day TTL, so the re-run read back the
    IC50/Ki-only activity list and produced a byte-identical dossier -- same
    "no qualified row", same composite 0.9165. The filter is part of what the
    cached value means, so the key has to move with it.
    """

    def test_activity_cache_key_is_versioned_for_this_change(self):
        import inspect
        source = inspect.getsource(chembl)
        self.assertNotIn('make_key("_fetch_activities_full_v2"', source)
        self.assertIn("_fetch_activities_full_v3_ec50", source)

    def test_bioactivity_count_cache_key_is_versioned(self):
        import inspect
        source = inspect.getsource(chembl)
        self.assertIn("get_target_bioactivity_count_v2_ec50", source)

    def test_candidate_pool_cache_key_is_versioned(self):
        import inspect
        source = inspect.getsource(chembl)
        self.assertNotIn('make_key("get_target_candidate_compounds_v4"', source)
        self.assertIn("get_target_candidate_compounds_v5_ec50", source)


class AgonistDiseaseRegressionTest(unittest.TestCase):
    """The class of disease this blind spot silently disqualified.

    A target whose therapy is activation rather than inhibition will have its
    potency recorded as EC50. If the fetch cannot see EC50, the candidate falls
    into the mechanism-only lane and the dossier claims no direct assay support
    while the source holds maximum-confidence human measurements.
    """

    def test_an_agonist_only_pool_is_no_longer_invisible(self):
        types = set(chembl._POTENCY_STANDARD_TYPES.split(","))
        self.assertTrue(
            types & {"EC50"},
            "a pool containing only agonist potency data must be visible")


if __name__ == "__main__":
    unittest.main()
