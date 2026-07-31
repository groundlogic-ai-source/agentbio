"""
Orphanet rare disease list via the Orphadata API.
Also includes the static WHO Neglected Tropical Disease list.
"""

import requests
import xml.etree.ElementTree as ET
from typing import Any, Optional
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://api.orphadata.com"

# Bulk cross-referencing product (single ~3.4MB file). Unlike the JSON API,
# this XML product exposes the DisorderGroup classification (Disorder /
# Subtype of disorder / Group of disorders) for every ORPHAcode in one call,
# so the candidate-universe umbrella filter needs a single fetch, not ~11.6k
# per-code lookups.
PRODUCT1_XML_URL = "https://www.orphadata.com/data/xml/en_product1.xml"

# DisorderGroup value that marks an umbrella term (e.g. "RASopathy") rather
# than a specific single disease. These are excluded from the scoring universe.
GROUP_OF_DISORDERS = "Group of disorders"

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
        url = f"{BASE_URL}/rd-cross-referencing/orphacodes"
        headers = {"Accept": "application/json"}
        resp = requests.get(url, params={"lang": "en"}, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("data", {}).get("results", [])
        if isinstance(results, dict):
            results = [results]

        for disorder in results:
            orpha_code = disorder.get("ORPHAcode") or disorder.get("OrphaCode") or ""
            name = disorder.get("Preferred term") or disorder.get("Name") or ""
            if not name:
                continue
            diseases.append({
                "orpha_code": str(orpha_code),
                "name": name,
                # Cross-references require a per-code lookup (see get_disease_xrefs);
                # they are not exposed on the bulk list and are not used for EFO
                # resolution (which matches on disease name via Open Targets).
                "icd10": None,
                "omim": None,
                "mesh": None,
            })

    except Exception as e:
        print(f"[orphadata] WARNING: API call failed ({e}). Returning empty rare disease list.")
        # Do NOT cache the empty universe after a failure — a cached [] would
        # silently zero the entire sweep for 7 days.
        return diseases

    cache_set(cache_key, diseases, ttl_days=7)
    return diseases


def get_disorder_metadata() -> list[dict[str, Any]]:
    """
    Fetch the Orphanet cross-referencing XML product (en_product1.xml) and return
    one record per ORPHAcode: {orpha_code, name, disorder_group}.

    `disorder_group` is Orphanet's DisorderGroup classification — one of
    "Disorder", "Subtype of disorder", or "Group of disorders". The last marks
    an umbrella term (e.g. "RASopathy") that aggregates several distinct
    diseases; the candidate-universe filter uses it to drop umbrella entries.

    A single ~3.4MB download covers the whole nomenclature, so this is far
    cheaper than per-code lookups. Cached with a long TTL (nomenclature is
    updated a few times a year).
    """
    cache_key = make_key("get_disorder_metadata")
    cached = get(cache_key)
    if cached is not None:
        return cached

    records: list[dict[str, Any]] = []
    try:
        resp = requests.get(PRODUCT1_XML_URL, timeout=120)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for d in root.findall(".//Disorder"):
            code = d.findtext("OrphaCode")
            name = d.findtext("Name")
            dg = d.find("DisorderGroup")
            group = dg.findtext("Name") if dg is not None else None
            if not code or not name:
                continue
            records.append({
                "orpha_code": str(code),
                "name": name,
                "disorder_group": group,
            })
    except Exception as e:
        print(f"[orphadata] WARNING: DisorderGroup product fetch failed ({e}). "
              f"Umbrella filtering will be skipped this run.")

    # Only cache a genuinely populated result; an empty parse (network failure)
    # should be retried, not frozen for 30 days.
    if records:
        cache_set(cache_key, records, ttl_days=30)
    return records


def get_disease_xrefs(orpha_code: str) -> dict[str, Any]:
    """
    Fetch ICD-10, OMIM, MeSH, and UMLS cross-references for a single ORPHAcode.
    Cached per code. Returns {icd10, omim, mesh, umls} (each may be None).

    This is a per-code lookup, intended to enrich only the diseases that reach
    the ranked output table — not all ~11k diseases at universe-build time.
    """
    cache_key = make_key("get_disease_xrefs", orpha_code)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {"icd10": None, "omim": None, "mesh": None, "umls": None}
    try:
        url = f"{BASE_URL}/rd-cross-referencing/orphacodes/{orpha_code}"
        resp = requests.get(url, params={"lang": "en"}, headers={"Accept": "application/json"}, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        record = payload.get("data", {}).get("results", payload)
        if isinstance(record, list):
            record = record[0] if record else {}

        refs = record.get("ExternalReference") or []
        for ref in refs:
            source = (ref.get("Source") or "").upper()
            value = ref.get("Reference")
            if not value:
                continue
            if source == "ICD-10" and result["icd10"] is None:
                result["icd10"] = value
            elif source == "OMIM" and result["omim"] is None:
                result["omim"] = value
            elif source == "MESH" and result["mesh"] is None:
                result["mesh"] = value
            elif source == "UMLS" and result["umls"] is None:
                result["umls"] = value
    except Exception as e:
        print(f"[orphadata] WARNING: xref lookup failed for ORPHAcode {orpha_code}: {e}")
        # Do NOT cache the all-None result after a failure (7-day poisoning).
        return result

    cache_set(cache_key, result, ttl_days=7)
    return result


def get_disease_prevalence(orpha_code: str) -> Optional[float]:
    """
    Attempt to fetch point prevalence (per million) from Orphadata's epidemiology API.

    This is optional / best-effort: if the endpoint is unreachable, requires auth,
    or returns no numeric estimate, returns None and logs clearly. The cache TTL
    is short on failure so the pipeline retries on the next sweep without waiting
    weeks.

    Returns: float (per million) if available, None otherwise.
    """
    cache_key = make_key("get_disease_prevalence_v1", orpha_code)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: Optional[float] = None
    try:
        url = f"{BASE_URL}/rd-epidemiology/orphacodes/{orpha_code}"
        resp = requests.get(
            url,
            params={"lang": "en"},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code in (401, 403):
            print(
                f"[orphadata] NOTE: epidemiology API requires authentication "
                f"(ORPHAcode {orpha_code}) — prevalence unavailable on this tier."
            )
            cache_set(cache_key, None, ttl_days=30)
            return None
        if resp.status_code == 404:
            cache_set(cache_key, None, ttl_days=7)
            return None
        resp.raise_for_status()
        data = resp.json()

        results = data.get("data", {}).get("results", [])
        if isinstance(results, dict):
            results = [results]

        best: Optional[float] = None
        for rec in results:
            prev_list = rec.get("Prevalence") or rec.get("prevalence") or []
            if isinstance(prev_list, dict):
                prev_list = [prev_list]
            for pv in prev_list:
                raw = pv.get("ValMoy") or pv.get("prevalence_per_million")
                if raw is None:
                    continue
                try:
                    f = float(raw)
                    if f > 0 and (best is None or f > best):
                        best = f
                except (TypeError, ValueError):
                    pass
        result = best

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        print(f"[orphadata] NOTE: epidemiology API HTTP {code} for ORPHAcode {orpha_code}: {e}")
    except Exception as e:
        print(f"[orphadata] NOTE: epidemiology API unavailable for ORPHAcode {orpha_code}: {e}")

    cache_set(cache_key, result, ttl_days=1 if result is None else 30)
    return result


def get_who_ntd_list() -> list[dict[str, Any]]:
    """Return the static WHO Neglected Tropical Disease list."""
    seen = set()
    unique = []
    for d in WHO_NTDS:
        if d["name"] not in seen:
            seen.add(d["name"])
            unique.append(d)
    return unique
