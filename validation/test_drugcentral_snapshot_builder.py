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
import subprocess
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
        # Never decoded. utf-16-le's trailing \x00 plus an explicit newline
        # keeps the block terminator on its own line, as in a real dump.
        "café-naïve-binary-ish-row\n".encode("utf-16-le") + b"\n",
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
            with open(dump, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
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


class UnwantedBlockIsolationTest(unittest.TestCase):
    """Regression: unwanted COPY blocks are skipped byte-for-byte (review
    round 2). Data inside them must never be decoded or pattern-matched."""

    def _parse(self, chunks):
        with tempfile.TemporaryDirectory() as td:
            dump = os.path.join(td, "d.sql.gz")
            _write_gz(dump, chunks)
            with gzip.open(dump, "rb") as fh:
                return [(t, r) for t, _, r in
                        bds._extract_copy_blocks(fh, bds_wanted())]

    def test_copy_like_data_and_binary_garbage_inside_unwanted_block(self):
        events = self._parse([
            b"COPY public.lincs_signature (x) FROM stdin;\n",
            # A data line byte-identical to a WANTED-table header — must not
            # hijack the parser:
            b"COPY public.act_table_full (act_id) FROM stdin;\n",
            b"\xff\xfe binary garbage that must never be decoded\n",
            b"\\\\.\n",           # escaped backslash-dot DATA, not terminator
            b"still unwanted data\n",
            b"\\.\n",             # real terminator for lincs_signature
            _ACT_HEADER.encode() + b"\n",
            _act_line().encode() + b"\n",
            b"\\.\n",
            _STRUCT_HEADER.encode() + b"\n",
            _struct_line().encode() + b"\n",
            b"\\.\n",
        ])
        rows = [r for _, r in events if r is not None]
        self.assertEqual(len(rows), 2)  # exactly the real act + struct rows
        self.assertEqual(rows[0]["accession"], "P08183")
        self.assertEqual(rows[1]["name"], "thapsigargin")

    def test_unparseable_copy_header_raises(self):
        with self.assertRaisesRegex(RuntimeError, "unparseable COPY header"):
            self._parse([b"COPY public.broken unbalanced FROM stdin\n"])


class DownloaderPinTest(unittest.TestCase):
    """The downloader's pinned-digest gate, exercised end-to-end via its
    env overrides (file:// fixture instead of the Wayback URL)."""

    _SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "scripts", "download_drugcentral_dump.sh")
    _DATA = b"tiny-fixture-payload"

    def _run_dl(self, digest: str):
        with tempfile.TemporaryDirectory() as td:
            payload = os.path.join(td, "payload.bin")
            with open(payload, "wb") as f:
                f.write(self._DATA)
            done = os.path.join(td, "done")
            env = dict(os.environ)
            env.update({
                "DRUGCENTRAL_DL_OUT": os.path.join(td, "out.bin"),
                "DRUGCENTRAL_DL_URL": "file://" + payload,
                "DRUGCENTRAL_DL_SIZE": str(len(self._DATA)),
                "DRUGCENTRAL_DL_SHA256": digest,
                "DRUGCENTRAL_DL_DONE": done,
                "DRUGCENTRAL_DL_LOCK": os.path.join(td, "lock"),
                "DRUGCENTRAL_DL_SHA_OUT": os.path.join(td, "sha"),
            })
            proc = subprocess.run(["bash", self._SCRIPT], env=env,
                                  capture_output=True, text=True, timeout=120)
            marker = ""
            if os.path.exists(done):
                with open(done) as fh:
                    marker = fh.read()
            return proc.returncode, marker

    def test_matching_pin_completes(self):
        rc, marker = self._run_dl(
            hashlib.sha256(self._DATA).hexdigest())
        self.assertEqual(rc, 0)
        self.assertIn("DONE", marker)

    def test_mismatched_pin_is_rejected(self):
        rc, marker = self._run_dl("0" * 64)
        self.assertNotEqual(rc, 0)
        self.assertIn("FAILED sha256-mismatch", marker)


def bds_wanted():
    return {"act_table_full": bds.ACT_KEEP, "structures": bds.STRUCT_KEEP}


if __name__ == "__main__":
    unittest.main()
