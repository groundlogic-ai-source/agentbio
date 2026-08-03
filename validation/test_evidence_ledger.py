"""
Unit tests for data_sources.evidence_ledger (PURE module).

Run with stdlib only:
    python3 -m unittest validation.test_evidence_ledger -v

Covers every invariant the module promises:
  * salt/ester forms of one active moiety collapse via the InChIKey block
  * no-structure records fall back to provider id then name (never wrongly)
  * the same assay/publication/label/trial across providers deduplicates by
    lineage — provider count is never an evidence boost
  * contradictions are preserved on the merged candidate
  * NOT_APPLICABLE is distinct from a real 0.0 quality; efficacy != safety
  * every Chemist output field is preserved through the union
  * merge output ordering is deterministic
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources.evidence_ledger import (  # noqa: E402
    EvidenceRecord, EvidenceRole, SourceType, Direction,
    QualificationStatus, ContradictionStatus, NOT_APPLICABLE,
    normalize_name, inchikey_block, normalize_evidence,
    evidence_quality, efficacy_confidence, safety_confidence,
    candidate_identity, merge_candidates,
)


# --- shared InChIKeys ------------------------------------------------------
# Two salt forms of ONE active moiety share the 14-char connectivity block.
_MOIETY_BLOCK = "RZVAJINKPMORJF"
_FREE_BASE = f"{_MOIETY_BLOCK}-UHFFFAOYSA-N"
_HCL_SALT = f"{_MOIETY_BLOCK}-QWERTYUISA-M"   # same block, different salt block
# A structurally UNRELATED moiety.
_OTHER_BLOCK = "AAAAAAAAAAAAAA"
_OTHER_KEY = f"{_OTHER_BLOCK}-BBBBBBBBSA-N"


def rec(**kw) -> EvidenceRecord:
    return normalize_evidence(kw)


class NormalizationTests(unittest.TestCase):
    def test_inchikey_block_extracts_14(self):
        self.assertEqual(inchikey_block(_FREE_BASE), _MOIETY_BLOCK)
        self.assertEqual(inchikey_block(_HCL_SALT), _MOIETY_BLOCK)

    def test_inchikey_block_bare_and_bad(self):
        self.assertEqual(inchikey_block(_MOIETY_BLOCK), _MOIETY_BLOCK)
        self.assertIsNone(inchikey_block(None))
        self.assertIsNone(inchikey_block(""))
        self.assertIsNone(inchikey_block("not-a-key"))

    def test_normalize_name_does_not_strip_salt_words(self):
        # Salt words are NOT stripped; name identity is last-resort only.
        self.assertEqual(normalize_name("  Drug   X  "), "drug x")
        self.assertNotEqual(normalize_name("Drug X"), normalize_name("Drug X Sodium"))

    def test_enum_coercion_lenient(self):
        r = rec(source_type="bioactivity_assay", direction="INHIBITOR",
                evidence_role="efficacy", qualification_status="qualified",
                contradiction_status="none")
        self.assertEqual(r.source_type, SourceType.BIOACTIVITY_ASSAY)
        self.assertEqual(r.direction, Direction.INHIBITOR)
        self.assertEqual(r.evidence_role, EvidenceRole.EFFICACY)
        self.assertEqual(r.qualification_status, QualificationStatus.QUALIFIED)

    def test_enum_coercion_falls_back_on_garbage(self):
        r = rec(source_type="nonsense", direction="sideways")
        self.assertEqual(r.source_type, SourceType.OTHER)
        self.assertEqual(r.direction, Direction.UNKNOWN)

    def test_bad_measurement_value_becomes_none(self):
        self.assertIsNone(rec(measurement_value="abc").measurement_value)
        self.assertIsNone(rec(measurement_value=float("nan")).measurement_value)


class SaltIdentityTests(unittest.TestCase):
    def test_salt_forms_share_identity(self):
        a = rec(inchikey=_FREE_BASE, molecule_name="Drug X")
        b = rec(inchikey=_HCL_SALT, molecule_name="Drug X hydrochloride")
        self.assertEqual(candidate_identity(a), candidate_identity(b))
        self.assertTrue(candidate_identity(a).startswith("moiety:"))

    def test_salt_forms_merge_to_one_candidate(self):
        recs = [
            rec(provider="chembl", inchikey=_FREE_BASE, molecule_name="Drug X",
                molecule_id="CHEMBL1", target_symbol="EGFR"),
            rec(provider="chembl", inchikey=_HCL_SALT, molecule_name="Drug X HCl",
                molecule_id="CHEMBL2", target_symbol="EGFR"),
        ]
        merged = merge_candidates(recs)
        self.assertEqual(len(merged), 1)

    def test_unrelated_structures_do_not_merge(self):
        a = rec(inchikey=_FREE_BASE, molecule_name="Same Name")
        b = rec(inchikey=_OTHER_KEY, molecule_name="Same Name")
        self.assertNotEqual(candidate_identity(a), candidate_identity(b))
        self.assertEqual(len(merge_candidates([a, b])), 2)


class NoStructureIdentityTests(unittest.TestCase):
    def test_provider_id_used_when_no_structure(self):
        a = rec(provider="chembl", molecule_id="CHEMBL99", molecule_name="Foo")
        self.assertEqual(candidate_identity(a), "molid:chembl:chembl99")

    def test_name_only_last_resort(self):
        a = rec(molecule_name="Aspirin")
        self.assertEqual(candidate_identity(a), "name:aspirin")

    def test_name_records_merge_but_only_without_structure(self):
        a = rec(molecule_name="Aspirin", target_symbol="PTGS1")
        b = rec(molecule_name="aspirin ", target_symbol="PTGS2")
        merged = merge_candidates([a, b])
        self.assertEqual(len(merged), 1)

    def test_structure_record_not_merged_into_name_bucket(self):
        # A record WITH structure must never merge into a name-only bucket.
        name_only = rec(molecule_name="Drug X")
        structured = rec(molecule_name="Drug X", inchikey=_FREE_BASE)
        self.assertNotEqual(candidate_identity(name_only),
                            candidate_identity(structured))
        self.assertEqual(len(merge_candidates([name_only, structured])), 2)

    def test_two_providers_no_structure_do_not_collide(self):
        a = rec(provider="chembl", molecule_id="X1")
        b = rec(provider="pubchem", molecule_id="X1")
        self.assertNotEqual(candidate_identity(a), candidate_identity(b))

    def test_totally_anonymous_records_do_not_merge(self):
        a = rec(provider="p", source_id="s1")
        b = rec(provider="p", source_id="s2")
        self.assertNotEqual(candidate_identity(a), candidate_identity(b))


class LineageDedupTests(unittest.TestCase):
    def test_same_publication_across_providers_dedups(self):
        # ChEMBL and Open Targets both cite the same PMID for the same target.
        a = rec(provider="chembl", inchikey=_FREE_BASE, target_accession="P00533",
                publication_id="12345678", evidence_role="target_link",
                source_type="publication")
        b = rec(provider="opentargets", inchikey=_FREE_BASE, target_accession="P00533",
                publication_id="12345678", evidence_role="target_link",
                source_type="publication")
        self.assertEqual(a.lineage_key(), b.lineage_key())
        merged = merge_candidates([a, b])[0]
        led = merged["_evidence_ledger"]
        # One piece of evidence even though two providers reported it.
        self.assertEqual(led["distinct_evidence_count"], 1)
        self.assertEqual(led["record_count"], 1)
        # Both providers still recorded as participants (health), not as boost.
        self.assertEqual(set(led["providers"]), {"chembl", "opentargets"})

    def test_same_assay_across_providers_dedups(self):
        a = rec(provider="chembl", inchikey=_FREE_BASE, assay_id="CHEMBL_A1",
                target_accession="P1", measurement_type="pchembl",
                measurement_value=7.0, source_type="bioactivity_assay")
        b = rec(provider="mirror", inchikey=_FREE_BASE, assay_id="CHEMBL_A1",
                target_accession="P1", measurement_type="pchembl",
                measurement_value=7.0, source_type="bioactivity_assay")
        self.assertEqual(a.lineage_key(), b.lineage_key())
        self.assertEqual(merge_candidates([a, b])[0]["_evidence_ledger"]
                         ["distinct_evidence_count"], 1)

    def test_trial_and_label_lineage_keys(self):
        t = rec(trial_id="NCT01234567", provider="ct")
        self.assertTrue(t.lineage_key().startswith("trial:nct01234567"))
        lab = rec(label_id="SETID-9", provider="fda")
        self.assertTrue(lab.lineage_key().startswith("label:setid-9"))

    def test_provider_count_never_boosts(self):
        # Ten providers citing one publication -> still one evidence datum.
        recs = [rec(provider=f"p{i}", inchikey=_FREE_BASE, target_accession="P1",
                    publication_id="999", source_type="publication",
                    evidence_role="efficacy") for i in range(10)]
        merged = merge_candidates(recs)[0]
        self.assertEqual(merged["_evidence_ledger"]["distinct_evidence_count"], 1)

    def test_distinct_assays_are_separate_evidence(self):
        a = rec(inchikey=_FREE_BASE, assay_id="A1", target_accession="P1",
                source_type="bioactivity_assay", measurement_type="pchembl",
                measurement_value=6.0)
        b = rec(inchikey=_FREE_BASE, assay_id="A2", target_accession="P1",
                source_type="bioactivity_assay", measurement_type="pchembl",
                measurement_value=8.0)
        merged = merge_candidates([a, b])[0]
        self.assertEqual(merged["_evidence_ledger"]["distinct_evidence_count"], 2)

    def test_qualified_wins_over_unqualified_duplicate(self):
        unq = rec(provider="p1", inchikey=_FREE_BASE, assay_id="A1",
                  target_accession="P1", source_type="bioactivity_assay",
                  measurement_type="pchembl", measurement_value=7.0,
                  qualification_status="unqualified")
        q = rec(provider="p2", inchikey=_FREE_BASE, assay_id="A1",
                target_accession="P1", source_type="bioactivity_assay",
                measurement_type="pchembl", measurement_value=7.0,
                qualification_status="qualified")
        merged = merge_candidates([unq, q])[0]
        led = merged["_evidence_ledger"]
        self.assertEqual(led["distinct_evidence_count"], 1)
        # The surviving record is the qualified one (evidence confidence not
        # halved). Legacy confidence_score remains reserved for ChEMBL's 0-9
        # assay-confidence scale.
        self.assertIsNone(merged["confidence_score"])
        self.assertIsNotNone(merged["efficacy_confidence"])


class QualityTests(unittest.TestCase):
    def test_quality_without_pchembl_is_nonzero(self):
        # A genetic association has no pChEMBL yet must score > 0.
        r = rec(source_type="genetic_association", evidence_role="target_link")
        q = evidence_quality(r)
        self.assertIsNot(q, NOT_APPLICABLE)
        self.assertGreater(q, 0.0)

    def test_pchembl_lift_bounded(self):
        low = rec(source_type="bioactivity_assay", measurement_type="pchembl",
                  measurement_value=5.0)
        high = rec(source_type="bioactivity_assay", measurement_type="pchembl",
                   measurement_value=9.0)
        self.assertLess(evidence_quality(low), evidence_quality(high))
        self.assertLessEqual(evidence_quality(high), 1.0)

    def test_not_applicable_distinct_from_zero(self):
        # No safety modality present -> NOT_APPLICABLE, not 0.0.
        eff_only = [rec(source_type="bioactivity_assay", measurement_type="pchembl",
                        measurement_value=7.0)]
        saf = safety_confidence(eff_only)
        self.assertIs(saf, NOT_APPLICABLE)
        self.assertIsNot(saf, 0.0)
        self.assertNotEqual(saf, 0.0)
        self.assertFalse(bool(NOT_APPLICABLE))

    def test_efficacy_and_safety_separate(self):
        recs = [
            rec(source_type="bioactivity_assay", measurement_type="pchembl",
                measurement_value=8.0),
            rec(source_type="adverse_event", evidence_role="safety"),
        ]
        eff = efficacy_confidence(recs)
        saf = safety_confidence(recs)
        self.assertIsNot(eff, NOT_APPLICABLE)
        self.assertIsNot(saf, NOT_APPLICABLE)
        # They are computed independently and are not the same aggregate.
        self.assertIsInstance(eff, float)
        self.assertIsInstance(saf, float)

    def test_qualified_label_mechanism_is_efficacy_evidence(self):
        label = rec(
            source_type="drug_label", evidence_role="efficacy",
            label_id="synthetic-spl", measurement_type="label_mechanism_class",
            qualification_status="qualified",
        )
        self.assertEqual(efficacy_confidence([label]), 0.8)

    def test_unqualified_penalty_not_dropped_to_zero(self):
        r = rec(source_type="mechanism", qualification_status="unqualified")
        q = evidence_quality(r)
        self.assertGreater(q, 0.0)
        self.assertLess(q, _base := 0.85)

    def test_contradicted_penalty(self):
        clean = rec(source_type="mechanism")
        contra = rec(source_type="mechanism", contradiction_status="contradicted")
        self.assertLess(evidence_quality(contra), evidence_quality(clean))


class ContradictionTests(unittest.TestCase):
    def test_contradiction_preserved(self):
        recs = [
            rec(provider="a", inchikey=_FREE_BASE, target_symbol="EGFR",
                direction="agonist", evidence_role="target_link",
                source_type="mechanism", contradiction_status="contradicts"),
            rec(provider="b", inchikey=_FREE_BASE, target_symbol="EGFR",
                direction="antagonist", evidence_role="target_link",
                source_type="mechanism", contradiction_status="contradicted",
                assay_id="A2"),
        ]
        merged = merge_candidates(recs)[0]
        self.assertEqual(merged["_evidence_ledger"]["contradiction_count"], 2)


class AggregateAndPreservationTests(unittest.TestCase):
    def _rich(self):
        return [
            rec(provider="chembl", inchikey=_FREE_BASE, molecule_name="Drug X",
                molecule_id="CHEMBL25", smiles="CCO", target_symbol="EGFR",
                target_accession="P00533", assay_id="A1",
                source_type="bioactivity_assay", measurement_type="pchembl",
                measurement_value=7.5, evidence_role="efficacy",
                qualification_status="qualified", disease_name="NSCLC"),
            rec(provider="chembl", inchikey=_HCL_SALT, molecule_name="Drug X HCl",
                molecule_id="CHEMBL26", target_symbol="EGFR",
                target_accession="P00533", assay_id="A2",
                source_type="bioactivity_assay", measurement_type="pchembl",
                measurement_value=8.9, evidence_role="efficacy",
                qualification_status="qualified"),
            rec(provider="chembl", inchikey=_FREE_BASE, target_accession="P00533",
                source_type="regulatory_approval", measurement_type="max_phase",
                measurement_value=4.0, evidence_role="approval"),
            rec(provider="opentargets", inchikey=_FREE_BASE, target_symbol="EGFR",
                target_accession="P00533", source_type="genetic_association",
                measurement_type="ot_association", measurement_value=0.82,
                evidence_role="target_link", disease_name="NSCLC"),
        ]

    def test_all_chemist_fields_present(self):
        merged = merge_candidates(self._rich())[0]
        required = [
            "drug_name", "molecule_chembl_id", "smiles", "inchikey",
            "pchembl_value", "confidence_score", "max_phase",
            "is_approved_drug", "source_chembl_ids", "source_activity_ids",
            "target_symbol", "uniprot_id", "target_discovery_method",
            "disease_name", "ot_association_score",
        ]
        for f in required:
            self.assertIn(f, merged, f"missing Chemist field {f!r}")

    def test_aggregates(self):
        merged = merge_candidates(self._rich())[0]
        self.assertEqual(merged["max_phase"], 4.0)          # max approval
        self.assertTrue(merged["is_approved_drug"])
        self.assertEqual(merged["pchembl_value"], 8.9)      # best affinity
        self.assertEqual(merged["uniprot_id"], "P00533")
        self.assertEqual(merged["target_symbol"], "EGFR")
        self.assertEqual(merged["ot_association_score"], 0.82)
        self.assertEqual(merged["target_discovery_method"], "genetic_association")
        self.assertEqual(merged["molecule_chembl_id"], "CHEMBL25")

    def test_source_records_and_types_preserved(self):
        merged = merge_candidates(self._rich())[0]
        led = merged["_evidence_ledger"]
        self.assertIn("bioactivity_assay", led["source_types"])
        self.assertIn("genetic_association", led["source_types"])
        self.assertIn("regulatory_approval", led["source_types"])
        self.assertEqual(set(led["providers"]), {"chembl", "opentargets"})
        self.assertIn("CHEMBL25", merged["source_chembl_ids"])
        self.assertIn("A1", merged["source_activity_ids"])

    def test_source_health_explicit(self):
        recs = [
            rec(provider="chembl", inchikey=_FREE_BASE, assay_id="A1",
                source_type="bioactivity_assay", qualification_status="qualified"),
            rec(provider="flaky", inchikey=_FREE_BASE, assay_id="A2",
                source_type="bioactivity_assay", qualification_status="unqualified"),
        ]
        led = merge_candidates(recs)[0]["_evidence_ledger"]
        self.assertTrue(led["source_health"]["chembl"])
        self.assertFalse(led["source_health"]["flaky"])

    def test_target_memberships_union(self):
        recs = [
            rec(inchikey=_FREE_BASE, target_symbol="EGFR", target_accession="P00533",
                assay_id="A1"),
            rec(inchikey=_FREE_BASE, target_symbol="ERBB2", target_accession="P04626",
                assay_id="A2"),
        ]
        led = merge_candidates(recs)[0]["_evidence_ledger"]
        self.assertEqual(set(led["target_symbols"]), {"EGFR", "ERBB2"})
        self.assertEqual(set(led["target_accessions"]), {"P00533", "P04626"})


class DeterminismTests(unittest.TestCase):
    def test_ordering_is_deterministic(self):
        recs = [
            rec(inchikey=_OTHER_KEY, molecule_name="Zeta", assay_id="A1"),
            rec(inchikey=_FREE_BASE, molecule_name="Alpha", assay_id="A2"),
            rec(molecule_name="Only Name", assay_id="A3"),
        ]
        out1 = merge_candidates(recs)
        out2 = merge_candidates(list(reversed(recs)))
        keys1 = [c["_evidence_ledger"]["identity"] for c in out1]
        keys2 = [c["_evidence_ledger"]["identity"] for c in out2]
        self.assertEqual(keys1, keys2)
        self.assertEqual(keys1, sorted(keys1))

    def test_confidence_none_when_not_applicable(self):
        # A structure-only record has no efficacy modality contribution?
        # structure_db IS in efficacy? No — it is not. Confidence -> None.
        r = rec(source_type="structure_db", inchikey=_FREE_BASE, assay_id="A1")
        merged = merge_candidates([r])[0]
        self.assertIsNone(merged["confidence_score"])
        self.assertTrue(merged["_evidence_ledger"]["efficacy_not_applicable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
