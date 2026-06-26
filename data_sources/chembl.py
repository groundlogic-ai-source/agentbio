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


def _fetch_activities_full(target_chembl_id: str) -> list[dict[str, Any]]:
    """
    Like _fetch_activities, but retains molecule identity and structure so callers
    can build a candidate-compound list (not just a count). Keeps only activities
    whose assay confidence_score >= 8 and tags each kept record with `_confidence`.
    """
    url = f"{BASE_URL}/activity.json"
    params = {
        "target_chembl_id": target_chembl_id,
        "standard_type__in": "IC50,Ki",
        "pchembl_value__isnull": "false",
        "only": "activity_id,assay_chembl_id,molecule_chembl_id,canonical_smiles,pchembl_value,standard_type",
        "limit": 1000,
        "offset": 0,
    }
    data = _get_json(url, params)
    activities = data.get("activities", [])
    if not activities:
        return []

    assay_ids = sorted({a["assay_chembl_id"] for a in activities if a.get("assay_chembl_id")})
    confidence = _fetch_assay_confidence(assay_ids)

    kept = []
    for a in activities:
        c = confidence.get(a.get("assay_chembl_id"), 0)
        if c >= 8:
            a["_confidence"] = c
            kept.append(a)
    return kept


def _fetch_molecule_meta(molecule_ids: list[str]) -> dict[str, dict[str, Any]]:
    """
    Batch-fetch molecule metadata. Returns {molecule_chembl_id: {max_phase,
    pref_name, canonical_smiles}}. `max_phase == 4` denotes an approved drug.
    """
    meta: dict[str, dict[str, Any]] = {}
    if not molecule_ids:
        return meta
    url = f"{BASE_URL}/molecule.json"
    batch_size = 40
    for i in range(0, len(molecule_ids), batch_size):
        batch = molecule_ids[i : i + batch_size]
        params = {"molecule_chembl_id__in": ",".join(batch), "limit": 1000}
        try:
            data = _get_json(url, params)
        except Exception as e:
            print(f"[chembl] WARNING: molecule meta fetch failed: {e}")
            continue
        for m in data.get("molecules", []):
            mid = m.get("molecule_chembl_id")
            if not mid:
                continue
            struct = m.get("molecule_structures") or {}
            meta[mid] = {
                "max_phase": m.get("max_phase"),
                "pref_name": m.get("pref_name"),
                "canonical_smiles": struct.get("canonical_smiles"),
            }
    return meta


def get_target_candidate_compounds(uniprot_id: str, max_compounds: int = 25) -> dict[str, Any]:
    """
    Return the actual candidate compounds with bioactivity against a target
    (Homo sapiens, IC50/Ki, assay confidence_score >= 8), aggregated per molecule.

    Returns:
      {
        compounds: [ {
          molecule_chembl_id, pref_name, max_phase, canonical_smiles,
          pchembl_value (median over that molecule's qualifying activities),
          confidence_score (max assay confidence among kept activities),
          n_activities,
          source_activity_ids: [int],   # ChEMBL activity ids (provenance)
          source_assay_ids: [str],
          source_chembl_ids: [str],     # molecule + assay ids for provenance
        } ],
        target_chembl_ids: [str],
        pooled_across_multiple_targets: bool,
      }

    Compounds are ranked by median pChEMBL (desc) and capped at `max_compounds`.
    Mirrors get_target_bioactivity_count's strict filtering — this is the
    compound-level counterpart of that count.
    """
    cache_key = make_key("get_target_candidate_compounds", uniprot_id, max_compounds)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "compounds": [],
        "target_chembl_ids": [],
        "pooled_across_multiple_targets": False,
    }

    try:
        target_ids = _resolve_target_chembl_id(uniprot_id)
        if not target_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["target_chembl_ids"] = target_ids
        result["pooled_across_multiple_targets"] = len(target_ids) > 1

        by_mol: dict[str, dict[str, Any]] = {}
        for tid in target_ids:
            for a in _fetch_activities_full(tid):
                mid = a.get("molecule_chembl_id")
                if not mid:
                    continue
                d = by_mol.setdefault(mid, {
                    "molecule_chembl_id": mid,
                    "pchembls": [],
                    "confidences": [],
                    "activity_ids": [],
                    "assay_ids": set(),
                    "canonical_smiles": a.get("canonical_smiles"),
                })
                try:
                    d["pchembls"].append(float(a["pchembl_value"]))
                except (TypeError, ValueError, KeyError):
                    pass
                d["confidences"].append(a.get("_confidence", 0))
                if a.get("activity_id") is not None:
                    d["activity_ids"].append(a["activity_id"])
                if a.get("assay_chembl_id"):
                    d["assay_ids"].add(a["assay_chembl_id"])

        meta = _fetch_molecule_meta(list(by_mol.keys()))

        compounds = []
        for mid, d in by_mol.items():
            m = meta.get(mid, {})
            smiles = m.get("canonical_smiles") or d["canonical_smiles"]
            assay_ids = sorted(d["assay_ids"])
            compounds.append({
                "molecule_chembl_id": mid,
                "pref_name": m.get("pref_name"),
                "max_phase": m.get("max_phase"),
                "canonical_smiles": smiles,
                "pchembl_value": statistics.median(d["pchembls"]) if d["pchembls"] else None,
                "confidence_score": max(d["confidences"]) if d["confidences"] else None,
                "n_activities": len(d["activity_ids"]),
                "source_activity_ids": d["activity_ids"],
                "source_assay_ids": assay_ids,
                "source_chembl_ids": [mid] + assay_ids,
            })

        compounds.sort(key=lambda c: (c["pchembl_value"] or 0.0), reverse=True)
        result["compounds"] = compounds[:max_compounds]

    except Exception as e:
        print(f"[chembl] WARNING: candidate compound query failed for '{uniprot_id}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
