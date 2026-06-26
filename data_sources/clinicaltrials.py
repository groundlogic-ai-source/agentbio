"""
ClinicalTrials.gov API v2 — trial history lookup.
"""

import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

NEGATIVE_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
COMPLETED_STATUSES = {"COMPLETED"}


def _search_trials(drug_name: str, disease_name: str) -> list[dict]:
    query = f"{drug_name} AND {disease_name}"
    params = {
        "query.term": query,
        "fields": "NCTId,BriefTitle,OverallStatus,WhyStopped,ResultsFirstPostDate",
        "pageSize": 100,
        "format": "json",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("studies", [])
    except Exception as e:
        print(f"[clinicaltrials] WARNING: API call failed ({e})")
        return []


def check_prior_trials(drug_name: str, disease_name: str) -> dict[str, Any]:
    """
    Returns:
      - trials: list of {nct_id, title, status, why_stopped, has_results}
      - has_negative_repurposing_result: True if any trial for this drug+disease
        pair was terminated, withdrawn, or suspended (for this exact pair).
      - trial_count: total trials found
    """
    cache_key = make_key("check_prior_trials", drug_name, disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    raw_studies = _search_trials(drug_name, disease_name)

    trials = []
    has_negative = False

    for study in raw_studies:
        protocol = study.get("protocolSection", {})
        id_module = protocol.get("identificationModule", {})
        status_module = protocol.get("statusModule", {})
        results_module = study.get("resultsSection", {})

        nct_id = id_module.get("nctId", "")
        title = id_module.get("briefTitle", "")
        status = status_module.get("overallStatus", "UNKNOWN")
        why_stopped = status_module.get("whyStopped", None)
        has_results = bool(results_module)

        if status.upper() in NEGATIVE_STATUSES:
            has_negative = True

        trials.append({
            "nct_id": nct_id,
            "title": title,
            "status": status,
            "why_stopped": why_stopped,
            "has_results": has_results,
        })

    result = {
        "trials": trials,
        "has_negative_repurposing_result": has_negative,
        "trial_count": len(trials),
    }
    cache_set(cache_key, result, ttl_days=3)
    return result
