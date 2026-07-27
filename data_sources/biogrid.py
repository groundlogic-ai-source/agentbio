"""
BioGRID interaction data (https://webservice.thebiogrid.org).

IMPORTANT SCIENTIFIC CONSTRAINT:
BioGRID reports *physical and genetic interactions*. These are NOT directional
mechanism claims — an interaction does NOT mean gene A activates or inhibits gene
B. Every record returned by this wrapper is labelled accordingly, and downstream
code / LLM prompts must not describe these edges as activating/inhibiting unless
BioGRID's own EXPERIMENTAL_SYSTEM data explicitly says so.

Requires BIOGRID_API_KEY from the environment (Replit Secrets).
"""

import os
import time
import threading
import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://webservice.thebiogrid.org/interactions/"

# Applied to every returned record so the "not a mechanism" caveat travels with the data.
INTERACTION_LABEL = "physical/genetic interaction, not mechanism"

# Rate-limit: no more than 1 request per second to avoid abuse-detection revocation.
_RATE_LIMIT_DELAY = 1.0  # seconds between outbound requests
_rate_lock = threading.Lock()
_last_request_time: float = 0.0


def get_interactions(gene_symbol: str, tax_id: int = 9606, max_results: int = 200) -> dict[str, Any]:
    """
    Return interaction data for `gene_symbol` from BioGRID as a status dict:
      {
        interactions: list[dict],  # each element: {biogrid_interaction_id, interactor_symbol,
                                   #   experimental_system, experimental_system_type, label}
        query_status: str,         # "ok" | "no_data" | "query_failed" | "no_key"
      }

    IMPORTANT: always inspect `query_status` before treating an empty `interactions`
    list as "no interactions exist" — it may instead mean the query failed.

    query_status values:
      "ok"           — query succeeded; interactions may be empty (genuinely none found)
      "no_data"      — query succeeded; API returned 0 records
      "query_failed" — API call raised an exception or returned HTTP error
      "no_key"       — BIOGRID_API_KEY environment variable not set
    """
    api_key = os.environ.get("BIOGRID_API_KEY")
    if not api_key:
        print("[biogrid] WARNING: BIOGRID_API_KEY not set; returning no interactions")
        return {"interactions": [], "query_status": "no_key"}

    # v4: interSpeciesExcluded + selfInteractionsExcluded added to query;
    # post-processing deduplicates case-insensitively and filters residual self-hits.
    cache_key = make_key("get_interactions_v4", gene_symbol, tax_id, max_results)
    cached = get(cache_key)
    if cached is not None:
        return cached

    interactions: list[dict[str, Any]] = []
    params = {
        "accesskey": api_key,
        "format": "json",
        "geneList": gene_symbol,
        "searchNames": "true",
        "taxId": tax_id,
        "includeInteractors": "true",
        "max": max_results,
        # Exclude interactions between different organisms (removes virus-host
        # records, e.g. SARS-CoV-2 ORF3a appearing in human interactomes).
        "interSpeciesExcluded": "true",
        # Exclude self-interactions at the API level as first-pass filter.
        "selfInteractionsExcluded": "true",
    }

    try:
        # Enforce inter-request delay to avoid abuse-detection revocation.
        global _last_request_time
        with _rate_lock:
            elapsed = time.monotonic() - _last_request_time
            if elapsed < _RATE_LIMIT_DELAY:
                time.sleep(_RATE_LIMIT_DELAY - elapsed)
            _last_request_time = time.monotonic()

        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()  # dict keyed by BioGRID interaction id

        query_upper = gene_symbol.upper()
        raw_interactions: list[dict[str, Any]] = []
        for biogrid_id, rec in data.items():
            sym_a = rec.get("OFFICIAL_SYMBOL_A")
            sym_b = rec.get("OFFICIAL_SYMBOL_B")
            # The interactor is whichever side is not the query gene.
            if sym_a and sym_a.upper() == query_upper:
                interactor = sym_b
            elif sym_b and sym_b.upper() == query_upper:
                interactor = sym_a
            else:
                interactor = sym_b or sym_a
            if not interactor:
                continue
            raw_interactions.append({
                "biogrid_interaction_id": str(rec.get("BIOGRID_INTERACTION_ID", biogrid_id)),
                "interactor_symbol": interactor,
                "experimental_system": rec.get("EXPERIMENTAL_SYSTEM"),
                "experimental_system_type": rec.get("EXPERIMENTAL_SYSTEM_TYPE"),
                "label": INTERACTION_LABEL,
            })

        # Post-processing quality filters:
        #   1. Self-hit guard: remove any record where the interactor is the
        #      query gene itself (belt-and-suspenders against the API filter).
        #   2. Case-fold deduplication: BioGRID returns gene symbols with
        #      inconsistent casing (e.g. AP2A1 vs Ap2a1 as separate records).
        #      Deduplicate by uppercase symbol and normalise to uppercase.
        seen_upper: set[str] = set()
        for ia in raw_interactions:
            sym_upper = ia["interactor_symbol"].upper()
            if sym_upper == query_upper:
                continue  # residual self-hit
            if sym_upper in seen_upper:
                continue  # duplicate from different-casing record
            seen_upper.add(sym_upper)
            ia["interactor_symbol"] = sym_upper  # normalise to uppercase
            interactions.append(ia)

        query_status = "ok" if interactions else "no_data"
    except Exception as e:
        print(f"[biogrid] WARNING: interaction query failed for '{gene_symbol}': {e}")
        return {"interactions": [], "query_status": "query_failed"}

    result = {"interactions": interactions, "query_status": query_status}
    cache_set(cache_key, result, ttl_days=7)
    return result
