"""
ChEMBL bioactivity data.
Resolves UniProt IDs to ChEMBL targets (Homo sapiens only),
then fetches IC50/Ki bioactivity records with confidence_score >= 8.
"""

import math
import statistics
import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"


def _get_json(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _resolve_target_chembl_id(uniprot_id: str) -> list[str]:
    """
    Resolve a UniProt accession to Homo sapiens ChEMBL target IDs.
    Returns a list (may be empty).
    """
    url = f"{BASE_URL}/target.json"
    params = {
        "target_components__accession": uniprot_id,
        "organism": "Homo sapiens",
        "limit": 50,
    }
    data = _get_json(url, params)
    targets = data.get("targets", [])
    ids = []
    for t in targets:
        organism = t.get("organism", "")
        if "Homo sapiens" in organism or organism == "":
            ids.append(t["target_chembl_id"])
    return ids


def _fetch_activities(target_chembl_id: str) -> list[dict[str, Any]]:
    """
    Fetch IC50/Ki activities with pchembl_value present, confidence >= 8.
    Paginates up to 1000 records.
    """
    url = f"{BASE_URL}/activity.json"
    params = {
        "target_chembl_id": target_chembl_id,
        "standard_type__in": "IC50,Ki",
        "pchembl_value__isnull": "false",
        "limit": 1000,
        "offset": 0,
    }
    data = _get_json(url, params)
    activities = data.get("activities", [])
    return [
        a for a in activities
        if a.get("confidence_score") is not None and int(a["confidence_score"]) >= 8
    ]


def get_target_bioactivity_count(uniprot_id: str) -> dict[str, Any]:
    """
    For a UniProt ID, resolve to Homo sapiens ChEMBL target(s) and return:
      - count: number of qualifying IC50/Ki records (confidence >= 8)
      - median_pchembl: median pChEMBL value across qualifying records
      - target_chembl_ids: list of ChEMBL IDs used
      - pooled_across_multiple_targets: bool flag (True if > 1 target ID matched)
      - low_confidence_excluded: always True (we filter < 8 out)

    IMPORTANT: Values are NOT pooled across different target_chembl_ids silently.
    When pooled_across_multiple_targets is True, interpret with caution.
    """
    cache_key = make_key("get_target_bioactivity_count", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "count": 0,
        "median_pchembl": None,
        "target_chembl_ids": [],
        "pooled_across_multiple_targets": False,
        "low_confidence_excluded": True,
    }

    try:
        target_ids = _resolve_target_chembl_id(uniprot_id)
        if not target_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["target_chembl_ids"] = target_ids
        if len(target_ids) > 1:
            result["pooled_across_multiple_targets"] = True

        all_pchembl: list[float] = []
        total_count = 0
        for tid in target_ids:
            activities = _fetch_activities(tid)
            total_count += len(activities)
            for a in activities:
                try:
                    val = float(a["pchembl_value"])
                    all_pchembl.append(val)
                except (TypeError, ValueError):
                    pass

        result["count"] = total_count
        if all_pchembl:
            result["median_pchembl"] = statistics.median(all_pchembl)

    except Exception as e:
        print(f"[chembl] WARNING: bioactivity query failed for '{uniprot_id}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
