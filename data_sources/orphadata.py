"""
Orphanet rare disease list via the Orphadata API.
Also includes the static WHO Neglected Tropical Disease list.
"""

import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://api.orphadata.com"

WHO_NTDS = [
    {"name": "Buruli ulcer", "icd10": "A31.1", "mesh": "D009165"},
    {"name": "Chagas disease", "icd10": "B57", "mesh": "D014355"},
    {"name": "Dengue", "icd10": "A97", "mesh": "D003715"},
    {"name": "Dracunculiasis (Guinea-worm disease)", "icd10": "B72", "mesh": "D004320"},
    {"name": "Echinococcosis", "icd10": "B67", "mesh": "D004443"},
    {"name": "Foodborne trematodiases", "icd10": "B66", "mesh": "D014201"},
    {"name": "Human African trypanosomiasis (sleeping sickness)", "icd10": "B56", "mesh": "D014353"},
    {"name": "Leishmaniasis", "icd10": "B55", "mesh": "D007896"},
    {"name": "Leprosy", "icd10": "A30", "mesh": "D007918"},
    {"name": "Lymphatic filariasis", "icd10": "B74.0", "mesh": "D008625"},
    {"name": "Mycetoma, chromoblastomycosis and other deep mycoses", "icd10": "B47", "mesh": "D009182"},
    {"name": "Onchocerciasis (river blindness)", "icd10": "B73", "mesh": "D009883"},
    {"name": "Rabies", "icd10": "A82", "mesh": "D011818"},
    {"name": "Scabies and other ectoparasitoses", "icd10": "B86", "mesh": "D012532"},
    {"name": "Schistosomiasis", "icd10": "B65", "mesh": "D012552"},
    {"name": "Soil-transmitted helminthiases", "icd10": "B76", "mesh": "D006888"},
    {"name": "Snakebite envenomation", "icd10": "T63.0", "mesh": "D013119"},
    {"name": "Taeniasis and cysticercosis", "icd10": "B68", "mesh": "D013166"},
    {"name": "Trachoma", "icd10": "A71", "mesh": "D014141"},
    {"name": "Yaws", "icd10": "A66", "mesh": "D015001"},
]


def get_rare_disease_list() -> list[dict[str, Any]]:
    """
    Fetch Orphanet product1 (rare disease list with cross-references).
    Returns a list of dicts: {orpha_code, name, icd10, omim, mesh}.
    Falls back gracefully if API is unreachable.
    """
    cache_key = make_key("get_rare_disease_list")
    cached = get(cache_key)
    if cached is not None:
        return cached

    diseases = []
    try:
        url = f"{BASE_URL}/en/product1"
        headers = {"Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        disorder_list = []
        if isinstance(data, dict):
            if "data" in data:
                payload = data["data"]
                if isinstance(payload, dict) and "results" in payload:
                    disorder_list = payload["results"]
                elif isinstance(payload, list):
                    disorder_list = payload
            elif "results" in data:
                disorder_list = data["results"]

        for disorder in disorder_list:
            orpha_code = disorder.get("OrphaCode") or disorder.get("orpha_code") or ""
            name = disorder.get("Name", [{}])
            if isinstance(name, list):
                name = name[0].get("label", "") if name else ""
            elif isinstance(name, dict):
                name = name.get("label", "")

            icd10_list, omim_list, mesh_list = [], [], []

            refs = disorder.get("ExternalReferenceList", []) or disorder.get("external_references", [])
            for ref in refs:
                source = ref.get("Source", ref.get("source", ""))
                ref_val = ref.get("Reference", ref.get("reference", ""))
                if source == "ICD-10":
                    icd10_list.append(ref_val)
                elif source == "OMIM":
                    omim_list.append(ref_val)
                elif source == "MeSH":
                    mesh_list.append(ref_val)

            diseases.append({
                "orpha_code": str(orpha_code),
                "name": name,
                "icd10": icd10_list[0] if icd10_list else None,
                "omim": omim_list[0] if omim_list else None,
                "mesh": mesh_list[0] if mesh_list else None,
            })

    except Exception as e:
        print(f"[orphadata] WARNING: API call failed ({e}). Returning empty rare disease list.")

    cache_set(cache_key, diseases, ttl_days=7)
    return diseases


def get_who_ntd_list() -> list[dict[str, Any]]:
    """Return the static WHO Neglected Tropical Disease list."""
    seen = set()
    unique = []
    for d in WHO_NTDS:
        if d["name"] not in seen:
            seen.add(d["name"])
            unique.append(d)
    return unique
