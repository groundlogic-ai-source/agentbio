"""Tests for the BindingDB target-first lane and its multisource wiring.

unittest-only (no pytest in this environment). All HTTP is mocked; the live
contract was verified 2026-08-10 and is documented in data_sources/bindingdb.py.
"""
import unittest
from unittest import mock

from data_sources import bindingdb
from data_sources import drugcentral_local
from data_sources import multisource_candidates as ms


def _payload(rows):
    return {"getLindsByUniprotsResponse": {"affinities": list(rows)}}


def _row(monomer="100", atype="Ki", aff="10.0", smile="AAA",
         pmid="999", doi=""):
    return {"query": "Some kinase", "monomerid": monomer, "smile": smile,
            "affinity_type": atype, "affinity": aff, "pmid": pmid, "doi": doi}


def _resp(status=200, payload=None, json_error=False):
    r = mock.Mock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = payload
    return r


# NOTE: the approved-moiety map is keyed by the lane's moiety key applied to
# BOTH sides (BindingDB row SMILES and snapshot SMILES), so "AAA" and its
# snapshot counterpart "SNAP_AAA" deliberately share one key.
_MOIETY_KEYS = {
    "AAA": "keyA", "SNAP_AAA": "keyA",
    "BBB": "keyB", "EEE": "keyE",
}
_APPROVED_ROWS = [{"struct_id": 42, "name": "Testolone", "smiles": "SNAP_AAA",
                   "inchikey": "AAAAAAAAAAAAAA-XXXXXXXX-Y"}]


class ParseAffinityTests(unittest.TestCase):
    def test_relations(self):
        self.assertEqual(bindingdb.parse_affinity("<1.000"), ("<", 1.0))
        self.assertEqual(bindingdb.parse_affinity(">=50"), (">=", 50.0))
        self.assertEqual(bindingdb.parse_affinity("2.10"), ("=", 2.10))
        self.assertEqual(bindingdb.parse_affinity("= 5"), ("=", 5.0))

    def test_unparseable_rejected(self):
        for bad in ("abc", "", None, "0", "-3", "NaN", "1e999"):
            self.assertIsNone(bindingdb.parse_affinity(bad), bad)

    def test_pchembl_conversion(self):
        self.assertEqual(bindingdb.pchembl_from_nm(100.0), 7.0)
        self.assertEqual(bindingdb.pchembl_from_nm(1.0), 9.0)


class LaneTests(unittest.TestCase):
    def _mocks(self, rows, approved=_APPROVED_ROWS):
        return (
            mock.patch.object(bindingdb.requests, "get",
                              return_value=_resp(payload=_payload(rows))),
            mock.patch.object(bindingdb, "cache_get", return_value=None),
            mock.patch.object(bindingdb, "cache_set"),
            mock.patch.object(bindingdb, "_moiety_key",
                              side_effect=lambda s: _MOIETY_KEYS.get(s, "")),
            mock.patch.object(drugcentral_local,
                              "approved_moiety_rows",
                              return_value=approved),
        )

    def test_happy_path_filters_to_approved(self):
        rows = [
            _row(monomer="100", atype="Ki", aff="<10.0", smile="AAA"),
            _row(monomer="101", atype="IC50", aff="500", smile="BBB"),
            _row(monomer="102", atype="kon", aff="5", smile="CCC"),
            _row(monomer="103", atype="Ki", aff="abc", smile="DDD"),
        ]
        http, cget, cset, ik, appr = self._mocks(rows)
        with http, cget, cset as cset_m, ik, appr:
            env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "ok")
        self.assertEqual(len(env["candidates"]), 1)
        cand = env["candidates"][0]
        self.assertEqual(cand["provider_molecule_id"], "BDBM100")
        self.assertEqual(cand["relation"], "<")
        self.assertEqual(cand["affinity_nm"], 10.0)
        self.assertEqual(cand["pchembl"], 8.0)
        self.assertEqual(cand["approved_struct_id"], 42)
        self.assertEqual(cand["name"], "Testolone")
        # The kept row borrows the snapshot's real InChIKey (this RDKit build
        # has no InChI support) so cross-lane union keys on structural identity.
        self.assertEqual(cand["inchikey"], "AAAAAAAAAAAAAA-XXXXXXXX-Y")
        self.assertEqual(cand["pmid"], "999")
        self.assertEqual(env["stats"]["returned"], 4)
        self.assertEqual(env["stats"]["parsed"], 1)
        self.assertEqual(env["stats"]["skipped_unapproved"], 1)
        self.assertEqual(env["stats"]["skipped_affinity"], 2)
        cset_m.assert_called_once()

    def test_repurposing_off_keeps_unapproved_and_skips_snapshot(self):
        rows = [_row(monomer="101", atype="IC50", aff="500", smile="BBB")]
        http, cget, cset, ik, appr = self._mocks(rows)
        with http, cget, cset, ik, appr as appr_m:
            env = bindingdb.get_target_interactions(
                "P00519", repurposing_only=False)
        appr_m.assert_not_called()
        self.assertEqual(env["status"], "ok")
        self.assertIsNone(env["candidates"][0]["approved_struct_id"])

    def test_empty_is_healthy_and_cached(self):
        http, cget, cset, ik, appr = self._mocks([])
        with http, cget, cset as cset_m, ik, appr:
            env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "empty")
        self.assertEqual(env["candidates"], [])
        cset_m.assert_called_once()

    def test_transient_http_unavailable_and_never_cached(self):
        http, cget, cset, ik, appr = self._mocks([])
        with mock.patch.object(bindingdb.requests, "get",
                               return_value=_resp(status=503)):
            with cget, cset as cset_m, ik, appr:
                env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "unavailable")
        self.assertIn("503", env["error"])
        cset_m.assert_not_called()

    def test_malformed_payload_unavailable_and_never_cached(self):
        for bad in (_resp(payload={"unexpected": True}),
                    _resp(json_error=True)):
            http, cget, cset, ik, appr = self._mocks([])
            with mock.patch.object(bindingdb.requests, "get",
                                   return_value=bad):
                with cget, cset as cset_m, ik, appr:
                    env = bindingdb.get_target_interactions("P00519")
            self.assertEqual(env["status"], "unavailable")
            cset_m.assert_not_called()

    def test_corrected_response_key_accepted(self):
        # If the provider ever fixes the "Linds" typo, the lane must not break.
        http, cget, cset, ik, appr = self._mocks([])
        fixed = {"getLigandsByUniprotsResponse": {"affinities": []}}
        with mock.patch.object(bindingdb.requests, "get",
                               return_value=_resp(payload=fixed)):
            with cget, cset, ik, appr:
                env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "empty")

    def test_missing_snapshot_fails_visibly(self):
        rows = [_row()]
        http, cget, cset, ik, _ = self._mocks(rows)
        with http, cget, cset as cset_m, ik:
            with mock.patch.object(
                    drugcentral_local, "approved_moiety_rows",
                    side_effect=drugcentral_local.SnapshotCorrupt("gone")):
                env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "unavailable")
        self.assertIn("gone", env["error"])
        cset_m.assert_not_called()

    def test_malformed_rows_unavailable_and_never_cached(self):
        # A non-empty affinities list where NO row matches the verified row
        # contract is a malformed payload, not a genuine "no binders".
        http, cget, cset, ik, appr = self._mocks(["garbage", {"foo": 1}])
        with http, cget, cset as cset_m, ik, appr:
            env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "unavailable")
        self.assertIn("row contract", env["error"])
        cset_m.assert_not_called()

    def test_standardization_failure_unavailable_and_never_cached(self):
        http, cget, cset, _, appr = self._mocks([_row()])
        with http, cget, cset as cset_m, appr:
            with mock.patch.object(
                    bindingdb, "_moiety_key",
                    side_effect=bindingdb._SourceUnavailable("rdkit broke")):
                env = bindingdb.get_target_interactions("P00519")
        self.assertEqual(env["status"], "unavailable")
        self.assertIn("rdkit broke", env["error"])
        cset_m.assert_not_called()

    def test_moiety_key_raises_on_standardization_failure(self):
        # Parse-succeeds / standardize-fails must raise, never return ''.
        with mock.patch(
                "rdkit.Chem.MolStandardize.rdMolStandardize.FragmentParent",
                side_effect=RuntimeError("boom")):
            with self.assertRaises(bindingdb._SourceUnavailable):
                bindingdb._moiety_key("CCO")
        # A genuinely unparseable SMILES still returns '' (per-row skip).
        self.assertEqual(bindingdb._moiety_key("not-a-smiles"), "")

    def test_cache_hit_skips_network(self):
        cached = {"status": "ok", "candidates": [{"monomer_id": "1"}],
                  "error": None, "release": None, "stats": {}}
        with mock.patch.object(bindingdb, "cache_get", return_value=cached):
            with mock.patch.object(bindingdb.requests, "get") as http_m:
                env = bindingdb.get_target_interactions("P00519")
        http_m.assert_not_called()
        self.assertIs(env, cached)

    def test_no_accession_is_explicit_error(self):
        env = bindingdb.get_target_interactions("")
        self.assertEqual(env["status"], "unavailable")
        self.assertIn("no uniprot_id", env["error"])


class ConverterTests(unittest.TestCase):
    def _envelope(self, struct_id=42):
        return {"source": "bindingdb", "status": "ok", "error": None,
                "release": None, "candidates": [{
                    "monomer_id": "100",
                    "provider_molecule_id": "BDBM100",
                    "name": "Testolone",
                    "smiles": "AAA",
                    "inchikey": "AAAAAAAAAAAAAA-XXXXXXXX-Y",
                    "affinity_type": "Ki",
                    "affinity_nm": 10.0,
                    "relation": "<",
                    "pchembl": 8.0,
                    "pmid": "999",
                    "doi": "",
                    "query_target": "Some kinase",
                    "approved_struct_id": struct_id,
                }]}

    def test_records_shape(self):
        recs = ms.records_from_bindingdb_envelope(
            self._envelope(), uniprot_id="P00519", gene="ABL1",
            disease_name="CML")
        self.assertEqual(len(recs), 2)
        bio = next(r for r in recs if r.measurement_type == "pchembl")
        self.assertEqual(bio.provider, "bindingdb")
        self.assertEqual(bio.source_id, "bindingdb:100:Ki:999")
        self.assertEqual(bio.measurement_value, 8.0)
        self.assertEqual(bio.publication_id, "999")
        self.assertEqual(bio.target_species, "")  # never asserted human
        appr = next(r for r in recs if r.measurement_type == "phase")
        self.assertEqual(appr.provider, "drugcentral")
        self.assertEqual(appr.source_id, "drugcentral-approval:42")
        self.assertEqual(appr.measurement_value, 4.0)

    def test_unapproved_row_gets_no_approval_record(self):
        recs = ms.records_from_bindingdb_envelope(
            self._envelope(struct_id=None), uniprot_id="P00519", gene="ABL1")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].measurement_type, "pchembl")

    def test_ot_score_attaches_genetic_link(self):
        recs = ms.records_from_bindingdb_envelope(
            self._envelope(), uniprot_id="P00519", gene="ABL1",
            disease_name="CML", ot_score=0.5)
        kinds = {r.source_type for r in recs}
        self.assertIn(ms.SourceType.GENETIC_ASSOCIATION, kinds)

    def test_non_dict_envelope_yields_nothing(self):
        self.assertEqual(ms.records_from_bindingdb_envelope(None), [])


class WiringTests(unittest.TestCase):
    def test_bindingdb_supported_and_unknown_rejected(self):
        self.assertIn("bindingdb", ms.SUPPORTED_SOURCES)
        self.assertEqual(ms.normalize_enabled_sources([" BindingDB "]),
                         frozenset({"bindingdb"}))
        with self.assertRaises(ValueError):
            ms.normalize_enabled_sources(["bindingdb", "nonsense"])

    def test_disabled_lane_is_never_called(self):
        with mock.patch.object(bindingdb, "get_target_interactions") as lane:
            out = ms.collect_target_candidates(
                uniprot_id="P00519", gene="ABL1", disease_name="CML",
                ot_score=None, target_discovery_method="",
                repurposing_only=True, enabled_sources=["chembl"])
        lane.assert_not_called()
        self.assertEqual(out["source_status"]["bindingdb"]["status"],
                         "disabled")

    def test_enabled_lane_merges_approved_candidate(self):
        env = ConverterTests()._envelope()
        with mock.patch.object(bindingdb, "get_target_interactions",
                               return_value=env):
            out = ms.collect_target_candidates(
                uniprot_id="P00519", gene="ABL1", disease_name="CML",
                ot_score=None, target_discovery_method="",
                repurposing_only=True, enabled_sources=["bindingdb"])
        self.assertEqual(out["source_status"]["bindingdb"]["status"], "ok")
        self.assertEqual(len(out["candidates"]), 1)
        cand = out["candidates"][0]
        self.assertEqual(cand["drug_name"], "Testolone")
        self.assertTrue(cand["is_approved_drug"])
        self.assertEqual(cand["pchembl_value"], 8.0)
        self.assertIn("bindingdb",
                      cand["_evidence_ledger"]["providers"])
        # Approval evidence collapses onto the DrugCentral lineage anchor.
        approval_srcs = {r["source_id"] for r in
                         cand["_evidence_ledger"]["records"]
                         if r["measurement_type"] == "phase"}
        self.assertEqual(approval_srcs, {"drugcentral-approval:42"})


if __name__ == "__main__":
    unittest.main()
