"""
PubChem PUG REST API — chemical structure lookup.
Always resolves via InChIKey first, never matches purely on name string downstream.
"""

import json
import re
import requests
from typing import Any, Optional
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PUG_VIEW_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound"

# WHO ATC codes look like e.g. "L01XE03" (1 letter, 2 digits, 2 letters, 2 digits).
_ATC_RE = re.compile(r"\b[A-Z]\d{2}[A-Z]{2}\d{2}\b")


def _get_json(url: str) -> Optional[dict]:
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[pubchem] WARNING: request failed for {url}: {e}")
        return None


def _resolve_inchikey(drug_name: str) -> Optional[str]:
    url = f"{BASE_URL}/compound/name/{requests.utils.quote(drug_name)}/property/InChIKey/JSON"
    data = _get_json(url)
    if data is None:
        return None
    props = data.get("PropertyTable", {}).get("Properties", [])
    if not props:
        return None
    return props[0].get("InChIKey")


def get_compound_data(drug_name: str) -> dict[str, Any]:
    """
    Resolves drug_name → InChIKey, then fetches CanonicalSMILES, MolecularWeight, XLogP.
    Returns:
      {
        inchikey: str | None,
        canonical_smiles: str | None,
        molecular_weight: float | None,
        xlogp: float | None,
        resolved: bool,
        error: str | None,
      }

    IMPORTANT: inchikey is the canonical identifier for all downstream matching.
    Never match on drug_name string for anything downstream of this function.
    """
    cache_key = make_key("get_compound_data", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "inchikey": None,
        "canonical_smiles": None,
        "molecular_weight": None,
        "xlogp": None,
        "resolved": False,
        "error": None,
    }

    try:
        inchikey = _resolve_inchikey(drug_name)
        if not inchikey:
            result["error"] = f"Could not resolve InChIKey for '{drug_name}'"
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["inchikey"] = inchikey

        props_url = (
            f"{BASE_URL}/compound/inchikey/{inchikey}"
            f"/property/ConnectivitySMILES,MolecularWeight,XLogP/JSON"
        )
        props_data = _get_json(props_url)
        if props_data:
            props = props_data.get("PropertyTable", {}).get("Properties", [])
            if props:
                p = props[0]
                # PubChem renamed CanonicalSMILES -> ConnectivitySMILES (2025).
                result["canonical_smiles"] = p.get("ConnectivitySMILES") or p.get("CanonicalSMILES") or p.get("SMILES")
                mw = p.get("MolecularWeight")
                result["molecular_weight"] = float(mw) if mw is not None else None
                xlogp = p.get("XLogP")
                result["xlogp"] = float(xlogp) if xlogp is not None else None
                result["resolved"] = True

    except Exception as e:
        result["error"] = str(e)
        print(f"[pubchem] WARNING: compound data fetch failed for '{drug_name}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result


def _inchikey_to_cid(inchikey: str) -> Optional[int]:
    url = f"{BASE_URL}/compound/inchikey/{inchikey}/cids/JSON"
    data = _get_json(url)
    if not data:
        return None
    cids = data.get("IdentifierList", {}).get("CID", [])
    return cids[0] if cids else None


def get_drug_classification(inchikey: str) -> dict[str, Any]:
    """
    Determine whether a compound is an approved/known drug using PubChem's
    classification fields. Resolves the InChIKey to a CID, then queries PUG-View
    for the "ATC Code" heading — a WHO ATC code is assigned only to recognised
    drugs, so its presence is a reliable known-drug signal.

    Returns:
      {
        inchikey, cid: int | None,
        is_known_drug: bool,
        atc_codes: [str],
        classification_source: str | None,
        error: str | None,
      }

    NOTE: absence of an ATC code is treated as "not confirmed by PubChem", not as
    proof the compound is not a drug. Callers may corroborate with ChEMBL max_phase.
    """
    cache_key = make_key("get_drug_classification", inchikey)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "inchikey": inchikey,
        "cid": None,
        "is_known_drug": False,
        "atc_codes": [],
        "classification_source": None,
        "error": None,
    }

    try:
        cid = _inchikey_to_cid(inchikey)
        if cid is None:
            result["error"] = "no CID for InChIKey"
            cache_set(cache_key, result, ttl_days=7)
            return result
        result["cid"] = cid

        url = f"{PUG_VIEW_URL}/{cid}/JSON?heading=ATC+Code"
        data = _get_json(url)
        if data:
            atc_codes = sorted(set(_ATC_RE.findall(json.dumps(data))))
            if atc_codes:
                result["is_known_drug"] = True
                result["atc_codes"] = atc_codes
                result["classification_source"] = "ATC Code (PubChem PUG-View)"
    except Exception as e:
        result["error"] = str(e)
        print(f"[pubchem] WARNING: drug classification failed for '{inchikey}': {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result
