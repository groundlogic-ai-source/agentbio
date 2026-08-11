"""Pinned local PubChem property snapshot for the bounded drug universe.

Why this exists
---------------
PubChem PUG REST is a hard dependency of both the audit lane (XLogP drives
the lipophilicity dimension) and pool enrichment, and it degrades under load
with `PUGREST.ServerBusy` 503s. An outage mid-study either wedges the run or,
worse, stamps a constant "unresolved" across a cohort — which silently drops
a scoring term rather than failing loudly.

The scientifically load-bearing entity set is bounded and small: the distinct
drugs in the repoDB dataset (~1.5k). Their physicochemical properties are
static facts, so they belong in a pinned local snapshot rather than a live
API call — the same pattern already used for DrugCentral.

Contract (mirrors the DrugCentral snapshot precedent)
-----------------------------------------------------
* Read-only at run time. Built once by
  `validation/build_pubchem_snapshot.py`, then committed and sha256-pinned.
* **Fail-closed on corruption**: a missing or unreadable snapshot raises
  rather than silently falling through to the network, so a broken pin can
  never masquerade as a cache miss.
* **Miss != failure**: a drug absent from the snapshot returns None so the
  caller can decide (live lookup, or an explicit UNRESOLVED). Absence of a
  row is a real fact about coverage, never a stamped zero.
* Reproducibility: a frozen study bound to a snapshot hash gets byte-identical
  chemistry on re-run, which live PUG REST cannot promise.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Optional

SNAPSHOT_PATH = Path(__file__).resolve().parent / "pubchem_snapshot.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS compound (
    query_name       TEXT PRIMARY KEY,   -- casefolded lookup key
    display_name     TEXT NOT NULL,
    inchikey         TEXT,
    canonical_smiles TEXT,
    molecular_weight REAL,
    xlogp            REAL,
    is_known_drug    INTEGER,
    atc_codes        TEXT,               -- comma-joined, '' when none
    resolved         INTEGER NOT NULL,
    harvested_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class SnapshotUnavailable(RuntimeError):
    """Raised when the snapshot is expected but unusable (fail-closed)."""


def _norm(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def available() -> bool:
    return SNAPSHOT_PATH.exists()


def sha256() -> Optional[str]:
    if not SNAPSHOT_PATH.exists():
        return None
    return hashlib.sha256(SNAPSHOT_PATH.read_bytes()).hexdigest()


def _connect(readonly: bool = True) -> sqlite3.Connection:
    if readonly:
        if not SNAPSHOT_PATH.exists():
            raise SnapshotUnavailable(f"missing snapshot: {SNAPSHOT_PATH}")
        conn = sqlite3.connect(f"file:{SNAPSHOT_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(SNAPSHOT_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def lookup(drug_name: str) -> Optional[dict[str, Any]]:
    """Return the pinned record for `drug_name`, or None when not covered.

    Raises SnapshotUnavailable if the snapshot file is absent or corrupt --
    never degrades quietly into a miss.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM compound WHERE query_name = ?",
                (_norm(drug_name),)).fetchone()
    except sqlite3.DatabaseError as exc:
        raise SnapshotUnavailable(f"corrupt snapshot: {exc}") from exc
    if row is None:
        return None
    return {
        "inchikey": row["inchikey"],
        "canonical_smiles": row["canonical_smiles"],
        "molecular_weight": row["molecular_weight"],
        "xlogp": row["xlogp"],
        "is_known_drug": bool(row["is_known_drug"]),
        "atc_codes": [c for c in (row["atc_codes"] or "").split(",") if c],
        "resolved": bool(row["resolved"]),
        "error": None,
        "source": "pubchem_snapshot",
    }


def coverage() -> dict[str, Any]:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM compound").fetchone()[0]
        resolved = conn.execute(
            "SELECT COUNT(*) FROM compound WHERE resolved = 1").fetchone()[0]
        with_xlogp = conn.execute(
            "SELECT COUNT(*) FROM compound WHERE xlogp IS NOT NULL"
        ).fetchone()[0]
        meta = {r["key"]: r["value"]
                for r in conn.execute("SELECT key, value FROM meta")}
    return {"rows": total, "resolved": resolved, "with_xlogp": with_xlogp,
            "sha256": sha256(), **meta}
