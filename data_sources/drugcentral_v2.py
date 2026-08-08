"""
DrugCentral v2 target–activity data.

API base (verified): https://uxn2ycvimg.us-east-2.awsapprunner.com

This adapter is TARGET-FIRST: given a UniProt accession it pulls the bioactivity
rows recorded against that target (/act_table_full/accession/{accession}),
restricts to Homo sapiens established products, resolves each drug's structure
(/structures/id/{struct_id}), and normalises it into a candidate row that
preserves the source activity / assay / lineage fields (act_id, act_type,
act_value, act_source, act_comment, moa, tdl, target_class, ...).

Gene fallback: if the accession lookup 500s (server-side accession index
failure), and a gene symbol is supplied, we retry via
/act_table_full/gene/{gene}. A gene fallback is only a routing detour — the
Homo-sapiens + established-product restrictions still apply.

"Established product" = DrugCentral structure status in {OFP, OFM} (Official
FDA Product / marketed product), inspected on the resolved structure row.
status ONP / null are excluded. This keeps the candidate pool to real,
established medicines rather than experimental structures.

Cache discipline (mirrors data_sources/chembl.py):
  - Only HEALTHY, non-malformed responses are cached (empty-after-filter
    included: the accession/gene resolved but nothing survived the Homo-sapiens
    + established-product filter).
  - 429 / 5xx (other than the accession→gene 500 fallback) / timeouts /
    connection errors / malformed payload → UNAVAILABLE and NOT cached.

Common return envelope:
  {source, status, candidates, error, release}

NOTE: this adapter never queries by drug NAME. It is target-first only, so a
retrospective benchmark's held-out drug cannot leak in via a name lookup.
"""

import os

import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key
from data_sources import drugcentral_local

BASE_URL = "https://uxn2ycvimg.us-east-2.awsapprunner.com"


def _use_local_lane() -> bool:
    """Amendment 6: serve queries from the committed official 11/01/2023 dump
    snapshot (DRS-identical semantics) whenever it is present. The live App
    Runner endpoint (UNM, no SLA) is only used when no snapshot exists.
    DRUGCENTRAL_FORCE_LIVE=1 overrides for debugging — set per deployment,
    never mid-run."""
    if os.environ.get("DRUGCENTRAL_FORCE_LIVE"):
        return False
    return drugcentral_local.available()

# v2: Amendment 6 local-lane snapshot — never mix live-API-derived cache
# entries with snapshot-derived ones (data provenance differs by source).
_CACHE_VERSION = "v2"
_TTL_DAYS = 7

# Transient statuses that must render the source unavailable (never cached).
# NOTE: 500 on the accession route is handled specially (gene fallback) BEFORE
# it reaches this set.
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# DrugCentral structure.status values that denote an established marketed
# product. Inspected on the resolved /structures row.
_ESTABLISHED_STATUSES = {"OFP", "OFM"}

_HOMO_SAPIENS = "homo sapiens"


class _SourceUnavailable(Exception):
    """Transient/malformed condition — must not be cached."""


class _AccessionServerError(Exception):
    """The accession route returned 500. Callers may retry via gene fallback."""


def _envelope(status: str, candidates: list[dict[str, Any]],
              error: Optional[str], release: Optional[str]) -> dict[str, Any]:
    return {
        "source": "drugcentral",
        "status": status,
        "candidates": candidates,
        "error": error,
        "release": release,
    }


def _get_json(path: str, *, accession_route: bool = False) -> Any:
    """GET {BASE_URL}{path}, returning parsed JSON.

    Raises:
      _AccessionServerError  — 500 on the accession route (fallback-eligible).
      _SourceUnavailable     — other transient HTTP / timeout / connection
                               error / non-JSON body.
    A 404 returns None (legitimate "no such resource").
    """
    if _use_local_lane():
        try:
            return drugcentral_local.get_json(path)
        except drugcentral_local.SnapshotCorrupt as e:
            raise _SourceUnavailable(str(e)) from e

    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, headers={"Accept": "application/json"},
                            timeout=30)
    except requests.exceptions.RequestException as e:
        raise _SourceUnavailable(f"request to {url} failed: {e}") from e

    if resp.status_code == 404:
        return None
    if resp.status_code == 500 and accession_route:
        raise _AccessionServerError(f"{url} returned 500")
    if resp.status_code in _TRANSIENT_STATUSES:
        raise _SourceUnavailable(
            f"{url} returned transient HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise _SourceUnavailable(
            f"{url} returned unexpected HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as e:
        raise _SourceUnavailable(f"{url} returned non-JSON body: {e}") from e


def _require_list(data: Any, what: str) -> list[dict[str, Any]]:
    """Validate a payload is a JSON list of objects (the API contract)."""
    if data is None:
        return []
    if not isinstance(data, list):
        raise _SourceUnavailable(f"{what} did not return a JSON list")
    for row in data:
        if not isinstance(row, dict):
            raise _SourceUnavailable(f"{what} row was not an object")
    return data


def _fetch_act_rows_by_accession(accession: str) -> list[dict[str, Any]]:
    data = _get_json(f"/act_table_full/accession/{accession}",
                    accession_route=True)
    return _require_list(data, "act_table_full/accession")


def _fetch_act_rows_by_gene(gene: str) -> list[dict[str, Any]]:
    data = _get_json(f"/act_table_full/gene/{gene}")
    return _require_list(data, "act_table_full/gene")


def _fetch_structure(struct_id: Any) -> Optional[dict[str, Any]]:
    """Resolve /structures/id/{struct_id}. The endpoint returns a JSON list;
    we take the first row. None → not found (404 or empty list)."""
    data = _get_json(f"/structures/id/{struct_id}")
    if data is None:
        return None
    rows = _require_list(data, "structures/id")
    return rows[0] if rows else None


def _is_homo_sapiens(row: dict[str, Any]) -> bool:
    org = (row.get("organism") or "").strip().lower()
    return org == _HOMO_SAPIENS


def _is_established(structure: dict[str, Any]) -> bool:
    status = (structure.get("status") or "").strip().upper()
    return status in _ESTABLISHED_STATUSES


def _normalize_candidate(act_row: dict[str, Any],
                         structure: dict[str, Any]) -> dict[str, Any]:
    """Build one normalized established-product candidate, preserving the source
    activity / assay / lineage fields for auditable provenance."""
    struct_id = act_row.get("struct_id")

    # Evidence record: the raw activity provenance for this candidate row.
    evidence: list[dict[str, Any]] = [{
        "type": "drugcentral_activity",
        "act_id": act_row.get("act_id"),
        "act_type": act_row.get("act_type"),
        "act_value": act_row.get("act_value"),
        "act_unit": act_row.get("act_unit"),
        "relation": act_row.get("relation"),
        "act_source": act_row.get("act_source"),
        "act_comment": act_row.get("act_comment"),
        "moa": act_row.get("moa"),
        "moa_source": act_row.get("moa_source"),
        "action_type": act_row.get("action_type"),
        "target_id": act_row.get("target_id"),
        "target_class": act_row.get("target_class"),
        "tdl": act_row.get("tdl"),
        "first_in_class": act_row.get("first_in_class"),
    }]

    act_value: Optional[float] = None
    raw_val = act_row.get("act_value")
    if raw_val not in (None, ""):
        try:
            act_value = float(raw_val)
        except (TypeError, ValueError):
            act_value = None

    return {
        # Provider identity
        "source": "drugcentral",
        "struct_id": struct_id,
        "provider_act_id": act_row.get("act_id"),
        # Drug identity (from resolved structure row)
        "name": structure.get("name") or act_row.get("target_name"),
        "structure_status": structure.get("status"),
        # Structure
        "smiles": structure.get("smiles"),
        "inchi": structure.get("inchi"),
        "inchikey": structure.get("inchikey"),
        "molecular_weight": structure.get("cd_molweight"),
        "cas_reg_no": structure.get("cas_reg_no"),
        # Pharmacology / activity (source fields preserved)
        "act_type": act_row.get("act_type"),
        "act_value": act_value,
        "act_unit": act_row.get("act_unit"),
        "relation": act_row.get("relation"),
        "action_type": act_row.get("action_type"),
        "moa": act_row.get("moa"),
        "moa_source": act_row.get("moa_source"),
        "act_source": act_row.get("act_source"),
        "act_comment": act_row.get("act_comment"),
        "tdl": act_row.get("tdl"),
        "first_in_class": act_row.get("first_in_class"),
        # Target identity
        "gene": act_row.get("gene"),
        "accession": act_row.get("accession"),
        "swissprot": act_row.get("swissprot"),
        "target_id": act_row.get("target_id"),
        "target_name": act_row.get("target_name"),
        "target_class": act_row.get("target_class"),
        "organism": act_row.get("organism"),
        # Provenance
        "evidence": evidence,
    }


def _build_candidates(act_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter to Homo sapiens, resolve structures, keep established products,
    normalise, and dedup by struct_id (merging evidence for repeat activities).

    Structures are resolved once per struct_id (memoised) so a drug with many
    activity rows against the target does not re-fetch its structure."""
    struct_cache: dict[Any, Optional[dict[str, Any]]] = {}
    by_struct: dict[Any, dict[str, Any]] = {}

    for row in act_rows:
        if not _is_homo_sapiens(row):
            continue
        struct_id = row.get("struct_id")
        if struct_id is None:
            continue

        if struct_id not in struct_cache:
            struct_cache[struct_id] = _fetch_structure(struct_id)
        structure = struct_cache[struct_id]
        if structure is None or not _is_established(structure):
            continue

        if struct_id in by_struct:
            # Same drug, another activity row → merge evidence only.
            by_struct[struct_id]["evidence"].extend(
                _normalize_candidate(row, structure)["evidence"])
            continue

        by_struct[struct_id] = _normalize_candidate(row, structure)

    return list(by_struct.values())


def get_target_interactions(uniprot_id: str,
                            gene: Optional[str] = None) -> dict[str, Any]:
    """Target-first established-product candidate lookup for a UniProt
    accession, with gene fallback on an accession-route 500.

    Args:
      uniprot_id: primary target UniProt accession (e.g. 'P49841').
      gene:       HGNC symbol used ONLY as a fallback if the accession route
                  500s. Optional; without it a 500 is unavailable.

    Returns the common envelope {source, status, candidates, error, release}.

    status:
      "ok"          — resolved and ≥1 established-product candidate.
      "empty"       — resolved but nothing survived the Homo-sapiens +
                      established-product filter (healthy, cacheable).
      "unavailable" — transient failure / malformed payload (NOT cached).
    """
    cache_key = make_key(f"drugcentral_get_target_interactions_{_CACHE_VERSION}",
                        uniprot_id, gene)
    cached = get(cache_key)
    if cached is not None:
        return cached

    try:
        try:
            act_rows = _fetch_act_rows_by_accession(uniprot_id)
        except _AccessionServerError as e:
            # Accession index failed server-side. Only the gene route can
            # rescue this; without a gene symbol it is unavailable.
            if not gene:
                print(f"[drugcentral] WARNING: accession 500 for "
                      f"'{uniprot_id}' and no gene fallback available: {e}")
                return _envelope("unavailable", [], str(e), None)
            print(f"[drugcentral] accession 500 for '{uniprot_id}'; "
                  f"falling back to gene '{gene}'")
            act_rows = _fetch_act_rows_by_gene(gene)

        candidates = _build_candidates(act_rows)

    except _SourceUnavailable as e:
        print(f"[drugcentral] WARNING: source unavailable for "
              f"'{uniprot_id}': {e}")
        return _envelope("unavailable", [], str(e), None)

    status = "ok" if candidates else "empty"
    result = _envelope(status, candidates, None, None)
    cache_set(cache_key, result, ttl_days=_TTL_DAYS)
    return result
