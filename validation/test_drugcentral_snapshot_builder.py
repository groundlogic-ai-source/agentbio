"""Fail-closed parser tests for the Amendment-6 snapshot builder.

The builder converts the official 1.4 GB pg_dump into the committed two-table
sqlite. These tests pin the code-review hardening: malformed COPY rows,
headers missing retained columns, invalid UTF-8, and unpinned dump bytes must
all FAIL LOUDLY — never silently substitute NULLs or build from unverified
input.

unittest-only (no pytest in this env): run via
    python3 -m unittest validation.test_drugcentral_snapshot_builder -v
"""
import gzip
import hashlib
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "build_drugcentral_snapshot",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts",
                 "build_drugcentral_snapshot.py"))
bds = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bds)

_ACT_HEADER = ("COPY public.act_table_full (act_id, struct_id, target_id, "
               "target_name, target_class, accession, gene, swissprot, "
               "act_value, act_unit, act_type, act_comment, act_source, "
               "relation, moa, moa_source, act_source_url, moa_source_url, "
               "action_type, first_in_class, tdl, act_ref_id, moa_ref_id, "
               "organism) FROM stdin;")
_STRUCT_HEADER = ("COPY public.structures (id, cd_id, name, status, smiles, "
                  "inchi, inchikey, cd_molweight, cas_reg_no, molfile) "
                  "FROM stdin;")


def _act_line(act_id="5", struct_id="10", accession="P08183",
              act_value="3.951", comment=r"tab\there", moa="\\N"):
    """24 fields matching _ACT_HEADER order."""
    return "\t".join([
        act_id, struct_id, "884", "SERCA", "Enzyme", accession, "ATP2A1",
        "P08183", act_value, "nM", "IC50", comment, "DrugCentral", "=",
        moa, "DrugCentral", r"\N", r"\N", "inhibitor", "0", "Tchem", r"\N",
        r"\N", "Homo sapiens"])


def _struct_line(sid="10", name="thapsigargin", status="OFP"):
    return "\t".join([sid, "900", name, status, "C=C", "InChI=1S",
                      "AAAA-AAAA-N", "650.8", "67526-95-8", r"\N"])


def _write_gz(path, chunks: list[bytes]):
    with gzip.open(path, "wb") as f:
        for c in chunks:
            f.write(c)


def _good_dump(path):
    _write_gz(path, [
        b"-- pg_dump header noise\n",
        _ACT_HEADER.encode() + b"\n",
        _act_line().encode() + b"\n",
        _act_line(act_id="6", struct_id="11", accession="Q99999").encode()
        + b"\n",
        b"\\.\n",
        b"COPY public.lincs_signature (x) FROM stdin;\n",
        "café-naïve-binary-ish-row\n".encode("utf-16-le"),  # never decoded
        b"\\.\n",
        _STRUCT_HEADER.encode() + b"\n",
        _struct_line().encode() + b"\n",
        b"\\.\n",
    ])


class BuilderParserTest(unittest.TestCase):
    def test_happy_path_parses_escapes_nulls_and_types(self):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            out = os.path.join(td, "s.sqlite")
            rep = os.path.join(td, "r.json")
            _good_dump(dump)
            digest = hashlib.sha256(open(dump, "rb").read()).hexdigest()
            with mock.patch.multiple(
                    bds, DUMP=dump, OUT=out, REPORT=rep,
                    EXPECTED_DUMP_SHA256=digest,
                    _MIN_ACT_ROWS=0, _MIN_STRUCT_ROWS=0, _MIN_ESTABLISHED=0):
                self.assertEqual(bds.main(), 0)
            conn = sqlite3.connect(out)
            rows = conn.execute(
                "SELECT act_id, act_value, act_comment, moa, accession "
                "FROM act_table_full ORDER BY act_id").fetchall()
            self.assertEqual(rows[0],
                             (5, 3.951, "tab\there", None, "P08183"))
            self.assertEqual(rows[1][4], "Q99999")
            struct = conn.execute(
                "SELECT id, name, status, cd_molweight FROM structures"
            ).fetchone()
            self.assertEqual(struct, (10, "thapsigargin", "OFP", 650.8))
            conn.close()

    def test_field_count_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            _write_gz(dump, [
                _ACT_HEADER.encode() + b"\n",
                b"1\t2\t3\n",  # 3 fields vs 24 declared
                b"\\.\n",
            ])
            with self.assertRaisesRegex(RuntimeError, "field"):
                with gzip.open(dump, "rb") as fh:
                    list(bds._extract_copy_blocks(fh, bds_wanted()))

    def test_missing_retained_column_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            header = _ACT_HEADER.replace("gene, ", "")  # drop a retained col
            _write_gz(dump, [header.encode() + b"\n",
                             _act_line().encode() + b"\n", b"\\.\n"])
            with self.assertRaisesRegex(RuntimeError, "lacks retained"):
                with gzip.open(dump, "rb") as fh:
                    list(bds._extract_copy_blocks(fh, bds_wanted()))

    def test_invalid_utf8_in_wanted_block_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            bad = _act_line().encode().replace(b"SERCA", b"SERC\xffA")
            _write_gz(dump, [_ACT_HEADER.encode() + b"\n", bad + b"\n",
                             b"\\.\n"])
            with self.assertRaises(UnicodeDecodeError):
                with gzip.open(dump, "rb") as fh:
                    list(bds._extract_copy_blocks(fh, bds_wanted()))

    def test_unpinned_dump_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            _good_dump(dump)
            with mock.patch.multiple(
                    bds, DUMP=dump,
                    EXPECTED_DUMP_SHA256="0" * 64):
                self.assertEqual(bds.main(), 2)


def bds_wanted():
    return {"act_table_full": bds.ACT_KEEP, "structures": bds.STRUCT_KEEP}


if __name__ == "__main__":
    unittest.main()
