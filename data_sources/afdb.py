"""
AlphaFold Database (AFDB) structure confidence data.
Fetches per-residue pLDDT scores and computes the mean.
"""

import statistics
import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

PRIMARY_URL = "https://alphafold.ebi.ac.uk/api/prediction/{uniprot_id}"
FALLBACK_URL = "https://alphafold.com/api/prediction/{uniprot_id}"


def _fetch_prediction(uniprot_id: str) -> list[dict]:
    for url_tpl in (PRIMARY_URL, FALLBACK_URL):
        try:
            url = url_tpl.format(uniprot_id=uniprot_id)
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            continue
    return []


def get_structure_confidence(uniprot_id: str) -> dict[str, Any]:
    """
    Returns {has_structure: bool, mean_pLDDT: float | None, model_url: str | None}.
    Fetches per-residue pLDDT from the AFDB API and computes the mean.
    """
    cache_key = make_key("get_structure_confidence", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "has_structure": False,
        "mean_pLDDT": None,
        "model_url": None,
    }

    try:
        predictions = _fetch_prediction(uniprot_id)
        if not predictions:
            cache_set(cache_key, result, ttl_days=7)
            return result

        prediction = predictions[0]
        result["has_structure"] = True
        result["model_url"] = prediction.get("pdbUrl") or prediction.get("cifUrl")

        residue_url = prediction.get("plddt")
        if residue_url and isinstance(residue_url, str) and residue_url.startswith("http"):
            try:
                r = requests.get(residue_url, timeout=20)
                r.raise_for_status()
                plddt_data = r.json()
                if isinstance(plddt_data, list):
                    scores = [
                        float(entry.get("pLDDT", entry.get("confidence", 0)))
                        for entry in plddt_data
                        if isinstance(entry, dict)
                    ]
                    if scores:
                        result["mean_pLDDT"] = statistics.mean(scores)
            except Exception:
                pass

        if result["mean_pLDDT"] is None:
            mean_val = prediction.get("meanPlddt") or prediction.get("globalMetricValue")
            if mean_val is not None:
                result["mean_pLDDT"] = float(mean_val)

    except Exception as e:
        print(f"[afdb] WARNING: structure query failed for '{uniprot_id}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
