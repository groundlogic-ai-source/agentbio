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
import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://webservice.thebiogrid.org/interactions/"

# Applied to every returned record so the "not a mechanism" caveat travels with the data.
INTERACTION_LABEL = "physical/genetic interaction, not mechanism"


def get_interactions(gene_symbol: str, tax_id: int = 9606, max_results: int = 200) -> list[dict[str, Any]]:
    """
    Return a list of interacting genes for `gene_symbol` from BioGRID.

    Each element:
      {
        biogrid_interaction_id: str,
        interactor_symbol: str,        # the partner gene (not the query gene)
        experimental_system: str,      # e.g. "Affinity Capture-MS"
        experimental_system_type: str, # "physical" or "genetic"
        label: "physical/genetic interaction, not mechanism",
      }

    Returns [] on missing API key or any error (logged as a warning) — callers
    must treat an empty list as "no data", never as "no interactions exist".
    """
    api_key = os.environ.get("BIOGRID_API_KEY")
    if not api_key:
        print("[biogrid] WARNING: BIOGRID_API_KEY not set; returning no interactions")
        return []

    cache_key = make_key("get_interactions", gene_symbol, tax_id, max_results)
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
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()  # dict keyed by BioGRID interaction id

        query_upper = gene_symbol.upper()
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
            interactions.append({
                "biogrid_interaction_id": str(rec.get("BIOGRID_INTERACTION_ID", biogrid_id)),
                "interactor_symbol": interactor,
                "experimental_system": rec.get("EXPERIMENTAL_SYSTEM"),
                "experimental_system_type": rec.get("EXPERIMENTAL_SYSTEM_TYPE"),
                "label": INTERACTION_LABEL,
            })
    except Exception as e:
        print(f"[biogrid] WARNING: interaction query failed for '{gene_symbol}': {e}")
        return []

    cache_set(cache_key, interactions, ttl_days=7)
    return interactions
