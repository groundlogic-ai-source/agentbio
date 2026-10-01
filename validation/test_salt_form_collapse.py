"""A salt is the same drug, and must not compete with its own free base.

Post-benchmark correction of 2026-09-29. ChEMBL records a salt as a separate
molecule with its own InChIKey, because the key is computed over the counterion
too. Connectivity-block matching therefore cannot relate the two forms:

    ELIGLUSTAT          CHEMBL2110588   FJZZPCZKBUKGGU-AUSIDOKSSA-N
    ELIGLUSTAT TARTRATE CHEMBL5723563   KUBARPMUNHKBIQ-VTHUDJRQSA-N

Different keys, different connectivity blocks, same active moiety.
``molecule_hierarchy.parent_chembl_id`` on the salt points at CHEMBL2110588.

Left unmerged, one drug competed with itself in a Niemann-Pick type C run:
eliglustat at composite 0.8315 and eliglustat tartrate at 0.5002, as separate
rows, each with its own independently-retrieved literature-gate verdict -- one
SEARCH_FAILED, the other cleared. Same moiety, two answers. The split also
divides activity evidence that should have been pooled, which drags the median
potency of both rows away from the moiety's real value.
"""

import unittest
from unittest import mock

from data_sources import chembl

PARENT = "CHEMBL2110588"   # ELIGLUSTAT
SALT = "CHEMBL5723563"     # ELIGLUSTAT TARTRATE


def _bucket(mid, values, smiles="CCC"):
    return {
        "molecule_chembl_id": mid,
        "pchembls": [v for v, _ in values],
        "pchembl_pairs": list(values),
        "confidences": [9] * len(values),
        "activity_ids": [a for _, a in values],
        "assay_ids": {f"A{a}" for _, a in values},
        "canonical_smiles": smiles,
    }


class SaltCollapseTest(unittest.TestCase):

    def test_salt_and_parent_merge_into_one_row(self):
        """The exact shape that produced two competing eliglustat rows."""
        by_mol = {
            PARENT: _bucket(PARENT, [(7.8, 1), (7.6, 2)]),
            SALT: _bucket(SALT, [(7.2, 3)]),
        }
        meta = {
            PARENT: {"parent_chembl_id": PARENT, "pref_name": "ELIGLUSTAT"},
            SALT: {"parent_chembl_id": PARENT,
                   "pref_name": "ELIGLUSTAT TARTRATE"},
        }
        merged, _ = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(list(merged), [PARENT])
        self.assertEqual(
            sorted(merged[PARENT]["source_molecule_chembl_ids"]),
            sorted([PARENT, SALT]))

    def test_activity_evidence_is_pooled_not_discarded(self):
        by_mol = {
            PARENT: _bucket(PARENT, [(7.8, 1), (7.6, 2)]),
            SALT: _bucket(SALT, [(7.2, 3)]),
        }
        meta = {PARENT: {"parent_chembl_id": PARENT},
                SALT: {"parent_chembl_id": PARENT}}
        merged, _ = chembl._collapse_salts_to_parent(by_mol, meta)
        row = merged[PARENT]
        self.assertEqual(sorted(row["pchembls"]), [7.2, 7.6, 7.8])
        self.assertEqual(sorted(row["activity_ids"]), [1, 2, 3])
        self.assertEqual(row["assay_ids"], {"A1", "A2", "A3"})

    def test_the_pooled_median_is_the_moiety_median(self):
        """Splitting the evidence moved both rows' medians off the true value."""
        by_mol = {
            PARENT: _bucket(PARENT, [(8.0, 1), (7.8, 2)]),
            SALT: _bucket(SALT, [(6.0, 3)]),
        }
        meta = {PARENT: {"parent_chembl_id": PARENT},
                SALT: {"parent_chembl_id": PARENT}}
        merged, _ = chembl._collapse_salts_to_parent(by_mol, meta)
        median, _anchor = chembl._median_with_anchor(
            merged[PARENT]["pchembl_pairs"])
        self.assertEqual(median, 7.8)

    def test_unrelated_molecules_are_left_alone(self):
        by_mol = {"CHEMBL_A": _bucket("CHEMBL_A", [(7.0, 1)]),
                  "CHEMBL_B": _bucket("CHEMBL_B", [(6.0, 2)])}
        meta = {"CHEMBL_A": {"parent_chembl_id": "CHEMBL_A"},
                "CHEMBL_B": {"parent_chembl_id": "CHEMBL_B"}}
        merged, _ = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(sorted(merged), ["CHEMBL_A", "CHEMBL_B"])

    def test_a_molecule_with_no_meta_keeps_its_own_identity(self):
        """Absent hierarchy must not silently merge distinct compounds."""
        by_mol = {"CHEMBL_A": _bucket("CHEMBL_A", [(7.0, 1)]),
                  "CHEMBL_B": _bucket("CHEMBL_B", [(6.0, 2)])}
        merged, _ = chembl._collapse_salts_to_parent(by_mol, {})
        self.assertEqual(sorted(merged), ["CHEMBL_A", "CHEMBL_B"])

    def test_parent_structure_is_preferred_over_the_salt_structure(self):
        by_mol = {
            SALT: _bucket(SALT, [(7.2, 3)], smiles="SALT_SMILES"),
            PARENT: _bucket(PARENT, [(7.8, 1)], smiles="PARENT_SMILES"),
        }
        meta = {PARENT: {"parent_chembl_id": PARENT},
                SALT: {"parent_chembl_id": PARENT}}
        merged, _ = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(merged[PARENT]["canonical_smiles"], "PARENT_SMILES")


class ApprovalSurvivesTheMergeTest(unittest.TestCase):
    """A salt of an approved drug is the same approved moiety.

    The first version of this merge keyed the output row on the parent and then
    read the parent's metadata entry. Where the parent had no metadata -- the
    backfill failed, or ChEMBL held no record under the parent identifier --
    the merged row inherited an empty entry and lost ``max_phase``. In a
    ``repurposing_only`` pool that filters on approval, an APPROVED drug then
    vanished silently. That is the same class of silent data loss the
    pagination fix exists to remove, so approval is carried up from whichever
    contributing form has it.
    """

    def test_approval_is_carried_up_from_the_salt(self):
        by_mol = {SALT: _bucket(SALT, [(7.2, 3)])}
        meta = {SALT: {"parent_chembl_id": PARENT, "max_phase": 4,
                       "pref_name": "ELIGLUSTAT TARTRATE"}}
        with mock.patch.object(chembl, "_fetch_molecule_meta", return_value={}):
            _merged, out_meta = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(float(out_meta[PARENT]["max_phase"]), 4.0)

    def test_identity_is_carried_up_when_the_parent_has_none(self):
        by_mol = {SALT: _bucket(SALT, [(7.2, 3)], smiles="S")}
        meta = {SALT: {"parent_chembl_id": PARENT,
                       "pref_name": "ELIGLUSTAT TARTRATE",
                       "canonical_smiles": "S"}}
        with mock.patch.object(chembl, "_fetch_molecule_meta", return_value={}):
            _merged, out_meta = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(out_meta[PARENT]["pref_name"], "ELIGLUSTAT TARTRATE")
        self.assertEqual(out_meta[PARENT]["canonical_smiles"], "S")

    def test_the_parents_own_metadata_wins_when_present(self):
        by_mol = {PARENT: _bucket(PARENT, [(7.8, 1)]),
                  SALT: _bucket(SALT, [(7.2, 3)])}
        meta = {PARENT: {"parent_chembl_id": PARENT, "pref_name": "ELIGLUSTAT",
                         "max_phase": 4},
                SALT: {"parent_chembl_id": PARENT,
                       "pref_name": "ELIGLUSTAT TARTRATE", "max_phase": 4}}
        _merged, out_meta = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(out_meta[PARENT]["pref_name"], "ELIGLUSTAT")

    def test_highest_phase_across_forms_wins(self):
        by_mol = {PARENT: _bucket(PARENT, [(7.8, 1)]),
                  SALT: _bucket(SALT, [(7.2, 3)])}
        meta = {PARENT: {"parent_chembl_id": PARENT, "max_phase": 2},
                SALT: {"parent_chembl_id": PARENT, "max_phase": "4.0"}}
        _merged, out_meta = chembl._collapse_salts_to_parent(by_mol, meta)
        self.assertEqual(float(out_meta[PARENT]["max_phase"]), 4.0)

    def test_unknown_phase_does_not_outrank_a_known_one(self):
        self.assertEqual(float(chembl._max_phase(None, "4.0")), 4.0)
        self.assertEqual(float(chembl._max_phase("4.0", None)), 4.0)
        self.assertIsNone(chembl._max_phase(None, None))
        self.assertIsNone(chembl._max_phase("not-a-number"))


class ParentMetaBackfillTest(unittest.TestCase):
    """The parent may carry no activities of its own.

    When only the salt was assayed, the parent never appears in the activity
    payload, so its name and structure have to be fetched separately or the
    merged row would be anonymous.
    """

    def test_missing_parent_meta_is_fetched(self):
        by_mol = {SALT: _bucket(SALT, [(7.2, 3)])}
        meta = {SALT: {"parent_chembl_id": PARENT}}
        with mock.patch.object(
            chembl, "_fetch_molecule_meta",
            return_value={PARENT: {"pref_name": "ELIGLUSTAT",
                                   "parent_chembl_id": PARENT}},
        ) as fetch:
            merged, out_meta = chembl._collapse_salts_to_parent(by_mol, meta)
        fetch.assert_called_once_with([PARENT])
        self.assertEqual(list(merged), [PARENT])
        self.assertEqual(out_meta[PARENT]["pref_name"], "ELIGLUSTAT")

    def test_no_backfill_call_when_every_parent_is_known(self):
        by_mol = {PARENT: _bucket(PARENT, [(7.8, 1)])}
        meta = {PARENT: {"parent_chembl_id": PARENT}}
        with mock.patch.object(chembl, "_fetch_molecule_meta") as fetch:
            chembl._collapse_salts_to_parent(by_mol, meta)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
