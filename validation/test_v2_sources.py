"""Unit tests for the v2 target-first source adapters (no network required).

Covers, for both data_sources/gtopdb.py and data_sources/drugcentral_v2.py:
  - normalization of a healthy candidate row (provider IDs, structure,
    action/type, affinity/activity, target identity, refs, evidence);
  - degraded/transient behaviour: 5xx and timeouts → 'unavailable', NOT cached;
  - malformed payload (wrong JSON shape) → 'unavailable', NOT cached;
  - healthy-empty → cacheable;
  - dedup of a drug hitting multiple targets / multiple activity rows;
  - exact endpoint routes actually requested.

All requests are mocked; nothing touches the network.

Run: python3 -m unittest validation.test_v2_sources
"""

import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

import requests  # noqa: E402
from cache.cache import make_key, _get_conn  # noqa: E402
from data_sources import gtopdb  # noqa: E402
from data_sources import drugcentral_v2 as dc  # noqa: E402


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #
def _has(key: str) -> bool:
    return _get_conn().execute(
        "SELECT 1 FROM cache WHERE key=?", (key,)).fetchone() is not None


def _purge(*keys: str) -> None:
    conn = _get_conn()
    for k in keys:
        conn.execute("DELETE FROM cache WHERE key=?", (k,))
    conn.commit()


# --------------------------------------------------------------------------- #
# Fake HTTP response + a routing FakeSession
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status_code=200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no JSON object could be decoded")
        return self._json_data


def _make_requests_get(routes, recorder=None):
    """Return a fake requests.get that maps a URL path to a _FakeResp.

    `routes` maps the URL path (everything after the base) to either a
    _FakeResp or an Exception instance (which is raised, simulating a timeout /
    connection error). `recorder` (if given) collects each requested path.
    """
    def _fake_get(url, params=None, headers=None, timeout=None):
        # Strip whichever base is in play.
        path = url
        for base in (gtopdb.BASE_URL, dc.BASE_URL):
            if path.startswith(base):
                path = path[len(base):]
                break
        if recorder is not None:
            recorder.append((path, params))
        if path not in routes:
            raise AssertionError(f"unexpected URL path requested: {path!r}")
        result = routes[path]
        if isinstance(result, Exception):
            raise result
        return result
    return _fake_get


# =========================================================================== #
# GtoPdb
# =========================================================================== #
class TestGtopdb(unittest.TestCase):
    UID = "P_FAKE_GTOPDB"

    def setUp(self):
        self.key = make_key(
            f"gtopdb_get_target_interactions_{gtopdb._CACHE_VERSION}",
            self.UID, True)
        _purge(self.key)

    def tearDown(self):
        _purge(self.key)

    # ---- healthy normalization ------------------------------------------- #
    def _healthy_routes(self):
        return {
            "/targets": _FakeResp(json_data=[{"targetId": 2019,
                                              "name": "ERBB2"}]),
            "/targets/2019/interactions": _FakeResp(json_data=[{
                "interactionId": 78745,
                "targetId": 2019,
                "targetName": "erb-b2 receptor tyrosine kinase 2",
                "targetSpecies": "Human",
                "primaryTarget": True,
                "ligandId": 5692,
                "ligandName": "lapatinib",
                "endogenous": False,
                "type": "Inhibitor",
                "action": "Inhibition",
                "selectivity": "Not Determined",
                "affinity": "8.0",
                "affinityParameter": "pIC50",
                "originalAffinity": "9.8x10-9",
                "originalAffinityType": "IC50",
                "refs": [{"referenceId": 22941, "pmid": 12467226,
                          "title": "Mol Cancer Ther", "year": 2001}],
            }]),
            "/ligands/5692": _FakeResp(json_data={
                "ligandId": 5692, "name": "lapatinib", "type": "Synthetic organic",
                "inn": "lapatinib", "approved": True, "withdrawn": False}),
            "/ligands/5692/structure": _FakeResp(json_data={
                "ligandId": 5692, "ligandName": "lapatinib",
                "iupacName": "N-{3-chloro...}",
                "smiles": "CS(=O)(=O)CCNCc1ccc(o1)-c1ccc2ncnc(Nc3ccc(OCc4cccc(F)c4)c(Cl)c3)c2c1",
                "inchi": "InChI=1S/C29H26ClFN4O4S/...",
                "inchiKey": "BCFGMOOMADDAQU-UHFFFAOYSA-N"}),
            "/ligands/5692/databaseLinks": _FakeResp(json_data=[
                {"accession": "CHEMBL554", "database": "ChEMBL Ligand"},
                {"accession": "DB01259", "database": "DrugBank Ligand"},
            ]),
        }

    def test_normalization(self):
        recorder = []
        fake_get = _make_requests_get(self._healthy_routes(), recorder)
        with mock.patch.object(gtopdb.requests, "get", fake_get):
            res = gtopdb.get_target_interactions(self.UID)

        self.assertEqual(res["source"], "gtopdb")
        self.assertEqual(res["status"], "ok")
        self.assertIsNone(res["error"])
        self.assertEqual(len(res["candidates"]), 1)
        c = res["candidates"][0]
        # provider IDs
        self.assertEqual(c["provider_ligand_id"], 5692)
        self.assertEqual(c["provider_interaction_id"], 78745)
        self.assertEqual(c["chembl_id"], "CHEMBL554")
        self.assertEqual(c["drugbank_id"], "DB01259")
        # identity + structure
        self.assertEqual(c["name"], "lapatinib")
        self.assertEqual(c["inchikey"], "BCFGMOOMADDAQU-UHFFFAOYSA-N")
        self.assertTrue(c["smiles"])
        # action/type + affinity (coerced to float)
        self.assertEqual(c["action"], "Inhibition")
        self.assertEqual(c["action_type"], "Inhibitor")
        self.assertEqual(c["affinity"], 8.0)
        self.assertEqual(c["affinity_parameter"], "pIC50")
        # target identity
        self.assertEqual(c["target_id"], 2019)
        self.assertEqual(c["target_species"], "Human")
        # refs + evidence
        self.assertEqual(c["refs"][0]["pmid"], "12467226")
        self.assertEqual(c["evidence"][0]["type"], "gtopdb_interaction")
        self.assertEqual(c["evidence"][0]["interaction_id"], 78745)
        # cached (healthy)
        self.assertTrue(_has(self.key))

    def test_exact_endpoint_routes(self):
        recorder = []
        fake_get = _make_requests_get(self._healthy_routes(), recorder)
        with mock.patch.object(gtopdb.requests, "get", fake_get):
            gtopdb.get_target_interactions(self.UID)
        paths = [p for p, _ in recorder]
        self.assertIn("/targets", paths)
        self.assertIn("/targets/2019/interactions", paths)
        self.assertIn("/ligands/5692", paths)
        self.assertIn("/ligands/5692/structure", paths)
        self.assertIn("/ligands/5692/databaseLinks", paths)
        # /targets called with the accession param
        targets_call = next(pr for p, pr in recorder if p == "/targets")
        self.assertEqual(targets_call.get("accession"), self.UID)
        # interactions call carries the approved+species filter
        inter_call = next(pr for p, pr in recorder
                          if p == "/targets/2019/interactions")
        self.assertEqual(inter_call.get("species"), "Human")
        self.assertEqual(inter_call.get("approved"), "true")

    # ---- degraded / transient -------------------------------------------- #
    def test_5xx_unavailable_not_cached(self):
        routes = {"/targets": _FakeResp(status_code=503)}
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertIsNotNone(res["error"])
        self.assertEqual(res["candidates"], [])
        self.assertFalse(_has(self.key), "unavailable must not be cached")

    def test_timeout_unavailable_not_cached(self):
        routes = {"/targets": requests.exceptions.Timeout("timed out")}
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    def test_malformed_payload_not_cached(self):
        # /targets returns a dict instead of the contract's list → malformed.
        routes = {"/targets": _FakeResp(json_data={"unexpected": "shape"})}
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key),
                         "malformed payload must not poison the cache")

    def test_non_json_body_not_cached(self):
        routes = {"/targets": _FakeResp(raise_json=True)}
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    # ---- healthy empty ---------------------------------------------------- #
    def test_target_no_match_empty_cached(self):
        routes = {"/targets": _FakeResp(json_data=[])}
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["candidates"], [])
        self.assertTrue(_has(self.key), "healthy empty may cache")

    def test_resolved_but_no_interactions_empty_cached(self):
        routes = {
            "/targets": _FakeResp(json_data=[{"targetId": 999}]),
            "/targets/999/interactions": _FakeResp(json_data=[]),
        }
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "empty")
        self.assertTrue(_has(self.key))

    # ---- dedup across multiple targets ----------------------------------- #
    def test_dedup_same_ligand_multiple_targets(self):
        interaction_t1 = {
            "interactionId": 1, "targetId": 111, "targetSpecies": "Human",
            "ligandId": 5692, "ligandName": "lapatinib", "type": "Inhibitor",
            "action": "Inhibition", "affinity": "8.0",
            "affinityParameter": "pIC50", "refs": []}
        interaction_t2 = {
            "interactionId": 2, "targetId": 222, "targetSpecies": "Human",
            "ligandId": 5692, "ligandName": "lapatinib", "type": "Inhibitor",
            "action": "Inhibition", "affinity": "7.5",
            "affinityParameter": "pIC50", "refs": []}
        routes = {
            "/targets": _FakeResp(json_data=[{"targetId": 111},
                                            {"targetId": 222}]),
            "/targets/111/interactions": _FakeResp(json_data=[interaction_t1]),
            "/targets/222/interactions": _FakeResp(json_data=[interaction_t2]),
            "/ligands/5692": _FakeResp(json_data={
                "ligandId": 5692, "name": "lapatinib", "approved": True}),
            "/ligands/5692/structure": _FakeResp(json_data={
                "smiles": "CC", "inchiKey": "KEY"}),
            "/ligands/5692/databaseLinks": _FakeResp(json_data=[]),
        }
        recorder = []
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes, recorder)):
            res = gtopdb.get_target_interactions(self.UID)
        # One candidate despite two targets.
        self.assertEqual(len(res["candidates"]), 1)
        c = res["candidates"][0]
        # Both target hits recorded as evidence.
        self.assertEqual(len(c["evidence"]), 2)
        self.assertEqual({e["target_id"] for e in c["evidence"]}, {111, 222})
        # Ligand payloads fetched exactly once (dedup avoids re-fetch).
        self.assertEqual(sum(1 for p, _ in recorder if p == "/ligands/5692"), 1)

    def test_unapproved_ligand_dropped(self):
        routes = {
            "/targets": _FakeResp(json_data=[{"targetId": 5}]),
            "/targets/5/interactions": _FakeResp(json_data=[{
                "interactionId": 9, "targetId": 5, "targetSpecies": "Human",
                "ligandId": 42, "ligandName": "tool_cpd", "type": "Inhibitor",
                "action": "Inhibition", "refs": []}]),
            "/ligands/42": _FakeResp(json_data={
                "ligandId": 42, "name": "tool_cpd", "approved": False}),
        }
        with mock.patch.object(gtopdb.requests, "get",
                               _make_requests_get(routes)):
            res = gtopdb.get_target_interactions(self.UID, approved_only=True)
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["candidates"], [])


# =========================================================================== #
# DrugCentral v2
# =========================================================================== #
class TestDrugCentral(unittest.TestCase):
    UID = "P_FAKE_DC"
    GENE = "GSK3B"

    def setUp(self):
        # These tests pin LIVE-API-lane behavior (timeouts, 500s, payload
        # validation). Force that lane even when the Amendment-6 local
        # snapshot is present in the checkout.
        self._lane = mock.patch.object(dc, "_use_local_lane",
                                       return_value=False)
        self._lane.start()
        self.key = make_key(
            f"drugcentral_get_target_interactions_{dc._CACHE_VERSION}",
            self.UID, None)
        self.key_gene = make_key(
            f"drugcentral_get_target_interactions_{dc._CACHE_VERSION}",
            self.UID, self.GENE)
        _purge(self.key, self.key_gene)

    def tearDown(self):
        self._lane.stop()
        _purge(self.key, self.key_gene)

    def _act_row(self, struct_id=1548, act_id=100, act_value=8.0,
                 organism="Homo sapiens"):
        return {
            "gene": "ERBB2", "accession": self.UID, "swissprot": "ERBB2_HUMAN",
            "target_name": "Receptor tyrosine-protein kinase erbB-2",
            "target_id": 884, "target_class": "Kinase",
            "act_value": act_value, "act_type": "IC50", "act_unit": None,
            "relation": "=", "action_type": None, "moa": None,
            "moa_source": None, "act_source": "CHEMBL",
            "act_comment": "Inhibition of ...", "tdl": "Tclin",
            "first_in_class": None, "struct_id": struct_id, "act_id": act_id,
            "organism": organism,
        }

    def _struct_row(self, struct_id=1548, status="OFP", name="lapatinib"):
        return {
            "id": struct_id, "name": name, "status": status,
            "smiles": "CS(=O)(=O)CCNCc1ccc(o1)...",
            "inchi": "InChI=1S/...", "inchikey": "BCFGMOOMADDAQU-UHFFFAOYSA-N",
            "cd_molweight": 581.06, "cas_reg_no": "231277-92-2",
        }

    # ---- healthy normalization ------------------------------------------- #
    def test_normalization(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data=[self._act_row()]),
            "/structures/id/1548": _FakeResp(json_data=[self._struct_row()]),
        }
        recorder = []
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes, recorder)):
            res = dc.get_target_interactions(self.UID)

        self.assertEqual(res["source"], "drugcentral")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["candidates"]), 1)
        c = res["candidates"][0]
        self.assertEqual(c["struct_id"], 1548)
        self.assertEqual(c["provider_act_id"], 100)
        self.assertEqual(c["name"], "lapatinib")
        self.assertEqual(c["structure_status"], "OFP")
        self.assertEqual(c["inchikey"], "BCFGMOOMADDAQU-UHFFFAOYSA-N")
        self.assertTrue(c["smiles"])
        # source activity / assay / lineage preserved
        self.assertEqual(c["act_type"], "IC50")
        self.assertEqual(c["act_value"], 8.0)
        self.assertEqual(c["act_source"], "CHEMBL")
        self.assertEqual(c["tdl"], "Tclin")
        self.assertEqual(c["target_class"], "Kinase")
        # target identity
        self.assertEqual(c["accession"], self.UID)
        self.assertEqual(c["organism"], "Homo sapiens")
        # evidence
        self.assertEqual(c["evidence"][0]["type"], "drugcentral_activity")
        self.assertEqual(c["evidence"][0]["act_id"], 100)
        self.assertTrue(_has(self.key))

    def test_exact_endpoint_routes(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data=[self._act_row()]),
            "/structures/id/1548": _FakeResp(json_data=[self._struct_row()]),
        }
        recorder = []
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes, recorder)):
            dc.get_target_interactions(self.UID)
        paths = [p for p, _ in recorder]
        self.assertIn(f"/act_table_full/accession/{self.UID}", paths)
        self.assertIn("/structures/id/1548", paths)

    # ---- Homo sapiens + established-product filters ----------------------- #
    def test_non_human_dropped(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data=[self._act_row(organism="Rattus norvegicus")]),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["candidates"], [])
        self.assertTrue(_has(self.key), "healthy empty (filtered) may cache")

    def test_non_established_status_dropped(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data=[self._act_row()]),
            "/structures/id/1548":
                _FakeResp(json_data=[self._struct_row(status="ONP")]),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["candidates"], [])

    def test_ofm_status_kept(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data=[self._act_row()]),
            "/structures/id/1548":
                _FakeResp(json_data=[self._struct_row(status="OFM")]),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["candidates"][0]["structure_status"], "OFM")

    # ---- dedup across activity rows -------------------------------------- #
    def test_dedup_same_struct_multiple_activities(self):
        rows = [self._act_row(act_id=100, act_value=8.0),
                self._act_row(act_id=101, act_value=7.2)]
        routes = {
            f"/act_table_full/accession/{self.UID}": _FakeResp(json_data=rows),
            "/structures/id/1548": _FakeResp(json_data=[self._struct_row()]),
        }
        recorder = []
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes, recorder)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(len(res["candidates"]), 1)
        # Both activities recorded as evidence.
        self.assertEqual(len(res["candidates"][0]["evidence"]), 2)
        # Structure resolved exactly once (memoised).
        self.assertEqual(
            sum(1 for p, _ in recorder if p == "/structures/id/1548"), 1)

    # ---- gene fallback on accession 500 ---------------------------------- #
    def test_gene_fallback_on_accession_500(self):
        routes = {
            f"/act_table_full/accession/{self.UID}": _FakeResp(status_code=500),
            f"/act_table_full/gene/{self.GENE}":
                _FakeResp(json_data=[self._act_row()]),
            "/structures/id/1548": _FakeResp(json_data=[self._struct_row()]),
        }
        recorder = []
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes, recorder)):
            res = dc.get_target_interactions(self.UID, gene=self.GENE)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["candidates"]), 1)
        paths = [p for p, _ in recorder]
        self.assertIn(f"/act_table_full/gene/{self.GENE}", paths)
        self.assertTrue(_has(self.key_gene))

    def test_accession_500_without_gene_is_unavailable(self):
        routes = {
            f"/act_table_full/accession/{self.UID}": _FakeResp(status_code=500),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    # ---- degraded / transient / malformed -------------------------------- #
    def test_503_unavailable_not_cached(self):
        routes = {
            f"/act_table_full/accession/{self.UID}": _FakeResp(status_code=503),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    def test_timeout_unavailable_not_cached(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                requests.exceptions.ConnectionError("conn reset"),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    def test_malformed_payload_not_cached(self):
        routes = {
            f"/act_table_full/accession/{self.UID}":
                _FakeResp(json_data={"unexpected": "dict"}),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(self.key))

    def test_no_match_empty_cached(self):
        routes = {
            f"/act_table_full/accession/{self.UID}": _FakeResp(json_data=[]),
        }
        with mock.patch.object(dc.requests, "get",
                               _make_requests_get(routes)):
            res = dc.get_target_interactions(self.UID)
        self.assertEqual(res["status"], "empty")
        self.assertTrue(_has(self.key))


if __name__ == "__main__":
    unittest.main()
