"""Regression tests for the benchmark-holdout redaction (leave-one-out mode).

Run with stdlib only:  python3 -m unittest validation.test_holdout -v

Guards the three failure modes the code review identified:
  1. Redacted-to-empty names must NEVER fall through to the drug_indication
     EFO fallback (that path re-discovers the held-out drug).
  2. Holdout fallback/sentinel output must never share a cache key with
     non-holdout empty-names output (cache poisoning).
  3. Transient resolution failures of the held-out drug must fail loud
     (raise), never silently degrade redaction.
Plus salt/ester-form redaction via shared ChEMBL parent ID.
"""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources import chembl, holdout  # noqa: E402


class HoldoutTestBase(unittest.TestCase):
    def setUp(self):
        holdout.deactivate()
        # Isolate from the real sqlite cache: miss on read, capture on write.
        self.cache_writes = {}
        self._p_get = patch.object(chembl, "get", lambda k: None)
        self._p_set = patch.object(
            chembl, "cache_set",
            lambda k, v, ttl_days=7: self.cache_writes.setdefault(k, v))
        self._p_get.start()
        self._p_set.start()
        self.addCleanup(self._p_get.stop)
        self.addCleanup(self._p_set.stop)
        self.addCleanup(holdout.deactivate)

    def _stub_resolution(self, mapping, metas=None, children=None):
        """mapping: drug name -> mol_id (or None). metas: mol_id -> meta dict."""
        metas = metas or {}
        children = children or {}
        self._p_find = patch.object(
            chembl, "_find_molecule_chembl_id",
            lambda name: mapping.get(name.upper()) or mapping.get(name))
        self._p_meta = patch.object(
            chembl, "_fetch_molecule_meta",
            lambda ids, **kw: {i: metas.get(i, {}) for i in ids})
        self._p_find.start()
        self._p_meta.start()
        self.addCleanup(self._p_find.stop)
        self.addCleanup(self._p_meta.stop)

        def fake_get_json(url, params=None):
            if "molecule.json" in url and params and "parent_chembl_id" in params:
                parent = params["parent_chembl_id"]
                return {"molecules": [
                    {"molecule_chembl_id": m} for m in children.get(parent, [])
                ]}
            raise AssertionError(f"unexpected API call in test: {url} {params}")
        self._p_json = patch.object(chembl, "_get_json", fake_get_json)
        self._p_json.start()
        self.addCleanup(self._p_json.stop)


class TestRedactedToEmpty(HoldoutTestBase):
    def test_redacted_to_empty_never_falls_back(self):
        # Holdout resolves fine; the only approved name IS the held-out drug.
        self._stub_resolution(
            {"EVEROLIMUS": "CHEMBL1901"},
            metas={"CHEMBL1901": {"parent_chembl_id": "CHEMBL1901"}},
        )
        holdout.activate(["Everolimus"])
        # _get_json raises AssertionError on any unexpected call — if the
        # drug_indication fallback runs, this test fails.
        out = chembl.get_pharmacological_targets_for_disease(
            "MONDO_0008612", approved_drug_names=["EVEROLIMUS"])
        self.assertEqual(out, [])

    def test_none_names_under_holdout_never_falls_back(self):
        # Call-site coerces redacted-to-empty to None (`or None`) — the
        # sentinel short-circuit must catch None too.
        self._stub_resolution({"EVEROLIMUS": "CHEMBL1901"})
        holdout.activate(["Everolimus"])
        out = chembl.get_pharmacological_targets_for_disease(
            "MONDO_0008612", approved_drug_names=None)
        self.assertEqual(out, [])

    def test_unresolved_holdout_still_blocks_fallback(self):
        # Drug genuinely absent from ChEMBL (returns None): name-level
        # redaction only, recorded unresolved, and the only approved name
        # being the held-out drug must still short-circuit (no fallback).
        self._stub_resolution({"SOMEBIOLOGIC": None})
        holdout.activate(["Somebiologic"])
        out = chembl.get_pharmacological_targets_for_disease(
            "EFO_0000001", approved_drug_names=["SOMEBIOLOGIC"])
        self.assertEqual(out, [])
        self.assertEqual(holdout.unresolved(), ["Somebiologic"])

    def test_cache_key_separation(self):
        self._stub_resolution({"EVEROLIMUS": "CHEMBL1901"})
        holdout.activate(["Everolimus"])
        chembl.get_pharmacological_targets_for_disease(
            "MONDO_0008612", approved_drug_names=None)
        holdout.deactivate()
        sentinel_keys = set(self.cache_writes)
        self.assertTrue(sentinel_keys, "sentinel entry should be cached")
        # A non-holdout empty-names call must use a DIFFERENT key.
        normal_key = chembl.make_key(
            "get_pharmacological_targets_for_disease_v3", "MONDO_0008612", ())
        self.assertNotIn(normal_key, sentinel_keys)


class TestSaltFormRedaction(HoldoutTestBase):
    def test_salt_form_dropped_via_shared_parent(self):
        self._stub_resolution(
            {"SILDENAFIL": "CHEMBL192", "SILDENAFIL CITRATE": "CHEMBL1737",
             "ASPIRIN": "CHEMBL25"},
            metas={
                "CHEMBL192": {"parent_chembl_id": "CHEMBL192"},
                "CHEMBL1737": {"parent_chembl_id": "CHEMBL192"},
                "CHEMBL25": {"parent_chembl_id": "CHEMBL25"},
            },
            children={"CHEMBL192": ["CHEMBL1737"]},
        )
        holdout.activate(["Sildenafil"])
        kept = chembl.redact_holdout_names(["SILDENAFIL CITRATE", "ASPIRIN"])
        self.assertEqual(kept, ["ASPIRIN"])


class TestFailLoud(HoldoutTestBase):
    def test_transient_resolution_error_raises(self):
        def boom(name):
            raise RuntimeError("HTTP 503 from ChEMBL")
        with patch.object(chembl, "_find_molecule_chembl_id", boom), \
             patch.object(chembl.time, "sleep", lambda s: None):
            holdout.activate(["Imatinib"])
            with self.assertRaises(RuntimeError) as ctx:
                chembl.redact_holdout_names(["MEPOLIZUMAB"])
            self.assertIn("under-redacted", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
