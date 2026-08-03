"""
IUPHAR/BPS Guide to PHARMACOLOGY (GtoPdb) ligand–target interaction data.

Verified public API base: https://www.guidetopharmacology.org/services

This adapter is TARGET-FIRST: given a UniProt accession it resolves the GtoPdb
target(s), pulls the approved-drug interactions recorded against those targets
(Homo sapiens only), and normalises each into a candidate row carrying provider
IDs, structure (SMILES / InChIKey), the pharmacological action/type, the
affinity, the target identity, literature refs, and per-row evidence records.

It recovers approved drugs that ChEMBL's IC50/Ki assay pool misses because
GtoPdb records the curated ligand→target relationship directly rather than
requiring a qualifying bioactivity assay.

Cache discipline (mirrors data_sources/chembl.py and openfda.py):
  - Only HEALTHY, non-malformed responses are cached.
  - A HEALTHY *empty* result (the target resolves but has no approved-drug
    interactions) MAY be cached: the emptiness is post-filter and genuine.
  - 429 / 5xx / timeouts / connection errors / invalid (non-JSON or
    wrong-shape) payloads render the source UNAVAILABLE and are NEVER cached,
    so a transient outage cannot poison the cache as "no approved drugs".

Common return envelope (shared shape across the v2 source adapters):
  {
    "source":    "gtopdb",
    "status":    "ok" | "empty" | "unavailable",
    "candidates": [ {...}, ... ],
    "error":     str | None,
    "release":   str | None,      # provider release/version if advertised
  }
"""

import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://www.guidetopharmacology.org/services"

# Cache key version. Bump when the normalized candidate shape or the caching
# gate changes so stale rows from an older schema can never be served.
_CACHE_VERSION = "v1"
_TTL_DAYS = 7

# HTTP statuses that mean "the source is temporarily unavailable" — never cache.
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class _SourceUnavailable(Exception):
    """Raised for any condition that must render the source unavailable and
    must NOT be cached (transient HTTP, timeout, connection error, or a
    payload whose shape is not what the verified API contract promises)."""


def _envelope(status: str, candidates: list[dict[str, Any]],
              error: Optional[str], release: Optional[str]) -> dict[str, Any]:
    return {
        "source": "gtopdb",
        "status": status,
        "candidates": candidates,
        "error": error,
        "release": release,
    }


def _get_json(path: str, params: Optional[dict] = None) -> Any:
    """GET {BASE_URL}{path} and return parsed JSON.

    Raises _SourceUnavailable on transient HTTP, timeout, connection error, or
    a non-JSON body. Callers translate that into an 'unavailable' envelope and
    skip caching. A 404 is returned to the caller as None (a legitimate
    "no such resource" answer, distinct from an outage).
    """
    url = f"{BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params,
                            headers={"Accept": "application/json"}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise _SourceUnavailable(f"request to {url} failed: {e}") from e

    if resp.status_code == 404:
        return None
    if resp.status_code in _TRANSIENT_STATUSES:
        raise _SourceUnavailable(
            f"{url} returned transient HTTP {resp.status_code}")
    if resp.status_code != 200:
        # Any other non-200 is unexpected; treat as unavailable rather than
        # silently coercing to empty.
        raise _SourceUnavailable(
            f"{url} returned unexpected HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as e:
        raise _SourceUnavailable(f"{url} returned non-JSON body: {e}") from e


def _resolve_target_ids(uniprot_id: str) -> list[int]:
    """Resolve a UniProt accession to GtoPdb integer target IDs.

    Endpoint: /targets?accession={uniprot_id}
    The contract is a JSON *list* of target objects each carrying 'targetId'.
    A non-list payload is malformed → unavailable (raised).
    """
    data = _get_json("/targets", {"accession": uniprot_id})
    if data is None:
        return []
    if not isinstance(data, list):
        raise _SourceUnavailable("/targets did not return a JSON list")
    ids: list[int] = []
    for t in data:
        if not isinstance(t, dict):
            raise _SourceUnavailable("/targets row was not an object")
        tid = t.get("targetId")
        if isinstance(tid, int):
            ids.append(tid)
    return ids


def _fetch_interactions(target_id: int) -> list[dict[str, Any]]:
    """Approved, Human interactions recorded against a GtoPdb target.

    Endpoint: /targets/{targetId}/interactions?species=Human&approved=true
    Contract: a JSON list of interaction objects. Non-list → unavailable.
    """
    data = _get_json(f"/targets/{target_id}/interactions",
                    {"species": "Human", "approved": "true"})
    if data is None:
        return []
    if not isinstance(data, list):
        raise _SourceUnavailable("interactions did not return a JSON list")
    for row in data:
        if not isinstance(row, dict):
            raise _SourceUnavailable("interaction row was not an object")
    return data


def _fetch_ligand(ligand_id: int) -> Optional[dict[str, Any]]:
    """/ligands/{id} — ligand metadata (name, type, approval, withdrawn)."""
    data = _get_json(f"/ligands/{ligand_id}")
    if data is None:
        return None
    if not isinstance(data, dict):
        raise _SourceUnavailable("/ligands did not return a JSON object")
    return data


def _fetch_structure(ligand_id: int) -> Optional[dict[str, Any]]:
    """/ligands/{id}/structure — SMILES / InChI / InChIKey."""
    data = _get_json(f"/ligands/{ligand_id}/structure")
    if data is None:
        return None
    if not isinstance(data, dict):
        raise _SourceUnavailable("/structure did not return a JSON object")
    return data


def _fetch_database_links(ligand_id: int) -> list[dict[str, Any]]:
    """/ligands/{id}/databaseLinks — cross-references (ChEMBL, DrugBank, ...)."""
    data = _get_json(f"/ligands/{ligand_id}/databaseLinks")
    if data is None:
        return []
    if not isinstance(data, list):
        raise _SourceUnavailable("/databaseLinks did not return a JSON list")
    for row in data:
        if not isinstance(row, dict):
            raise _SourceUnavailable("/databaseLinks row was not an object")
    return data


def _extract_xref(db_links: list[dict[str, Any]], database: str) -> Optional[str]:
    """Return the accession for a named cross-reference database, if present."""
    for link in db_links:
        if (link.get("database") or "").strip().lower() == database.strip().lower():
            acc = link.get("accession")
            if acc:
                return str(acc)
    return None


def _normalize_refs(interaction: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the interaction's literature refs into a compact list."""
    refs_out: list[dict[str, Any]] = []
    for ref in interaction.get("refs") or []:
        if not isinstance(ref, dict):
            continue
        pmid = ref.get("pmid")
        refs_out.append({
            "reference_id": ref.get("referenceId"),
            "pmid": str(pmid) if pmid else None,
            "title": ref.get("title"),
            "year": ref.get("year"),
        })
    return refs_out


def _normalize_candidate(interaction: dict[str, Any],
                         ligand: Optional[dict[str, Any]],
                         structure: Optional[dict[str, Any]],
                         db_links: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one normalized approved-drug candidate row from an interaction plus
    the resolved ligand / structure / cross-reference payloads."""
    ligand = ligand or {}
    structure = structure or {}
    ligand_id = interaction.get("ligandId")

    refs = _normalize_refs(interaction)

    # Evidence record: the auditable provenance for this candidate row.
    evidence: list[dict[str, Any]] = [{
        "type": "gtopdb_interaction",
        "interaction_id": interaction.get("interactionId"),
        "target_id": interaction.get("targetId"),
        "affinity": interaction.get("affinity"),
        "affinity_parameter": interaction.get("affinityParameter"),
        "original_affinity": interaction.get("originalAffinity"),
        "original_affinity_type": interaction.get("originalAffinityType"),
        "action": interaction.get("action"),
        "action_type": interaction.get("type"),
        "endogenous": interaction.get("endogenous"),
        "primary_target": interaction.get("primaryTarget"),
        "refs": refs,
    }]

    affinity_val: Optional[float] = None
    raw_affinity = interaction.get("affinity")
    if raw_affinity not in (None, ""):
        try:
            affinity_val = float(raw_affinity)
        except (TypeError, ValueError):
            affinity_val = None

    return {
        # Provider identity
        "source": "gtopdb",
        "provider_ligand_id": ligand_id,
        "provider_interaction_id": interaction.get("interactionId"),
        "chembl_id": _extract_xref(db_links, "ChEMBL Ligand"),
        "drugbank_id": _extract_xref(db_links, "DrugBank Ligand"),
        # Drug identity
        "name": ligand.get("name") or interaction.get("ligandName"),
        "inn": ligand.get("inn"),
        "ligand_type": ligand.get("type"),
        "is_approved": bool(ligand.get("approved", True)),
        "is_withdrawn": bool(ligand.get("withdrawn", False)),
        # Structure
        "smiles": structure.get("smiles"),
        "inchi": structure.get("inchi"),
        "inchikey": structure.get("inchiKey"),
        "iupac_name": structure.get("iupacName"),
        # Pharmacology
        "action": interaction.get("action"),
        "action_type": interaction.get("type"),
        "selectivity": interaction.get("selectivity"),
        "affinity": affinity_val,
        "affinity_parameter": interaction.get("affinityParameter"),
        # Target identity
        "target_id": interaction.get("targetId"),
        "target_name": interaction.get("targetName"),
        "target_species": interaction.get("targetSpecies"),
        # Provenance
        "refs": refs,
        "evidence": evidence,
    }


def get_target_interactions(uniprot_id: str,
                            approved_only: bool = True) -> dict[str, Any]:
    """Target-first approved-drug candidate lookup for a UniProt accession.

    Resolves GtoPdb target(s) for the accession, pulls their Human approved
    interactions, and normalises each into a candidate row. Deduplicates by
    provider ligand id so a drug hitting several resolved targets appears once
    (its evidence records are merged).

    Returns the common envelope:
      {source, status, candidates, error, release}

    status:
      "ok"          — target resolved and ≥1 candidate found.
      "empty"       — target resolved but no approved-drug interactions
                      (healthy, cacheable).
      "unavailable" — transient failure / malformed payload (NOT cached).
    """
    cache_key = make_key(f"gtopdb_get_target_interactions_{_CACHE_VERSION}",
                        uniprot_id, approved_only)
    cached = get(cache_key)
    if cached is not None:
        return cached

    try:
        target_ids = _resolve_target_ids(uniprot_id)
        if not target_ids:
            # Genuine no-match: the accession has no GtoPdb target. This is a
            # healthy empty and is cacheable.
            result = _envelope("empty", [], None, None)
            cache_set(cache_key, result, ttl_days=_TTL_DAYS)
            return result

        by_ligand: dict[Any, dict[str, Any]] = {}
        for tid in target_ids:
            for interaction in _fetch_interactions(tid):
                ligand_id = interaction.get("ligandId")
                if ligand_id is None:
                    continue

                # Resolve per-ligand payloads once, reusing for dedup merges.
                if ligand_id in by_ligand:
                    # Same drug on another resolved target — merge evidence.
                    by_ligand[ligand_id]["evidence"].extend(
                        _normalize_refs_evidence(interaction))
                    continue

                ligand = _fetch_ligand(ligand_id)
                if approved_only and ligand is not None \
                        and not ligand.get("approved", False):
                    # The interactions endpoint already filters approved=true,
                    # but the ligand record is authoritative; drop any that the
                    # server contract did not actually mark approved.
                    continue

                structure = _fetch_structure(ligand_id)
                db_links = _fetch_database_links(ligand_id)
                by_ligand[ligand_id] = _normalize_candidate(
                    interaction, ligand, structure, db_links)

        candidates = list(by_ligand.values())

    except _SourceUnavailable as e:
        # Transient / malformed → unavailable, and DO NOT cache. A cached empty
        # here would masquerade as "no approved drugs" for the whole TTL.
        print(f"[gtopdb] WARNING: source unavailable for '{uniprot_id}': {e}")
        return _envelope("unavailable", [], str(e), None)

    status = "ok" if candidates else "empty"
    result = _envelope(status, candidates, None, None)
    cache_set(cache_key, result, ttl_days=_TTL_DAYS)
    return result


def _normalize_refs_evidence(interaction: dict[str, Any]) -> list[dict[str, Any]]:
    """Evidence record for a *duplicate* interaction (same ligand, other
    target) so the merged candidate keeps provenance for every target hit."""
    return [{
        "type": "gtopdb_interaction",
        "interaction_id": interaction.get("interactionId"),
        "target_id": interaction.get("targetId"),
        "affinity": interaction.get("affinity"),
        "affinity_parameter": interaction.get("affinityParameter"),
        "action": interaction.get("action"),
        "action_type": interaction.get("type"),
        "refs": _normalize_refs(interaction),
    }]
