"""Local-lane queries against the committed DrugCentral 2023 snapshot.

Amendment 6 (benchmark v2 pre-registration): the live DRS API
(unmtransinfo/CFDE_IDG_DRS on AWS App Runner) is a small no-SLA academic
deployment whose backing database hung for 25+ h on 2026-08-07/08, blocking
the pre-registered benchmark. The official 11/01/2023 dump is the versioned
snapshot of the very same database the DRS API queries
(postgresql://unmtid-dbs.net:5433/drugcentral), so serving queries locally
from it is data-identical while removing the availability risk.

Query semantics mirror app/main.py of CFDE_IDG_DRS exactly:
  /act_table_full/accession/{acc} : trim(accession) ILIKE '%acc%'  (404 if none)
  /act_table_full/gene/{gene}     : trim(gene) ILIKE '%gene%'      (404 if none)
  /structures/id/{id}             : id == <int>                    (404 if none)

Notes on faithfulness:
  - Postgres ILIKE and SQLite LIKE are both case-insensitive for ASCII, and
    both treat %/_ in the pattern as wildcards, so substring semantics match.
  - The live API's intermittent 500 on the accession route was a server-side
    bug (handled upstream via the gene fallback); the local lane returns the
    correct rows directly, so the fallback simply never triggers.
  - A 404 maps to None here, exactly as in drugcentral_v2._get_json.
"""
import os
import re
import sqlite3
from typing import Any, Optional
from urllib.parse import unquote

SNAPSHOT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "drugcentral_2023_snapshot.sqlite")

_ACT_COLS = ["act_id", "struct_id", "act_type", "act_value", "act_unit",
             "relation", "act_source", "act_comment", "moa", "moa_source",
             "action_type", "target_id", "target_class", "tdl",
             "first_in_class", "gene", "accession", "swissprot", "target_name",
             "organism"]
_STRUCT_COLS = ["id", "name", "status", "smiles", "inchi", "inchikey",
                "cd_molweight", "cas_reg_no"]

_ACCESSION_RE = re.compile(r"^/act_table_full/accession/([^/]+)$")
_GENE_RE = re.compile(r"^/act_table_full/gene/([^/]+)$")
_STRUCT_RE = re.compile(r"^/structures/id/([^/]+)$")


class SnapshotCorrupt(Exception):
    """Snapshot file missing/unreadable mid-run — explicit failure, never a
    silent fallback to the live API (which would mix data provenance)."""


def available() -> bool:
    return os.path.exists(SNAPSHOT_PATH)


# DrugCentral structure.status values denoting an established marketed product
# (mirrors drugcentral_v2._ESTABLISHED_STATUSES exactly; ONP / null excluded).
_ESTABLISHED_STATUSES = ("OFP", "OFM")


def approved_moiety_rows() -> list[dict[str, Any]]:
    """Raw identity rows for established products (status OFP/OFM).

    Returns ``[{struct_id, name, smiles, inchikey}]`` UNNORMALIZED.  Callers
    needing an active-moiety key apply their own structural normalization to
    ``smiles`` (e.g. the BindingDB lane's fragment-parent + neutralized
    canonical SMILES), keeping this module free of third-party chemistry deps.
    The ``struct_id`` lets callers anchor lineage on the same
    ``drugcentral-approval:{struct_id}`` identity the DrugCentral lane uses,
    instead of double-counting approval evidence.
    """
    rows = _query(
        "SELECT id, name, smiles, inchikey FROM structures "
        f"WHERE status IN ({','.join('?' * len(_ESTABLISHED_STATUSES))})",
        _ESTABLISHED_STATUSES, ["id", "name", "smiles", "inchikey"])
    return [
        {"struct_id": int(r["id"]),
         "name": str(r.get("name") or "").strip(),
         "smiles": str(r.get("smiles") or "").strip(),
         "inchikey": str(r.get("inchikey") or "").strip().upper()}
        for r in rows
    ]


def _query(sql: str, params: tuple, cols: list[str]) -> list[dict[str, Any]]:
    if not available():
        raise SnapshotCorrupt(f"snapshot missing at {SNAPSHOT_PATH}")
    try:
        # Per-call connection: read-only, thread-safe (the benchmark fans
        # targets out over a ThreadPoolExecutor).
        conn = sqlite3.connect(f"file:{SNAPSHOT_PATH}?mode=ro", uri=True)
        try:
            cur = conn.execute(sql, params)
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as e:
        raise SnapshotCorrupt(f"snapshot query failed: {e}") from e


def _act_where(col: str, value: str) -> Optional[list[dict[str, Any]]]:
    # Mirrors trim(col) ILIKE '%value%'. SQLite LIKE is ASCII case-insensitive,
    # matching Postgres ILIKE for the ASCII identifiers stored here.
    rows = _query(
        f"SELECT {', '.join(_ACT_COLS)} FROM act_table_full "
        f"WHERE TRIM({col}) LIKE ? ESCAPE '\\'",
        (f"%{unquote(value)}%",), _ACT_COLS)
    return rows or None


def _structure_by_id(struct_id: str) -> Optional[list[dict[str, Any]]]:
    try:
        sid: Any = int(struct_id)
    except ValueError:
        sid = struct_id  # non-integer ids simply match nothing, like the API
    rows = _query(
        f"SELECT {', '.join(_STRUCT_COLS)} FROM structures WHERE id = ?",
        (sid,), _STRUCT_COLS)
    return rows or None


def get_json(path: str) -> Any:
    """Local equivalent of drugcentral_v2._get_json: parsed-JSON-equivalent
    Python objects, None for the API's 404. Raises SnapshotCorrupt on any
    snapshot problem (callers convert to _SourceUnavailable)."""
    m = _ACCESSION_RE.match(path)
    if m:
        return _act_where("accession", m.group(1))
    m = _GENE_RE.match(path)
    if m:
        return _act_where("gene", m.group(1))
    m = _STRUCT_RE.match(path)
    if m:
        return _structure_by_id(m.group(1))
    raise SnapshotCorrupt(f"local lane has no route for path: {path}")
