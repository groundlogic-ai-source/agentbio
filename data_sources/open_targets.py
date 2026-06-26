"""
Open Targets Platform GraphQL API.
Resolves disease names/IDs to EFO IDs, then fetches target-disease associations.
"""

import requests
from typing import Any, Optional
from cache.cache import get, set as cache_set, make_key

GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"


def _graphql(query: str, variables: dict) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def search_disease_efo(disease_name: str) -> Optional[str]:
    """
    Search Open Targets for a disease by name and return its EFO ID.
    Returns None if not found.
    """
    cache_key = make_key("search_disease_efo", disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query SearchDisease($q: String!) {
      search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 5}) {
        hits {
          id
          entity
          name
          score
        }
      }
    }
    """
    try:
        data = _graphql(query, {"q": disease_name})
        hits = data.get("data", {}).get("search", {}).get("hits", [])
        disease_hits = [h for h in hits if h.get("entity") == "disease"]
        if not disease_hits:
            cache_set(cache_key, None, ttl_days=7)
            return None
        efo_id = disease_hits[0]["id"]
        cache_set(cache_key, efo_id, ttl_days=7)
        return efo_id
    except Exception as e:
        print(f"[open_targets] WARNING: EFO search failed for '{disease_name}': {e}")
        return None


def get_target_disease_score(disease_efo_id: str) -> list[dict[str, Any]]:
    """
    Fetch top target-disease associations for a given EFO ID.
    Returns [{target_symbol, ensembl_id, uniprot_id, association_score}] sorted descending.
    """
    cache_key = make_key("get_target_disease_score", disease_efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query DiseaseTargets($efoId: String!, $size: Int!) {
      disease(efoId: $efoId) {
        associatedTargets(page: {index: 0, size: $size}) {
          rows {
            target {
              id
              approvedSymbol
              proteinIds {
                id
                source
              }
            }
            score
          }
        }
      }
    }
    """
    results = []
    try:
        data = _graphql(query, {"efoId": disease_efo_id, "size": 10})
        disease_data = data.get("data", {}).get("disease") or {}
        rows = disease_data.get("associatedTargets", {}).get("rows", [])
        for row in rows:
            target = row.get("target", {})
            ensembl_id = target.get("id", "")
            symbol = target.get("approvedSymbol", "")
            uniprot_id = None
            for pid in target.get("proteinIds", []):
                if pid.get("source") in ("uniprot_swissprot", "uniprot_trembl", "uniprot"):
                    uniprot_id = pid.get("id")
                    break
            results.append({
                "target_symbol": symbol,
                "ensembl_id": ensembl_id,
                "uniprot_id": uniprot_id,
                "association_score": row.get("score", 0.0),
            })
        results.sort(key=lambda x: x["association_score"], reverse=True)
    except Exception as e:
        print(f"[open_targets] WARNING: association query failed for '{disease_efo_id}': {e}")

    cache_set(cache_key, results, ttl_days=7)
    return results
