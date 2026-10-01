"""One drug, one candidate: salts and cross-lane duplicates must not compete.

Post-benchmark correction of 2026-09-29. ``candidate_identity`` keys on full
stereochemical InChIKey first, which is right, and leaves two gaps that split a
single drug into candidates that compete with each other.

1. A SALT carries its own InChIKey, because the key covers the counterion:

       ELIGLUSTAT           CHEMBL2110588   parent CHEMBL2110588
       ELIGLUSTAT TARTRATE  CHEMBL5723563   parent CHEMBL2110588

   Both records carry the same ChEMBL parent and still scored as separate
   candidates -- 0.8238 and 0.5002 in one Niemann-Pick type C run, each with
   independently-retrieved gate verdicts, and with the activity evidence that
   should have been pooled divided between them.

2. A record WITHOUT an InChIKey falls back to a PROVIDER-NAMESPACED molecule
   id, so the same ChEMBL molecule arriving through two lanes becomes two
   candidates. Cariprazine appeared four times in that run: twice for DRD2 and
   twice for DRD3, every row CHEMBL2028019.

The coalescing pass runs AFTER keying, so it can only union groups and can
never split ones that already merge on structure. A ChEMBL id is globally
unique, so sharing one is proof of identity -- unlike the opaque per-provider
ids the namespacing exists to protect.
"""

import unittest

from data_sources.evidence_ledger import (
    EvidenceRecord,
    EvidenceRole,
    QualificationStatus,
    SourceType,
    merge_candidates,
)

PARENT = "CHEMBL2110588"   # ELIGLUSTAT
SALT = "CHEMBL5723563"     # ELIGLUSTAT TARTRATE
FREE_KEY = "FJZZPCZKBUKGGU-AUSIDOKSSA-N"
SALT_KEY = "KUBARPMUNHKBIQ-VTHUDJRQSA-N"


def _rec(**kw):
    base = dict(
        source_type=SourceType.BIOACTIVITY_ASSAY,
        evidence_role=EvidenceRole.EFFICACY,
        qualification_status=QualificationStatus.QUALIFIED,
        provider="chembl",
        source_id="s1",
        molecule_id=PARENT,
        parent_molecule_id=PARENT,
        molecule_name="ELIGLUSTAT",
        inchikey=FREE_KEY,
        target_symbol="UGCG",
        target_accession="Q16739",
        disease_name="Niemann-Pick disease type C",
    )
    base.update(kw)
    return EvidenceRecord(**base)


class SaltCoalescingTest(unittest.TestCase):

    def test_salt_and_free_base_become_one_candidate(self):
        """The exact pair that competed with itself."""
        merged = merge_candidates([
            _rec(source_id="a", molecule_id=PARENT, parent_molecule_id=PARENT,
                 inchikey=FREE_KEY),
            _rec(source_id="b", molecule_id=SALT, parent_molecule_id=PARENT,
                 inchikey=SALT_KEY, molecule_name="ELIGLUSTAT TARTRATE",
                 source_type=SourceType.MECHANISM),
        ])
        self.assertEqual(len(merged), 1)

    def test_evidence_from_both_forms_is_pooled(self):
        merged = merge_candidates([
            _rec(source_id="a", molecule_id=PARENT, parent_molecule_id=PARENT,
                 inchikey=FREE_KEY),
            _rec(source_id="b", molecule_id=SALT, parent_molecule_id=PARENT,
                 inchikey=SALT_KEY, source_type=SourceType.MECHANISM),
        ])
        records = (merged[0].get("_evidence_ledger") or {}).get("records") or []
        self.assertEqual(len(records), 2, "both forms' evidence must survive")

    def test_different_targets_stay_separate(self):
        """Coalescing is per target; the same drug on two targets is two rows."""
        merged = merge_candidates([
            _rec(source_id="a", target_symbol="UGCG", target_accession="Q16739"),
            _rec(source_id="b", target_symbol="GBA1", target_accession="P04062"),
        ])
        self.assertEqual(len(merged), 2)

    def test_genuinely_different_drugs_do_not_merge(self):
        merged = merge_candidates([
            _rec(source_id="a", molecule_id="CHEMBL111",
                 parent_molecule_id="CHEMBL111", inchikey="AAAAAAAAAAAAAA-BBBBBBBBBB-N"),
            _rec(source_id="b", molecule_id="CHEMBL222",
                 parent_molecule_id="CHEMBL222", inchikey="CCCCCCCCCCCCCC-DDDDDDDDDD-N"),
        ])
        self.assertEqual(len(merged), 2)


class CrossLaneDuplicateTest(unittest.TestCase):
    """The cariprazine shape: one ChEMBL molecule, two lanes, two rows."""

    def test_same_chembl_id_without_inchikey_merges_across_providers(self):
        merged = merge_candidates([
            _rec(source_id="a", provider="chembl", molecule_id="CHEMBL2028019",
                 parent_molecule_id="", inchikey="", molecule_name="cariprazine",
                 target_symbol="DRD2", target_accession="P14416"),
            _rec(source_id="b", provider="drugcentral",
                 molecule_id="CHEMBL2028019", parent_molecule_id="",
                 inchikey="", molecule_name="cariprazine",
                 target_symbol="DRD2", target_accession="P14416",
                 source_type=SourceType.MECHANISM),
        ])
        self.assertEqual(len(merged), 1)

    def test_opaque_provider_ids_are_still_namespaced(self):
        """Non-ChEMBL ids may collide between providers and must not merge."""
        merged = merge_candidates([
            _rec(source_id="a", provider="drugcentral", molecule_id="4834",
                 parent_molecule_id="", inchikey="", molecule_name="drug-a",
                 target_symbol="UGCG", target_accession="Q16739"),
            _rec(source_id="b", provider="gtopdb", molecule_id="4834",
                 parent_molecule_id="", inchikey="", molecule_name="drug-b",
                 target_symbol="UGCG", target_accession="Q16739",
                 source_type=SourceType.MECHANISM),
        ])
        self.assertEqual(len(merged), 2,
                         "two providers' opaque id 4834 are not the same drug")

    def test_structure_based_merging_is_unaffected(self):
        """The pass may only union; rows that already merged must still merge."""
        merged = merge_candidates([
            _rec(source_id="a", provider="chembl", molecule_id=PARENT,
                 inchikey=FREE_KEY),
            _rec(source_id="b", provider="drugcentral", molecule_id="4834",
                 parent_molecule_id="", inchikey=FREE_KEY,
                 source_type=SourceType.MECHANISM),
        ])
        self.assertEqual(len(merged), 1)


class DeterminismTest(unittest.TestCase):

    def test_result_is_order_independent(self):
        a = _rec(source_id="a", molecule_id=PARENT, inchikey=FREE_KEY)
        b = _rec(source_id="b", molecule_id=SALT, parent_molecule_id=PARENT,
                 inchikey=SALT_KEY, source_type=SourceType.MECHANISM)
        forward = merge_candidates([a, b])
        backward = merge_candidates([b, a])
        self.assertEqual(len(forward), len(backward))
        self.assertEqual(forward[0].get("canonical_compound_identity"),
                         backward[0].get("canonical_compound_identity"))

    def test_empty_input_is_handled(self):
        self.assertEqual(merge_candidates([]), [])


if __name__ == "__main__":
    unittest.main()
