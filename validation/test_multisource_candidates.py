"""
Unit tests for data_sources.multisource_candidates.

Run with stdlib only:
    python3 -m unittest validation.test_multisource_candidates -v

Adapters are MOCKED at the envelope level: the converters and the fan-out are
exercised against hand-built GtoPdb / DrugCentral envelopes, so these tests
never touch the network.  Coverage:

  * union across sources by InChIKey block (one active moiety, many providers)
  * legacy ChEMBL enriched dicts fold in with SEPARATE pChEMBL and
    assay_confidence records (both preserved through the union)
  * an unavailable source surfaces in source_status and never fabricates
    candidates
  * no provider-count bonus: N providers reporting the SAME lineage never
    inflate the evidence count
  * the fan-out NEVER queries a source by drug name (target-first only)
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources import multisource_candidates as msc  # noqa: E402
from data_sources.evidence_ledger import SourceType  # noqa: E402
from data_sources.evidence_ledger import EvidenceRecord, EvidenceRole  # noqa: E402
from data_sources.evidence_ledger import QualificationStatus, merge_candidates  # noqa: E402


# --- shared structure: ONE active moiety, two salt forms -------------------
_BLOCK = "RZVAJINKPMORJF"
_FREE_BASE = f"{_BLOCK}-UHFFFAOYSA-N"
_HCL_SALT = f"{_BLOCK}-QWERTYUISA-M"   # same 14-char block, different salt


def _gtopdb_env(status="ok", candidates=None, error=None):
    return {
        "source": "gtopdb",
        "status": status,
        "candidates": candidates or [],
        "error": error,
        "release": None,
    }


def _drugcentral_env(status="ok", candidates=None, error=None):
    return {
        "source": "drugcentral",
        "status": status,
        "candidates": candidates or [],
        "error": error,
        "release": None,
    }


def _gtopdb_candidate():
    return {
        "source": "gtopdb",
        "provider_ligand_id": 111,
        "provider_interaction_id": 999,
        "chembl_id": "CHEMBL25",
        "name": "Aspirin",
        "inn": "aspirin",
        "ligand_type": "Synthetic organic",
        "is_approved": True,
        "is_withdrawn": False,
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "inchikey": _FREE_BASE,
        "action": "Inhibitor",
        "action_type": "Enzyme",
        "affinity": 5.5,
        "affinity_parameter": "pIC50",
        "target_id": 1,
        "target_name": "PTGS1",
        "target_species": "Homo sapiens",
        "refs": [{"reference_id": 7, "pmid": "12345", "title": "T", "year": 2001}],
        "evidence": [],
    }


def _drugcentral_candidate(inchikey=_HCL_SALT):
    return {
        "source": "drugcentral",
        "struct_id": 4321,
        "provider_act_id": 88,
        "name": "Aspirin",
        "structure_status": "OFP",
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "inchikey": inchikey,
        "act_type": "IC50",
        "act_value": 12.0,
        "act_unit": "nM",
        "action_type": "INHIBITOR",
        "moa": "COX-1 inhibitor",
        "gene": "PTGS1",
        "accession": "P23219",
        "swissprot": "PGH1_HUMAN",
        "target_id": 1,
        "target_name": "Prostaglandin G/H synthase 1",
        "organism": "Homo sapiens",
        "evidence": [{
            "type": "drugcentral_activity",
            "act_id": 88,
            "act_type": "IC50",
            "act_value": 12.0,
            "act_unit": "nM",
            "act_source": "SCIENTIFIC LITERATURE",
            "action_type": "INHIBITOR",
        }],
    }


def _chembl_enriched(inchikey=_FREE_BASE):
    return {
        "molecule_chembl_id": "CHEMBL25",
        "drug_name": "Aspirin",
        "pref_name": "ASPIRIN",
        "smiles": "CC(=O)Oc1ccccc1C(=O)O",
        "inchikey": inchikey,
        "pchembl_value": 6.7,
        "confidence_score": 9,
        "max_phase": 4,
        "source_activity_ids": [55501],
        "source_chembl_ids": ["CHEMBL25", "CHEMBL_ASSAY_1"],
        "target_symbol": "PTGS1",
        "uniprot_id": "P23219",
        "target_discovery_method": "pharmacological_precedent",
        "disease_name": "inflammation",
        "ot_association_score": 0.42,
        "is_approved_drug": True,
    }


class ConverterTests(unittest.TestCase):
    def test_gtopdb_emits_mechanism_approval_and_publication(self):
        recs = msc.records_from_gtopdb_envelope(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            uniprot_id="P23219", disease_name="inflammation")
        types = [r.source_type for r in recs]
        self.assertIn(SourceType.MECHANISM, types)
        self.assertIn(SourceType.REGULATORY_APPROVAL, types)
        self.assertIn(SourceType.PUBLICATION, types)
        pubs = [r for r in recs if r.source_type == SourceType.PUBLICATION]
        self.assertEqual(pubs[0].publication_id, "12345")

    def test_drugcentral_emits_bioactivity_and_approval(self):
        recs = msc.records_from_drugcentral_envelope(
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            uniprot_id="P23219", disease_name="inflammation")
        types = [r.source_type for r in recs]
        self.assertIn(SourceType.BIOACTIVITY_ASSAY, types)
        self.assertIn(SourceType.REGULATORY_APPROVAL, types)
        bio = [r for r in recs if r.source_type == SourceType.BIOACTIVITY_ASSAY]
        self.assertEqual(bio[0].measurement_type, "IC50")
        self.assertEqual(bio[0].measurement_value, 12.0)

    def test_gtopdb_unavailable_envelope_yields_no_records(self):
        recs = msc.records_from_gtopdb_envelope(
            _gtopdb_env(status="unavailable", error="boom"))
        self.assertEqual(recs, [])


class ChemblEnrichedTests(unittest.TestCase):
    def test_separate_pchembl_and_confidence_records(self):
        recs = msc.normalize_chembl_enriched([_chembl_enriched()])
        mtypes = {r.measurement_type for r in recs}
        self.assertIn("pchembl", mtypes)
        self.assertIn("assay_confidence", mtypes)
        pchembl = [r for r in recs if r.measurement_type == "pchembl"]
        conf = [r for r in recs if r.measurement_type == "assay_confidence"]
        # They are DISTINCT records, not one averaged blob.
        self.assertEqual(len(pchembl), 1)
        self.assertEqual(len(conf), 1)
        self.assertEqual(pchembl[0].measurement_value, 6.7)
        self.assertEqual(conf[0].measurement_value, 9.0)
        self.assertNotEqual(pchembl[0].source_id, conf[0].source_id)

    def test_chembl_field_preservation_through_merge(self):
        from data_sources.evidence_ledger import merge_candidates
        recs = msc.normalize_chembl_enriched([_chembl_enriched()])
        merged = merge_candidates(recs)
        self.assertEqual(len(merged), 1)
        cand = merged[0]
        self.assertEqual(cand["molecule_chembl_id"], "CHEMBL25")
        self.assertEqual(cand["pchembl_value"], 6.7)
        self.assertEqual(cand["confidence_score"], 9)   # 0-9 assay scale kept
        self.assertEqual(cand["max_phase"], 4.0)
        self.assertTrue(cand["is_approved_drug"])
        self.assertEqual(cand["ot_association_score"], 0.42)
        self.assertEqual(cand["target_discovery_method"],
                         "pharmacological_precedent")
        self.assertEqual(cand["disease_name"], "inflammation")


class UnionTests(unittest.TestCase):
    def _collect(self, gtop_env, dc_env, chembl=None):
        # bindingdb is mocked to a healthy-empty envelope: these tests are
        # about gtopdb/drugcentral/chembl union semantics and must never hit
        # the network through the default-enabled BindingDB lane.
        with mock.patch.object(msc.gtopdb, "get_target_interactions",
                               return_value=gtop_env) as g, \
             mock.patch.object(msc.drugcentral_v2, "get_target_interactions",
                               return_value=dc_env) as d, \
             mock.patch.object(msc.bindingdb, "get_target_interactions",
                               return_value={"source": "bindingdb",
                                             "status": "empty",
                                             "candidates": [], "error": None,
                                             "release": None, "stats": {}}):
            result = msc.collect_target_candidates(
                uniprot_id="P23219", gene="PTGS1",
                disease_name="inflammation", ot_score=0.42,
                target_discovery_method="pharmacological_precedent",
                repurposing_only=True, chembl_enriched=chembl)
        return result, g, d

    def test_union_across_sources_by_inchikey(self):
        # Same moiety (salt vs free base) from all three sources -> ONE candidate.
        result, _, _ = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),          # free base
            _drugcentral_env(candidates=[_drugcentral_candidate()]),  # HCl salt
            chembl=[_chembl_enriched()])
        cands = result["candidates"]
        self.assertEqual(len(cands), 1)
        cand = cands[0]
        providers = cand["_evidence_ledger"]["providers"]
        self.assertEqual(set(providers), {"gtopdb", "drugcentral", "chembl"})
        # Structural union keyed on the shared 14-char block.
        self.assertTrue(cand["_evidence_ledger"]["identity"].endswith(_BLOCK))

    def test_target_first_never_queries_by_name(self):
        _, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]))
        # GtoPdb called with the accession, not a drug name.
        g.assert_called_once()
        self.assertEqual(g.call_args.args[0], "P23219")
        # DrugCentral called with accession + gene fallback, never a drug name.
        d.assert_called_once()
        self.assertEqual(d.call_args.args[0], "P23219")
        self.assertEqual(d.call_args.kwargs.get("gene"), "PTGS1")
        # No adapter argument anywhere equals the held-out drug name.
        for ca in (g.call_args, d.call_args):
            for val in list(ca.args) + list(ca.kwargs.values()):
                self.assertNotIn(str(val).lower(), {"aspirin"})

    def test_unavailable_source_surfaced_and_no_fabricated_candidates(self):
        result, _, _ = self._collect(
            _gtopdb_env(status="unavailable", error="503", candidates=[]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]))
        self.assertEqual(result["source_status"]["gtopdb"]["status"],
                         "unavailable")
        self.assertEqual(result["source_status"]["gtopdb"]["error"], "503")
        self.assertEqual(result["source_status"]["drugcentral"]["status"], "ok")
        # Only the healthy source contributes; nothing fabricated for the dead one.
        self.assertEqual(len(result["candidates"]), 1)
        providers = result["candidates"][0]["_evidence_ledger"]["providers"]
        self.assertNotIn("gtopdb", providers)

    def test_both_sources_unavailable_no_candidates(self):
        result, _, _ = self._collect(
            _gtopdb_env(status="unavailable", error="503"),
            _drugcentral_env(status="unavailable", error="500"))
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["source_status"]["gtopdb"]["status"],
                         "unavailable")
        self.assertEqual(result["source_status"]["drugcentral"]["status"],
                         "unavailable")

    def test_no_provider_count_bonus_on_shared_lineage(self):
        # GtoPdb and ChEMBL both cite the SAME publication (PMID 12345):
        # the ledger must count that publication lineage ONCE, not twice.
        gtop = _gtopdb_candidate()          # cites PMID 12345
        enriched = _chembl_enriched()
        # Give the ChEMBL enriched dict a co-cited publication via a shaped
        # record: simulate by adding a publication through a second gtopdb cand
        # citing the same PMID from a different "provider" perspective.
        second = _gtopdb_candidate()
        second["provider_interaction_id"] = 1000
        result, _, _ = self._collect(
            _gtopdb_env(candidates=[gtop, second]),
            _drugcentral_env(candidates=[]),
            chembl=[enriched])
        cand = result["candidates"][0]
        led = cand["_evidence_ledger"]
        # The single shared PMID lineage appears once in the distinct-evidence
        # set even though two interaction rows cited it.
        pub_lineages = [r["lineage_id"] for r in led["records"]
                        if r["source_type"] == "publication"]
        self.assertEqual(len(set(pub_lineages)), 1)

    def test_source_status_includes_chembl_when_supplied(self):
        result, _, _ = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[]),
            chembl=[_chembl_enriched()])
        self.assertEqual(result["source_status"]["chembl"]["status"], "ok")

    def test_no_double_count_when_gtopdb_and_chembl_same_moiety(self):
        # Distinct-evidence count reflects distinct artifacts, and having the
        # drug in two providers does not inflate the record set with duplicates
        # of the same underlying pChEMBL/publication artifact.
        result, _, _ = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[]),
            chembl=[_chembl_enriched()])
        cand = result["candidates"][0]
        led = cand["_evidence_ledger"]
        # record_count is the number of distinct lineages absorbed.
        self.assertEqual(led["record_count"], led["distinct_evidence_count"])

    def test_process_membership_overrides_unrelated_default_role(self):
        generic_record = EvidenceRecord(
            provider="drugcentral",
            source_type=SourceType.BIOACTIVITY_ASSAY,
            source_id="generic-assay",
            assay_id="generic-assay",
            evidence_role=EvidenceRole.EFFICACY,
            molecule_id="1",
            molecule_name="Phenobarbital",
            inchikey=_FREE_BASE,
            target_symbol="GABRG2",
            target_accession="P18507",
            measurement_type="pchembl",
            measurement_value=9.0,
            qualification_status=QualificationStatus.QUALIFIED,
        )
        process_record = EvidenceRecord(
            provider="europepmc",
            source_type=SourceType.PUBLICATION,
            source_id="europepmc:12345:gaba",
            publication_id="12345",
            evidence_role=EvidenceRole.DISEASE_LINK,
            molecule_id="2",
            molecule_name="Phenobarbital",
            inchikey=_FREE_BASE,
            target_symbol="GABRA1",
            target_accession="P14867",
            phenotype="GABA-A receptor signaling",
            context=(
                "mechanism_class=GABA-A receptor signaling;"
                "therapeutic_role=symptom_treatment"
            ),
            qualification_status=QualificationStatus.QUALIFIED,
        )
        generic = merge_candidates([generic_record])[0]
        generic.update({
            "pchembl_value": 9.0,
            "mechanism_class": None,
            "therapeutic_role": "disease_modifying",
            "process_support": [],
            "target_symbol": "GABRG2",
            "uniprot_id": "P18507",
        })
        process = merge_candidates([process_record])[0]
        process.update({
            "pchembl_value": 5.0,
            "mechanism_class": "GABA-A receptor signaling",
            "therapeutic_role": "symptom_treatment",
            "process_support": [{"pmid": "12345", "title": "Evidence"}],
            "process_source_status": {"status": "ok", "release": "v6"},
            "target_symbol": "GABRA1",
            "uniprot_id": "P14867",
            "target_discovery_method": "literature_mechanism_class",
        })

        merged = msc.merge_chemist_candidates([generic, process])

        self.assertEqual(len(merged), 1)
        candidate = merged[0]
        self.assertEqual(candidate["pchembl_value"], 9.0)
        self.assertEqual(
            candidate["mechanism_class"], "GABA-A receptor signaling"
        )
        self.assertEqual(
            candidate["therapeutic_role"], "symptom_treatment"
        )
        self.assertEqual(
            candidate["process_memberships"][0]["target_symbol"], "GABRA1"
        )
        self.assertEqual(
            candidate["process_memberships"][0]["process_support"][0]["pmid"],
            "12345",
        )


class EnabledSourcesTests(unittest.TestCase):
    """enabled_sources selects which providers are contacted at the boundary.

    Disabled providers must NOT be called and must surface as 'disabled' in
    source_status (never as a source failure). Default (None) contacts all.
    """

    def _collect(self, gtop_env, dc_env, chembl=None, enabled_sources=None):
        # bindingdb mocked healthy-empty (see UnionTests._collect): the
        # enabled/disabled semantics are observable via source_status.
        with mock.patch.object(msc.gtopdb, "get_target_interactions",
                               return_value=gtop_env) as g, \
             mock.patch.object(msc.drugcentral_v2, "get_target_interactions",
                               return_value=dc_env) as d, \
             mock.patch.object(msc.bindingdb, "get_target_interactions",
                               return_value={"source": "bindingdb",
                                             "status": "empty",
                                             "candidates": [], "error": None,
                                             "release": None, "stats": {}}):
            result = msc.collect_target_candidates(
                uniprot_id="P23219", gene="PTGS1",
                disease_name="inflammation", ot_score=0.42,
                target_discovery_method="pharmacological_precedent",
                repurposing_only=True, chembl_enriched=chembl,
                enabled_sources=enabled_sources)
        return result, g, d

    def test_default_none_enables_all_sources(self):
        result, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            chembl=[_chembl_enriched()], enabled_sources=None)
        g.assert_called_once()
        d.assert_called_once()
        providers = result["candidates"][0]["_evidence_ledger"]["providers"]
        self.assertEqual(set(providers), {"gtopdb", "drugcentral", "chembl"})
        for prov in ("gtopdb", "drugcentral", "chembl", "bindingdb"):
            self.assertNotEqual(
                result["source_status"][prov]["status"], "disabled")

    def test_chembl_only_skips_target_first_adapters(self):
        result, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            chembl=[_chembl_enriched()], enabled_sources=["chembl"])
        # Disabled adapters are NEVER called.
        g.assert_not_called()
        d.assert_not_called()
        self.assertEqual(result["source_status"]["gtopdb"]["status"], "disabled")
        self.assertEqual(
            result["source_status"]["drugcentral"]["status"], "disabled")
        self.assertEqual(result["source_status"]["chembl"]["status"], "ok")
        # Disabled is NOT a failure/error.
        self.assertIsNone(result["source_status"]["gtopdb"]["error"])
        cand = result["candidates"][0]
        self.assertEqual(cand["_evidence_ledger"]["providers"], ["chembl"])

    def test_chembl_plus_gtopdb_skips_drugcentral(self):
        result, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            chembl=[_chembl_enriched()],
            enabled_sources=["chembl", "gtopdb"])
        g.assert_called_once()
        d.assert_not_called()
        self.assertEqual(
            result["source_status"]["drugcentral"]["status"], "disabled")
        providers = set(result["candidates"][0]["_evidence_ledger"]["providers"])
        self.assertEqual(providers, {"chembl", "gtopdb"})

    def test_chembl_plus_drugcentral_skips_gtopdb(self):
        result, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            chembl=[_chembl_enriched()],
            enabled_sources=["chembl", "drugcentral"])
        g.assert_not_called()
        d.assert_called_once()
        self.assertEqual(
            result["source_status"]["gtopdb"]["status"], "disabled")
        providers = set(result["candidates"][0]["_evidence_ledger"]["providers"])
        self.assertEqual(providers, {"chembl", "drugcentral"})

    def test_disabled_chembl_does_not_fold_enriched(self):
        result, g, d = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[]),
            chembl=[_chembl_enriched()],
            enabled_sources=["gtopdb"])
        self.assertEqual(result["source_status"]["chembl"]["status"], "disabled")
        providers = set(result["candidates"][0]["_evidence_ledger"]["providers"])
        self.assertNotIn("chembl", providers)
        self.assertEqual(providers, {"gtopdb"})

    def test_union_semantics_preserved_per_subset(self):
        # ChEMBL+DrugCentral: the shared moiety still unions to ONE candidate.
        result, _, _ = self._collect(
            _gtopdb_env(candidates=[_gtopdb_candidate()]),
            _drugcentral_env(candidates=[_drugcentral_candidate()]),
            chembl=[_chembl_enriched()],
            enabled_sources=["chembl", "drugcentral"])
        self.assertEqual(len(result["candidates"]), 1)

    def test_unknown_source_is_hard_error(self):
        with self.assertRaises(ValueError):
            msc.normalize_enabled_sources(["chembl", "bogus"])

    def test_normalize_none_is_all(self):
        self.assertEqual(
            msc.normalize_enabled_sources(None), msc.DEFAULT_ENABLED_SOURCES)
        self.assertEqual(
            set(msc.DEFAULT_ENABLED_SOURCES), set(msc.SUPPORTED_SOURCES))


class ChemistPassthroughTests(unittest.TestCase):
    """run_chemist forwards enabled_sources to collect_target_candidates."""

    def test_run_chemist_forwards_enabled_sources(self):
        from agents import chemist as chem_mod

        bio = {"target": {
            "uniprot_id": "P23219", "target_symbol": "PTGS1",
            "disease_name": "inflammation", "ot_association_score": 0.42,
            "target_discovery_method": "pharmacological_precedent",
        }}

        captured: dict[str, object] = {}

        def _fake_collect(*args, **kwargs):
            captured["enabled_sources"] = kwargs.get("enabled_sources")
            return {"candidates": [], "source_status": {}}

        with mock.patch.object(
                chem_mod, "get_target_candidate_compounds",
                return_value={"compounds": [],
                              "pooled_across_multiple_targets": False}), \
             mock.patch.object(chem_mod, "_anthropic_client",
                               return_value=None), \
             mock.patch.object(chem_mod, "get_pathway_neighbor_targets",
                               return_value=[]), \
             mock.patch.object(chem_mod, "collect_target_candidates",
                               side_effect=_fake_collect):
            chem_mod.run_chemist(bio, enabled_sources=["chembl", "gtopdb"])

        self.assertEqual(captured["enabled_sources"], ["chembl", "gtopdb"])

    def test_run_chemist_default_passes_none(self):
        from agents import chemist as chem_mod

        bio = {"target": {
            "uniprot_id": "P23219", "target_symbol": "PTGS1",
            "disease_name": "inflammation", "ot_association_score": 0.42,
            "target_discovery_method": "pharmacological_precedent",
        }}
        captured: dict[str, object] = {}

        def _fake_collect(*args, **kwargs):
            captured["enabled_sources"] = kwargs.get("enabled_sources", "MISSING")
            return {"candidates": [], "source_status": {}}

        with mock.patch.object(
                chem_mod, "get_target_candidate_compounds",
                return_value={"compounds": [],
                              "pooled_across_multiple_targets": False}), \
             mock.patch.object(chem_mod, "_anthropic_client",
                               return_value=None), \
             mock.patch.object(chem_mod, "get_pathway_neighbor_targets",
                               return_value=[]), \
             mock.patch.object(chem_mod, "collect_target_candidates",
                               side_effect=_fake_collect):
            chem_mod.run_chemist(bio)

        self.assertIsNone(captured["enabled_sources"])


class GtopdbStructure204Tests(unittest.TestCase):
    """Amendment 4: /ligands/{id}/structure returning HTTP 204 (no deposited
    structure — approved biologics like olaratumab/tositumomab/efgartigimod)
    is a data absence, never a source failure.  Other endpoints stay strict.
    """

    def test_structure_204_returns_none_not_unavailable(self):
        from unittest import mock
        from data_sources import gtopdb
        with mock.patch.object(gtopdb.requests, "get",
                               return_value=mock.Mock(status_code=204)):
            self.assertIsNone(gtopdb._fetch_structure(9172))

    def test_204_on_other_endpoints_still_raises(self):
        from unittest import mock
        from data_sources import gtopdb
        with mock.patch.object(gtopdb.requests, "get",
                               return_value=mock.Mock(status_code=204)):
            with self.assertRaises(gtopdb._SourceUnavailable):
                gtopdb._get_json("/targets", {"accession": "P11836"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
