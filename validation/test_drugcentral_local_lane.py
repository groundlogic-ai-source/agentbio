"""Amendment 6: DrugCentral local-lane unit + conformance tests.

Unit tests build a synthetic snapshot and pin the DRS API's exact query
semantics (trim + case-insensitive substring for accession/gene, exact int
match for structures/id, 404 → None). The conformance test replays every
DrugCentral-enabled control target through the local lane and requires the
recorded ok/empty status to reproduce exactly — this is the evidence cited by
Amendment 6 for blessing the fingerprint transition instead of re-running
the control.

unittest-only (no pytest in this env): run via
    python3 -m unittest validation.test_drugcentral_local_lane -v
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from data_sources import drugcentral_local, drugcentral_v2

CONTROL = "validation/v2_source_ablation_results.json"

_ACT_DDL = ("CREATE TABLE act_table_full (act_id INTEGER, struct_id INTEGER, "
            "act_type TEXT, act_value REAL, act_unit TEXT, relation TEXT, "
            "act_source TEXT, act_comment TEXT, moa TEXT, moa_source TEXT, "
            "action_type TEXT, target_id INTEGER, target_class TEXT, "
            "tdl TEXT, first_in_class INTEGER, gene TEXT, accession TEXT, "
            "swissprot TEXT, target_name TEXT, organism TEXT)")
_STRUCT_DDL = ("CREATE TABLE structures (id INTEGER, name TEXT, status TEXT, "
               "smiles TEXT, inchi TEXT, inchikey TEXT, cd_molweight REAL, "
               "cas_reg_no TEXT)")


def _act_row(act_id=1, struct_id=10, accession="P08183", gene="ATP2A1",
             organism="Homo sapiens", act_value=5.0, moa=1, tdl="Tchem"):
    return (act_id, struct_id, "IC50", act_value, "nM", "=", "DrugCentral",
            None, moa, "DrugCentral", "inhibitor", 100, "Enzyme", tdl, 0,
            gene, accession, "P08183", "SERCA", organism)


def _struct_row(sid=10, name="THAPSIGARGIN", status="OFP"):
    return (sid, name, status, "C=C", "InChI=1S", "AAAA-AAAA-N", 650.8, "67526-95-8")


def _write_snapshot(path, act_rows, struct_rows):
    conn = sqlite3.connect(path)
    conn.execute(_ACT_DDL)
    conn.execute(_STRUCT_DDL)
    conn.executemany("INSERT INTO act_table_full VALUES (%s)"
                     % ",".join("?" * 20), act_rows)
    conn.executemany("INSERT INTO structures VALUES (%s)"
                     % ",".join("?" * 8), struct_rows)
    conn.commit()
    conn.close()


class LocalLaneSemanticsTest(unittest.TestCase):
    """DRS semantics reproduced exactly on a synthetic snapshot."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.snap = os.path.join(self._td.name, "snap.sqlite")
        _write_snapshot(
            self.snap,
            [_act_row(act_id=1, struct_id=10, accession="P08183 "),
             _act_row(act_id=2, struct_id=11, accession="XP08183X",
                      gene="OTHER"),
             _act_row(act_id=3, struct_id=12, accession="Q99999",
                      gene="ATP2A1"),
             _act_row(act_id=4, struct_id=13, accession="P08184",
                      gene="ATP2A2", organism="Mus musculus")],
            [_struct_row(10, "THAPSIGARGIN", "OFP"),
             _struct_row(11, "XP08183X DRUG", "OFM"),
             _struct_row(12, "Q99999 DRUG", "ONP"),
             _struct_row(13, "MOUSE ONLY", "OFP")])
        self._patch = mock.patch.object(drugcentral_local, "SNAPSHOT_PATH",
                                        self.snap)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def test_accession_substring_trim_case_insensitive(self):
        rows = drugcentral_local.get_json("/act_table_full/accession/p08183")
        self.assertIsNotNone(rows)
        ids = {r["act_id"] for r in rows}
        # trim: trailing-space row matches; substring: XP08183X matches;
        # case: lowercase query matches uppercase stored value.
        self.assertEqual(ids, {1, 2})

    def test_gene_route(self):
        rows = drugcentral_local.get_json("/act_table_full/gene/atp2a1")
        self.assertIsNotNone(rows)
        self.assertEqual({r["act_id"] for r in rows}, {1, 3})

    def test_no_rows_maps_to_none_like_404(self):
        self.assertIsNone(
            drugcentral_local.get_json("/act_table_full/accession/ZZZZZZ"))
        self.assertIsNone(drugcentral_local.get_json("/structures/id/999"))

    def test_structure_exact_id_returns_list(self):
        rows = drugcentral_local.get_json("/structures/id/10")
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "THAPSIGARGIN")
        self.assertEqual(rows[0]["status"], "OFP")

    def test_unrouted_path_is_explicit_failure(self):
        with self.assertRaises(drugcentral_local.SnapshotCorrupt):
            drugcentral_local.get_json("/act_table_full/act_id/1")

    def test_concurrent_queries_are_thread_safe(self):
        results, errors = [], []

        def work():
            try:
                results.append(
                    drugcentral_local.get_json(
                        "/act_table_full/accession/P08183"))
            except Exception as e:  # noqa: BLE001 - collect, assert below
                errors.append(e)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(len(results), 8)


class LocalLaneIntegrationTest(unittest.TestCase):
    """End-to-end through drugcentral_v2 with the cache neutralised."""

    def test_get_target_interactions_filters_and_normalises(self):
        with tempfile.TemporaryDirectory() as td:
            snap = os.path.join(td, "snap.sqlite")
            _write_snapshot(
                snap,
                [_act_row(act_id=1, struct_id=10, accession="P08183"),
                 _act_row(act_id=2, struct_id=12, accession="P08183"),
                 _act_row(act_id=3, struct_id=13, accession="P08183",
                          organism="Mus musculus")],
                [_struct_row(10, "THAPSIGARGIN", "OFP"),
                 _struct_row(12, "EXPERIMENTAL", "ONP"),
                 _struct_row(13, "MOUSE ONLY", "OFP")])
            with mock.patch.object(drugcentral_local, "SNAPSHOT_PATH", snap), \
                    mock.patch.object(drugcentral_v2, "get", return_value=None), \
                    mock.patch.object(drugcentral_v2, "cache_set",
                                      lambda *a, **k: None):
                env = drugcentral_v2.get_target_interactions("P08183",
                                                             gene="ATP2A1")
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["source"], "drugcentral")
        # Only the Homo sapiens + established-product (OFP) row survives.
        self.assertEqual(len(env["candidates"]), 1)
        cand = env["candidates"][0]
        self.assertEqual(cand["name"], "THAPSIGARGIN")
        self.assertEqual(cand["structure_status"], "OFP")
        self.assertEqual(cand["act_value"], 5.0)
        self.assertEqual(cand["evidence"][0]["type"], "drugcentral_activity")

    def test_empty_when_nothing_survives_filters(self):
        with tempfile.TemporaryDirectory() as td:
            snap = os.path.join(td, "snap.sqlite")
            _write_snapshot(snap,
                            [_act_row(struct_id=12)],
                            [_struct_row(12, "EXPERIMENTAL", "ONP")])
            with mock.patch.object(drugcentral_local, "SNAPSHOT_PATH", snap), \
                    mock.patch.object(drugcentral_v2, "get", return_value=None), \
                    mock.patch.object(drugcentral_v2, "cache_set",
                                      lambda *a, **k: None):
                env = drugcentral_v2.get_target_interactions("P08183")
        self.assertEqual(env["status"], "empty")

    def test_corrupt_snapshot_raises_unavailable_not_silent_live_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            snap = os.path.join(td, "snap.sqlite")
            with open(snap, "wb") as f:
                f.write(b"not a sqlite database")
            with mock.patch.object(drugcentral_local, "SNAPSHOT_PATH", snap), \
                    mock.patch.object(drugcentral_v2, "get", return_value=None), \
                    mock.patch.object(drugcentral_v2, "cache_set",
                                      lambda *a, **k: None):
                env = drugcentral_v2.get_target_interactions("P08183")
        self.assertEqual(env["status"], "unavailable")


@unittest.skipUnless(drugcentral_local.available(),
                     "Amendment 6 snapshot not built yet")
class ControlConformanceTest(unittest.TestCase):
    """Every DrugCentral-enabled control target must reproduce its recorded
    ok/empty status through the local lane. Any mismatch blocks the blessing."""

    def test_statuses_match_recorded_control(self):
        with open(CONTROL, encoding="utf-8") as f:
            payload = json.load(f)
        mem_cache: dict = {}
        mismatches = []
        checked = 0
        with mock.patch.object(drugcentral_v2, "get",
                               side_effect=lambda k: mem_cache.get(k)), \
                mock.patch.object(drugcentral_v2, "cache_set",
                                  side_effect=lambda k, v, **kw: mem_cache.
                                  update({k: v})):
            for row in payload["rows"]:
                if "drugcentral" not in (row.get("enabled_sources") or []):
                    continue
                for pt in row.get("per_target_results") or []:
                    rec = ((pt.get("source_status") or {})
                           .get("drugcentral") or {}).get("status")
                    if rec not in ("ok", "empty"):
                        continue
                    uni = pt.get("uniprot_id")
                    gene = pt.get("target_symbol")
                    if not uni:
                        continue
                    env = drugcentral_v2.get_target_interactions(
                        uni, gene=gene)
                    checked += 1
                    if env["status"] != rec:
                        mismatches.append(
                            f"{row['drug_name']}/{uni}({gene}): "
                            f"recorded={rec} local={env['status']}")
        self.assertGreater(checked, 50,
                           f"only {checked} control targets checked")
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
