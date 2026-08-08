#!/usr/bin/env python3
"""Build the committed two-table DrugCentral snapshot from the official dump.

Input : /tmp/drugcentral_dump_11012023.sql.gz — the official DrugCentral
        11/01/2023 PostgreSQL dump, fetched from the Internet Archive Wayback
        Machine snapshot of unmtid-dbs.net (origin host down since
        2026-08-07). See benchmark_v2_preregistration.md, Amendment 6.
Output: data_sources/drugcentral_2023_snapshot.sqlite — committed, rides the
        publish snapshot into prod, and is pinned in the pipeline fingerprint.

Only the columns the pipeline actually reads are kept (molfile/molimg blobs
and URL/ref-id bookkeeping are dropped). NULLs, text, and numeric values are
preserved so the local lane reproduces DRS API responses field-for-field.

Writes a build report to validation/drugcentral_snapshot_build.json.
"""
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys

DUMP = "/tmp/drugcentral_dump_11012023.sql.gz"
DUMP_SHA256_FILE = "/tmp/drugcentral_dump.sha256"
# Pinned at first retrieval (2026-08-08) from the Wayback capture; the builder
# refuses any other bytes. Content authenticity is further cross-validated by
# the conformance replay against statuses recorded from the live API during
# the control (Amendment 6, items 28-30).
EXPECTED_DUMP_SHA256 = ("055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf"
                        "7cc018204f3e04f")
# Minimum-row tripwires for the verified 11/01/2023 census (module constants
# so tests can relax them for synthetic fixtures).
_MIN_ACT_ROWS = 15000
_MIN_STRUCT_ROWS = 4000
_MIN_ESTABLISHED = 1000
OUT = "data_sources/drugcentral_2023_snapshot.sqlite"
REPORT = "validation/drugcentral_snapshot_build.json"

WAYBACK_URL = ("http://web.archive.org/web/20260301100338id_/"
               "https://unmtid-dbs.net/download/drugcentral.dump.11012023.sql.gz")

ACT_KEEP = ["act_id", "struct_id", "act_type", "act_value", "act_unit",
            "relation", "act_source", "act_comment", "moa", "moa_source",
            "action_type", "target_id", "target_class", "tdl",
            "first_in_class", "gene", "accession", "swissprot", "target_name",
            "organism"]
STRUCT_KEEP = ["id", "name", "status", "smiles", "inchi", "inchikey",
               "cd_molweight", "cas_reg_no"]

_INT_COLS = {"act_id", "struct_id", "target_id", "first_in_class", "id"}
_REAL_COLS = {"act_value", "cd_molweight"}

_COPY_RE = re.compile(r"^COPY public\.(\w+) \(([^)]+)\) FROM stdin;$")


def _unescape_pg_text(s: str) -> str:
    """Reverse pg_dump text-format backslash escapes."""
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(s):
            out.append("\\")
            break
        e = s[i]
        if e == "n":
            out.append("\n")
            i += 1
        elif e == "t":
            out.append("\t")
            i += 1
        elif e == "r":
            out.append("\r")
            i += 1
        elif e == "b":
            out.append("\b")
            i += 1
        elif e == "f":
            out.append("\f")
            i += 1
        elif e == "v":
            out.append("\v")
            i += 1
        elif e == "\\":
            out.append("\\")
            i += 1
        elif e.isdigit():  # octal escape \NNN
            oct_digits = s[i:i + 3]
            out.append(chr(int(oct_digits, 8)))
            i += 3
        else:
            out.append(e)
            i += 1
    return "".join(out)


def _coerce(col: str, raw):
    if raw is None:
        return None
    if col in _INT_COLS:
        try:
            return int(raw)
        except ValueError:
            return raw
    if col in _REAL_COLS:
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def _col_type(col: str) -> str:
    if col in _INT_COLS:
        return "INTEGER"
    if col in _REAL_COLS:
        return "REAL"
    return "TEXT"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_copy_blocks(fh, wanted: dict):
    """Yield (table, cols, row_dict) for COPY blocks of wanted tables.

    Fail-closed (code-review hardening, round 2): block state is tracked in
    raw bytes. Between blocks, only COPY-header lines are inspected and an
    unparseable one raises. UNWANTED blocks are skipped byte-for-byte until
    the exact `\.` terminator — their data is never decoded, so non-UTF-8
    payloads or data lines that merely look like COPY headers can neither
    crash the build nor inject spurious rows. Inside a WANTED block, a
    field-count mismatch, a header missing retained columns, or invalid
    UTF-8 all raise. (pg_dump escapes backslashes in data, so a literal `\.`
    data value is dumped as `\\.` and can never alias the block terminator.)
    """
    IN_UNWANTED = ""  # sentinel: inside a COPY block we do not extract
    table = None      # None = between blocks; IN_UNWANTED; or wanted name
    cols = None
    keep_idx = None
    keep_cols = None
    lineno = 0
    for bline in fh:
        lineno += 1
        if table == IN_UNWANTED and table is not None:
            # Raw skip: never decode, never pattern-match — only the exact
            # terminator line ends the block.
            if bline.rstrip(b"\n") == b"\\.":
                table = None
            continue
        if table is None:
            if not bline.startswith(b"COPY public."):
                continue
            line = bline.decode("ascii").rstrip("\n")  # SQL header; strict
            m = _COPY_RE.match(line)
            if not m:
                raise RuntimeError(
                    f"unparseable COPY header at dump line {lineno}: "
                    f"{line[:120]!r}")
            if m.group(1) not in wanted:
                table = IN_UNWANTED
                continue
            table = m.group(1)
            cols = [c.strip() for c in m.group(2).split(",")]
            keep_cols = wanted[table]
            missing = [c for c in keep_cols if c not in cols]
            if missing:
                raise RuntimeError(
                    f"COPY header for {table} lacks retained columns: "
                    f"{missing}")
            keep_idx = [(cols.index(c), c) for c in keep_cols]
            continue
        if bline.rstrip(b"\n") == b"\\.":
            yield (table, keep_cols, None)  # end marker
            table = None
            continue
        line = bline.decode("utf-8").rstrip("\n")  # strict: no silent replace
        fields = line.split("\t")
        if len(fields) != len(cols):
            raise RuntimeError(
                f"{table} COPY row at dump line {lineno} has {len(fields)} "
                f"fields; header declares {len(cols)} — refusing to emit "
                "substituted NULLs")
        row = {}
        for idx, c in keep_idx:
            raw = fields[idx]
            val = None if raw == "\\N" else _unescape_pg_text(raw)
            row[c] = _coerce(c, val)
        yield (table, keep_cols, row)


def main() -> int:
    if not os.path.exists(DUMP):
        print(f"[snapshot] dump not found at {DUMP}")
        return 1
    digest = _sha256_file(DUMP)
    if digest != EXPECTED_DUMP_SHA256:
        print(f"[snapshot] dump SHA-256 {digest} != pinned "
              f"{EXPECTED_DUMP_SHA256} — refusing to build from an "
              "unverified dump")
        return 2

    wanted = {"act_table_full": ACT_KEEP, "structures": STRUCT_KEEP}
    counts = {"act_table_full": 0, "structures": 0}

    tmp = OUT + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    cur = conn.cursor()
    cur.execute("CREATE TABLE act_table_full (%s)" % ", ".join(
        f"{c} {_col_type(c)}" for c in ACT_KEEP))
    cur.execute("CREATE TABLE structures (%s)" % ", ".join(
        f"{c} {_col_type(c)}" for c in STRUCT_KEEP))

    with gzip.open(DUMP, "rb") as fh:
        table = None
        batch = []
        for t, keep_cols, row in _extract_copy_blocks(fh, wanted):
            if row is None:  # end of a COPY block
                if batch:
                    cur.executemany(
                        f"INSERT INTO {table} VALUES (%s)"
                        % ",".join("?" * len(batch[0])), batch)
                    batch = []
                table = None
                continue
            if t != table and batch:
                cur.executemany(
                    f"INSERT INTO {table} VALUES (%s)"
                    % ",".join("?" * len(batch[0])), batch)
                batch = []
            table = t
            batch.append([row.get(c) for c in keep_cols])
            counts[t] += 1
            if len(batch) >= 5000:
                cur.executemany(
                    f"INSERT INTO {table} VALUES (%s)"
                    % ",".join("?" * len(batch[0])), batch)
                batch = []
        if batch:
            cur.executemany(
                f"INSERT INTO {table} VALUES (%s)"
                % ",".join("?" * len(batch[0])), batch)

    cur.execute("CREATE INDEX idx_structures_id ON structures(id)")
    cur.execute("CREATE INDEX idx_act_struct ON act_table_full(struct_id)")
    conn.commit()

    # Sanity probes (fixed expectations for the 11/01/2023 release).
    n_act = cur.execute("SELECT COUNT(*) FROM act_table_full").fetchone()[0]
    n_struct = cur.execute("SELECT COUNT(*) FROM structures").fetchone()[0]
    n_established = cur.execute(
        "SELECT COUNT(*) FROM structures WHERE status IN ('OFP','OFM')"
    ).fetchone()[0]
    n_probe = cur.execute(
        "SELECT COUNT(*) FROM act_table_full WHERE TRIM(accession) "
        "LIKE '%P08183%'").fetchone()[0]
    conn.close()

    # Fixed expectations for the 11/01/2023 release (verified by direct census
    # of the dump's COPY blocks): act_table_full is a *curated* table —
    # 20,978 rows; structures: 4,995 rows, of which 1,503 are OFP/OFM
    # established products (1,090 OFP + 413 OFM).
    assert n_act == counts["act_table_full"] and n_act > _MIN_ACT_ROWS, n_act
    assert n_struct == counts["structures"] and n_struct > _MIN_STRUCT_ROWS, \
        n_struct
    assert n_established > _MIN_ESTABLISHED, n_established
    assert n_probe > 0, "probe accession P08183 missing — snapshot unusable"

    os.replace(tmp, OUT)

    report = {
        "source_dump": WAYBACK_URL,
        "source_dump_sha256": digest,
        "source_dump_sha256_verified_against_pin": True,
        "release": "DrugCentral 2023 (dump dated 11/01/2023)",
        "rows": {"act_table_full": n_act, "structures": n_struct,
                 "established_product_structures": n_established},
        "probe_accession_P08183_rows": n_probe,
        "snapshot_bytes": os.path.getsize(OUT),
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
