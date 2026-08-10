"""
BindingDB quantitative pharmacology lane (target-first).

Verified public REST contract (verified live 2026-08-10 against
https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp):

  GET https://bindingdb.org/rest/getLigandsByUniprots
      ?uniprot={ACCESSION}&cutoff={N_NM}&response=application/json

  200 body shape (note the provider's "Linds" typo in the response key — both
  spellings are accepted so a silent upstream fix never breaks us):

    {"getLindsByUniprotsResponse": {"affinities": [
        {"query": "Tyrosine-protein kinase ABL1",
         "monomerid": "332698",
         "smile": "CNC(=O)c1cc(...)",
         "affinity_type": "Ki",            # Ki | Kd | IC50 | EC50
         "affinity": "<1.000",             # relation operator + nM value
         "pmid": "",                        # often empty
         "doi": "10.7270/Q2ZG6VCP"}, ...]}}

  A UniProt accession with no BindingDB rows returns 200 with an EMPTY
  affinities list (verified) — a genuine "no data", distinct from an outage.

This adapter is TARGET-FIRST: it only ever queries by UniProt accession, never
by drug name, so held-out benchmark drugs cannot leak in through the back door
(fusion rule 9, production_evidence_source_portfolio.md).

Unique value (portfolio lane "Quantitative pharmacology"): BindingDB carries
primary-literature and patent measurements that can be absent or differently
represented in ChEMBL.  De-duplication against other providers is delegated to
the evidence ledger's lineage keys; this adapter anchors each row on
monomer+affinity-type+publication so exact re-imports collapse.

Repurposing filter: the REST response has no approval field, so when
``repurposing_only`` is set each binder's SMILES is reduced to an
active-moiety key (RDKit fragment-parent + neutralize + canonical isomeric
SMILES — this RDKit build has no InChI support) and checked against the
committed DrugCentral 2023 snapshot's established-product set, normalized the
same way (drugcentral_local).  Rows that cannot be verified as an approved
active moiety are EXCLUDED, never passed through on faith — the same posture
as GtoPdb's approved_only filter.  Rows that pass borrow the snapshot's real
InChIKey (so the merged candidate unions with ChEMBL/DrugCentral records by
structural identity), its preferred name, and its ``approved_struct_id`` (so
the multisource converter can emit the approval record under the DrugCentral
lane's own lineage anchor — approval evidence is counted once, never twice).

Cache discipline (mirrors gtopdb.py / drugcentral_v2.py):
  - Only HEALTHY responses are cached ("ok" or post-filter "empty").
  - 429 / 5xx / timeouts / connection errors / malformed payloads render the
    source UNAVAILABLE and are NEVER cached, so a transient outage cannot
    poison the cache as "no binders".
  - The endpoint's documented empty-affinity list is genuine emptiness and MAY
    be cached (same rule as GtoPdb's post-filter empty).

Return envelope (shared v2 adapter shape):
  {"source": "bindingdb", "status": "ok" | "empty" | "unavailable",
   "candidates": [...], "error": str | None, "release": str | None,
   "stats": {row-level counts for auditability}}
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

import requests

from cache.cache import get as cache_get, set as cache_set, make_key
from data_sources import drugcentral_local

BASE_URL = "https://bindingdb.org/rest"

# Cache key version. Bump when the normalized candidate shape or the caching
# gate changes so stale rows from an older schema can never be served.
# v1 -> v2: v1 cached empty envelopes produced while identity resolution was
# InChIKey-based but this RDKit build has no InChI support (every row fell to
# skipped_structure); v2 uses the fragment-parent moiety key.
_CACHE_VERSION = "v2"
_TTL_DAYS = 7

# Affinity cutoff in nM passed to the provider. 10000 nM (10 uM) matches the
# provider's documented example and keeps recall generous; potency calibration
# happens downstream, not by starving the lane.
_DEFAULT_CUTOFF_NM = 10000

_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# Affinity types we know how to interpret quantitatively (all reported in nM
# by this endpoint).  Anything else (kon/koff, percent inhibition, ...) is
# skipped — never coerced into a fake number.
_AFFINITY_TYPE_MAP = {"KI": "Ki", "KD": "Kd", "IC50": "IC50", "EC50": "EC50"}

# "<1.000", ">=50", "2.10" -> (relation, value).  BindingDB embeds the relation
# operator in the affinity string; we preserve it verbatim in the row.
_RELATION_RE = re.compile(
    r"^(<=|>=|<|>|=)?\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)$")

# The provider's actual response key has a typo ("Linds").  Accept the
# corrected spelling too so an upstream fix is not an outage.
_RESPONSE_KEYS = ("getLindsByUniprotsResponse", "getLigandsByUniprotsResponse")


class _SourceUnavailable(Exception):
    """Any condition that must render the source unavailable and must NOT be
    cached (transient HTTP, timeout, connection error, malformed payload, or a
    broken local dependency such as a missing RDKit or DrugCentral snapshot)."""


def _envelope(status: str, candidates: list[dict[str, Any]],
              error: Optional[str], release: Optional[str],
              stats: Optional[dict[str, int]] = None) -> dict[str, Any]:
    return {
        "source": "bindingdb",
        "status": status,
        "candidates": candidates,
        "error": error,
        "release": release,
        "stats": stats or {},
    }


def _fetch_affinities(uniprot_id: str, cutoff_nm: int) -> list[dict[str, Any]]:
    """GET the affinity rows for one UniProt accession.

    Returns the raw ``affinities`` list (possibly empty).  Raises
    _SourceUnavailable on transient HTTP, timeout, connection error, or any
    payload that does not match the verified contract.
    """
    url = f"{BASE_URL}/getLigandsByUniprots"
    params = {
        "uniprot": uniprot_id,
        "cutoff": cutoff_nm,
        "response": "application/json",
    }
    try:
        resp = requests.get(url, params=params,
                            headers={"Accept": "application/json"},
                            timeout=60)
    except requests.exceptions.RequestException as e:
        raise _SourceUnavailable(f"request to {url} failed: {e}") from e

    if resp.status_code in _TRANSIENT_STATUSES:
        raise _SourceUnavailable(
            f"{url} returned transient HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise _SourceUnavailable(
            f"{url} returned unexpected HTTP {resp.status_code}")

    try:
        data = resp.json()
    except ValueError as e:
        raise _SourceUnavailable(f"{url} returned non-JSON body: {e}") from e

    inner = None
    if isinstance(data, dict):
        for key in _RESPONSE_KEYS:
            if key in data:
                inner = data[key]
                break
    if not isinstance(inner, dict) or not isinstance(
            inner.get("affinities"), list):
        raise _SourceUnavailable(
            f"{url} returned a payload that does not match the verified "
            "getLigandsByUniprots contract")
    rows = inner["affinities"]
    # A NON-EMPTY list in which NO row matches the verified row contract is a
    # malformed payload, not a genuine "no binders" — failing closed here is
    # what keeps a contract change from being cached as a healthy empty.
    if rows and not any(_row_shape_ok(r) for r in rows):
        raise _SourceUnavailable(
            f"{url} returned {len(rows)} affinity rows, none matching the "
            "verified row contract")
    return rows


def _row_shape_ok(row: Any) -> bool:
    """Minimal verified row contract: a dict carrying monomerid, smile and an
    affinity value (fields the live API always emits)."""
    return (isinstance(row, dict)
            and bool(str(row.get("monomerid") or "").strip())
            and bool(str(row.get("smile") or "").strip())
            and bool(str(row.get("affinity") or "").strip()))


def parse_affinity(raw: Any) -> Optional[tuple[str, float]]:
    """Parse a BindingDB affinity string into (relation, value_nM).

    Returns None for anything unparseable (the row is skipped, never coerced).
    """
    m = _RELATION_RE.match(str(raw or "").strip())
    if not m:
        return None
    relation = m.group(1) or "="
    try:
        value = float(m.group(2))
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return relation, value


def pchembl_from_nm(value_nm: float) -> float:
    """-log10(molar) potency from an nM affinity (same scale as pChEMBL)."""
    return round(9.0 - math.log10(value_nm), 4)


def _moiety_key(smiles: str) -> str:
    """Active-moiety key: largest organic fragment, neutralized, canonical.

    This RDKit build has no InChI support (``Chem.MolToInChIKey`` is absent),
    so the lane identifies moieties by fragment-parent + neutralized canonical
    isomeric SMILES instead.  Applied identically to BindingDB rows and to the
    DrugCentral snapshot's structures, it collapses salt/ionization variants
    (sodium acetate == acetic acid) while keeping esters and stereo distinct.
    Returns '' ONLY for SMILES that won't parse (the row is skipped, never
    coerced).  A missing RDKit — or any standardization failure — is an
    environment failure and must degrade the whole lane VISIBLY (unavailable,
    never cached), never silently skip every row.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem.MolStandardize import rdMolStandardize
    except ImportError as e:  # pragma: no cover - environment failure
        raise _SourceUnavailable(f"rdkit unavailable: {e}") from e
    if not smiles:
        return ""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return ""
    try:
        parent = rdMolStandardize.FragmentParent(mol)
        parent = rdMolStandardize.Uncharger().uncharge(parent)
        return Chem.MolToSmiles(parent, isomericSmiles=True)
    except Exception as e:
        # The molecule PARSED, so this is a toolchain failure, not bad data:
        # fail the lane visibly instead of quietly skipping every row into a
        # cacheable "empty".
        raise _SourceUnavailable(
            f"rdkit standardization failed for a parsed molecule: {e}") from e


def get_target_interactions(
    uniprot_id: str,
    *,
    cutoff_nm: int = _DEFAULT_CUTOFF_NM,
    repurposing_only: bool = True,
) -> dict[str, Any]:
    """Target-first BindingDB lane: approved-drug binders of one UniProt target.

    With ``repurposing_only`` (the production default) only binders whose
    InChIKey connectivity block matches an established DrugCentral product are
    returned.  Every kept row carries ``approved_struct_id`` so downstream can
    anchor approval evidence on the DrugCentral lineage identity.
    """
    accession = str(uniprot_id or "").strip().upper()
    if not accession:
        return _envelope("unavailable", [], "no uniprot_id supplied", None)

    key = make_key("bindingdb.get_target_interactions", _CACHE_VERSION,
                   accession, cutoff_nm, repurposing_only)
    cached = cache_get(key)
    if isinstance(cached, dict) and cached.get("status") in ("ok", "empty"):
        return cached

    try:
        rows = _fetch_affinities(accession, cutoff_nm)
        approved_map: dict[str, dict[str, Any]] = {}
        if repurposing_only:
            # A missing/corrupt snapshot (or broken RDKit) makes the approved
            # filter impossible; that must fail VISIBLY, never silently pass
            # unverified rows.
            for entry in drugcentral_local.approved_moiety_rows():
                k = _moiety_key(entry["smiles"])
                if k and k not in approved_map:
                    approved_map[k] = entry
    except (drugcentral_local.SnapshotCorrupt, _SourceUnavailable) as e:
        return _envelope("unavailable", [], str(e), None)

    stats = {"returned": len(rows), "parsed": 0, "skipped_affinity": 0,
             "skipped_structure": 0, "skipped_unapproved": 0}
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            stats["skipped_affinity"] += 1
            continue
        affinity_type = _AFFINITY_TYPE_MAP.get(
            str(row.get("affinity_type") or "").strip().upper())
        parsed = parse_affinity(row.get("affinity"))
        if affinity_type is None or parsed is None:
            stats["skipped_affinity"] += 1
            continue
        relation, value_nm = parsed
        smiles = str(row.get("smile") or "").strip()
        if not smiles:
            stats["skipped_structure"] += 1
            continue
        try:
            key = _moiety_key(smiles)
        except _SourceUnavailable as e:
            return _envelope("unavailable", [], str(e), None)
        if not key:
            stats["skipped_structure"] += 1
            continue

        inchikey = ""
        approved_struct_id: Optional[int] = None
        approved_name = ""
        if repurposing_only:
            approved = approved_map.get(key)
            if approved is None:
                stats["skipped_unapproved"] += 1
                continue
            approved_struct_id = approved["struct_id"]
            # The REST response carries no ligand names and no approval state;
            # borrow the snapshot's preferred name AND its real InChIKey so a
            # BindingDB nominee is never nameless downstream and unions with
            # ChEMBL/DrugCentral records by structural identity.
            approved_name = approved["name"]
            inchikey = approved["inchikey"]

        monomer_id = str(row.get("monomerid") or "").strip()
        if not monomer_id:
            stats["skipped_affinity"] += 1
            continue
        stats["parsed"] += 1
        candidates.append({
            "monomer_id": monomer_id,
            "provider_molecule_id": f"BDBM{monomer_id}",
            "name": approved_name,
            "smiles": smiles,
            "inchikey": inchikey,
            "affinity_type": affinity_type,
            "affinity_nm": value_nm,
            "relation": relation,
            "pchembl": pchembl_from_nm(value_nm),
            "pmid": str(row.get("pmid") or "").strip(),
            "doi": str(row.get("doi") or "").strip(),
            "query_target": str(row.get("query") or "").strip(),
            "approved_struct_id": approved_struct_id,
        })

    stats["kept"] = len(candidates)
    status = "ok" if candidates else "empty"
    envelope = _envelope(status, candidates, None, None, stats)
    # Cache only healthy terminal states; "unavailable" returns above are
    # never cached (transient failures must not poison the pool).
    cache_set(key, envelope, ttl_days=_TTL_DAYS)
    return envelope
