"""
Reactome Content Service data source.

Provides pathway-neighbor discovery for a given UniProt protein:
  - Fetches the Reactome pathways the protein participates in.
  - For each pathway (tightest first, ranked by maxDepth ascending), fetches
    the co-participating UniProt-mapped proteins.
  - Deduplicates and ranks by co-occurrence frequency.
  - Caps at max_neighbors to avoid the very broad neighbor sets produced by
    large multi-protein complexes (e.g. 40+ subunit ribosome entries).

Verified Reactome Content Service endpoints (July 2026):
  GET /ContentService/data/mapping/UniProt/{accession}/pathways
      -> list of Pathway objects with {stId, displayName, maxDepth, ...}
  GET /ContentService/data/participants/{stId}/referenceEntities
      -> list of reference entities with {databaseName, identifier, geneName, ...}
      Filters to UniProt proteins automatically (databaseName == "UniProt").
      Small molecules are ChEBI-mapped (excluded).
"""

import time
from typing import Any

import requests

from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://reactome.org/ContentService"
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})

# Examine at most this many pathways per protein, choosing the tightest
# (lowest maxDepth) first. Prevents returning hundreds of neighbors from
# broad housekeeping pathways (translation, metabolism) that would obscure
# the direct-reaction-adjacency relationships we care about.
_TOP_PATHWAYS = 5

_REQUEST_TIMEOUT = 20


def _get(url: str) -> Any:
    """GET url, return parsed JSON or None on any error."""
    try:
        r = _SESSION.get(url, timeout=_REQUEST_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[reactome] WARNING: GET {url} failed: {e}")
        return None


def get_pathway_neighbors(
    uniprot_id: str,
    max_neighbors: int = 10,
) -> list[dict[str, Any]]:
    """
    Return up to max_neighbors UniProt proteins that co-participate in the
    same Reactome pathway(s) as the query protein.

    Only the _TOP_PATHWAYS tightest (lowest maxDepth) pathways are examined
    to prefer direct reaction-adjacency relationships over broad complex
    membership.

    Returns:
        List of dicts: {uniprot_id, gene_name, pathway_count}
        pathway_count = number of examined pathways the neighbor appeared in.
        Returns [] gracefully on any API failure (never crashes the pipeline).
    """
    cache_key = make_key("reactome_pathway_neighbors_v1", uniprot_id, max_neighbors)
    cached = get(cache_key)
    if cached is not None:
        return cached

    # Step 1: fetch all pathways the protein participates in.
    pathways = _get(f"{BASE_URL}/data/mapping/UniProt/{uniprot_id}/pathways") or []
    if not isinstance(pathways, list) or not pathways:
        print(f"[reactome] no pathways found for {uniprot_id}")
        cache_set(cache_key, [], ttl_days=30)
        return []

    # Step 2: sort by maxDepth ascending (tight pathways first) and cap.
    # Lower maxDepth = the pathway sits deeper in the hierarchy = more
    # specific = less likely to be a broad housekeeping pathway.
    pathways_sorted = sorted(
        pathways,
        key=lambda p: (p.get("maxDepth", 99), p.get("stId", "")),
    )
    selected = pathways_sorted[:_TOP_PATHWAYS]
    print(f"[reactome] {uniprot_id}: {len(pathways)} pathway(s) found; "
          f"examining {len(selected)} tightest (maxDepth range "
          f"{selected[0].get('maxDepth','?')}–{selected[-1].get('maxDepth','?')})")

    # Step 3: for each pathway, fetch reference entities.
    freq: dict[str, dict[str, Any]] = {}  # uniprot_id -> {gene_name, pathway_count}
    for pw in selected:
        st_id = pw.get("stId")
        if not st_id:
            continue
        entities = _get(f"{BASE_URL}/data/participants/{st_id}/referenceEntities") or []
        if not isinstance(entities, list):
            continue
        time.sleep(0.05)   # gentle rate limiting
        for ent in entities:
            if ent.get("databaseName") != "UniProt":
                continue  # skip ChEBI small molecules / other db entries
            uid = ent.get("identifier", "")
            if not uid or uid == uniprot_id:
                continue  # exclude the query protein itself
            if uid not in freq:
                genes = ent.get("geneName") or []
                gene = genes[0] if genes else uid
                freq[uid] = {"uniprot_id": uid, "gene_name": gene, "pathway_count": 0}
            freq[uid]["pathway_count"] += 1

    if not freq:
        print(f"[reactome] no UniProt neighbors found for {uniprot_id} "
              f"in the examined pathways")
        cache_set(cache_key, [], ttl_days=30)
        return []

    # Step 4: rank by co-occurrence frequency (shared across more tight pathways
    # first), then alphabetically by gene_name for determinism.
    ranked = sorted(
        freq.values(),
        key=lambda x: (-x["pathway_count"], x["gene_name"]),
    )[:max_neighbors]

    print(f"[reactome] {uniprot_id}: {len(ranked)} pathway neighbor(s) "
          f"(top: {ranked[0]['gene_name']} count={ranked[0]['pathway_count']})")

    cache_set(cache_key, ranked, ttl_days=30)
    return ranked
