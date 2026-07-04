"""
openFDA drug adverse event data (FAERS) — https://api.fda.gov/drug/event.json.

Returns the most-frequently reported adverse event terms for a drug. This is a
real-world safety signal from spontaneous reports; it is NOT a causal or
incidence measure. No API key required (anonymous rate limits apply).
"""

import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://api.fda.gov/drug/event.json"
LABEL_URL = "https://api.fda.gov/drug/label.json"


def get_label_indications(drug_name: str) -> dict[str, Any]:
    """
    Return the FDA structured-product-label 'Indications and Usage' free text for
    a drug from openFDA (https://api.fda.gov/drug/label.json).

    This is the field where mutation-specific approvals are spelled out verbatim
    (e.g. "KRAS G12C-mutated ... NSCLC", "EGFR exon 19 deletions"). The ChEMBL
    structured indication terms are mutation-stripped, so the label text is the
    reliable source for the mutation-specificity DISCLOSURE flag.

    Returns:
      { drug: str, indications_text: str, source: str | None, error: str | None }

    A 404 (no label on file) is a legitimate empty result, not an error.
    """
    cache_key = make_key("get_label_indications", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "drug": drug_name,
        "indications_text": "",
        "source": None,
        "error": None,
    }

    params = {
        "search": (f'openfda.generic_name:"{drug_name}" '
                   f'OR openfda.brand_name:"{drug_name}"'),
        "limit": 1,
    }

    try:
        resp = requests.get(LABEL_URL, params=params, timeout=30)
        if resp.status_code == 404:
            cache_set(cache_key, result, ttl_days=30)
            return result
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        if rows:
            iu = rows[0].get("indications_and_usage") or []
            if isinstance(iu, list):
                iu = " ".join(iu)
            result["indications_text"] = (iu or "").strip()
            result["source"] = "openfda_label"
    except Exception as e:
        result["error"] = str(e)
        print(f"[openfda] WARNING: label indications query failed for '{drug_name}': {e}")

    cache_set(cache_key, result, ttl_days=30)
    return result


def get_adverse_events(drug_name: str, limit: int = 15) -> dict[str, Any]:
    """
    Return the top reported adverse event terms + counts for `drug_name` from FAERS.

    Returns:
      {
        drug: str,
        adverse_events: [{term: str, count: int}],   # ranked, highest count first
        total_event_terms: int,
        error: str | None,
      }

    openFDA returns HTTP 404 when there are zero matching reports; that is treated
    as "no signal found" (empty list), not an error.
    """
    cache_key = make_key("get_adverse_events", drug_name, limit)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "drug": drug_name,
        "adverse_events": [],
        "total_event_terms": 0,
        "error": None,
    }

    params = {
        "search": f'patient.drug.medicinalproduct:"{drug_name}"',
        "count": "patient.reaction.reactionmeddrapt.exact",
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code == 404:
            # No matching reports — legitimate empty result.
            cache_set(cache_key, result, ttl_days=7)
            return result
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("results", [])
        events = [{"term": r.get("term"), "count": int(r.get("count", 0))} for r in rows[:limit]]
        result["adverse_events"] = events
        result["total_event_terms"] = len(rows)
    except Exception as e:
        result["error"] = str(e)
        print(f"[openfda] WARNING: adverse event query failed for '{drug_name}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
