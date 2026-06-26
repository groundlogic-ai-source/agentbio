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
        organism = (t.get("organism") or "")
        tax_id = t.get("tax_id")
        # Strict species match: only keep Homo sapiens (tax_id 9606).
        # Server-side organism filter is belt; this is the suspenders.
        if "Homo sapiens" in organism or tax_id == 9606:
            ids.append(t["target_chembl_id"])
    return ids


def _fetch_assay_confidence(assay_ids: list[str]) -> dict[str, int]:
    """
    Look up confidence_score for a list of assay_chembl_ids.

    NOTE: confidence_score lives on the ChEMBL *assay* resource, not on the
    activity record. The activity endpoint's assay_confidence_score filter is
    silently ignored by the API, so we must join against /assay here ourselves.
    """
    confidence: dict[str, int] = {}
    if not assay_ids:
        return confidence

    url = f"{BASE_URL}/assay.json"
    batch_size = 50
    for i in range(0, len(assay_ids), batch_size):
        batch = assay_ids[i : i + batch_size]
        params = {
            "assay_chembl_id__in": ",".join(batch),
            "only": "assay_chembl_id,confidence_score",
            "limit": 1000,
        }
        data = _get_json(url, params)
        for a in data.get("assays", []):
            aid = a.get("assay_chembl_id")
            score = a.get("confidence_score")
            if aid is not None and score is not None:
                confidence[aid] = int(score)
    return confidence


def _fetch_activities(target_chembl_id: str) -> list[dict[str, Any]]:
    """
    Fetch IC50/Ki activities (pchembl_value present) for a target, then keep
    only those whose assay confidence_score >= 8. Pulls up to 1000 records.
    """
    url = f"{BASE_URL}/activity.json"
    params = {
        "target_chembl_id": target_chembl_id,
        "standard_type__in": "IC50,Ki",
        "pchembl_value__isnull": "false",
        "only": "assay_chembl_id,pchembl_value,standard_type",
        "limit": 1000,
        "offset": 0,
    }
    data = _get_json(url, params)
    activities = data.get("activities", [])
    if not activities:
        return []

    assay_ids = sorted({a["assay_chembl_id"] for a in activities if a.get("assay_chembl_id")})
    confidence = _fetch_assay_confidence(assay_ids)

    return [
        a for a in activities
        if confidence.get(a.get("assay_chembl_id"), 0) >= 8
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
