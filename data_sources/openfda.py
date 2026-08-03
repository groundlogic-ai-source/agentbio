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

# MedDRA preferred terms that are reporting artifacts, not adverse events:
# product-use metadata, effectiveness complaints, and disease-progression terms
# that echo the indication rather than describe a drug-induced harm. FAERS
# ranks these among the most-reported PTs for many drugs (e.g. "OFF LABEL USE"
# and "DRUG INEFFECTIVE" for bortezomib), and presenting them as adverse-event
# signals is misleading.
_NON_EVENT_PT = {
    "DRUG INEFFECTIVE",
    "OFF LABEL USE",
    "PRODUCT USE ISSUE",
    "PRODUCT USE COMPLAINT",
    "INTENTIONAL PRODUCT MISUSE",
    "PRODUCT DOSE OMISSION ISSUE",
    "NO ADVERSE EVENT",
    "DISEASE PROGRESSION",
    "MALIGNANT NEOPLASM PROGRESSION",
    "NEOPLASM PROGRESSION",
}


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

    # Only cache successful lookups (including explicit 404s handled above):
    # caching a transient failure would poison 30 days of label lookups.
    if result["error"] is None:
        cache_set(cache_key, result, ttl_days=30)
    return result


def get_label_mechanism(drug_name: str) -> dict[str, Any]:
    """Return FDA-label pharmacology text as a quoted mechanism assertion.

    The label is regulatory evidence for an established drug mechanism, not
    evidence that the drug treats the requested disease.  Consumers must keep
    that distinction explicit in their ledger role and score disclosure.
    """
    cache_key = make_key("get_label_mechanism_v1", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "drug": drug_name,
        "mechanism_text": "",
        "label_id": None,
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
            row = rows[0]
            # SPL labels vary: most use CLINICAL PHARMACOLOGY, while some
            # carry the useful wording in mechanism_of_action instead.
            text = (
                row.get("mechanism_of_action")
                or row.get("clinical_pharmacology")
                or row.get("description")
                or []
            )
            if isinstance(text, list):
                text = " ".join(str(item) for item in text)
            result.update({
                "mechanism_text": str(text or "").strip(),
                "label_id": (
                    (row.get("set_id") or row.get("id") or
                     (row.get("openfda") or {}).get("spl_set_id") or [None])[0]
                    if isinstance((row.get("openfda") or {}).get("spl_set_id"), list)
                    else row.get("set_id") or row.get("id") or
                    (row.get("openfda") or {}).get("spl_set_id")
                ),
                "source": "openfda_label",
            })
    except Exception as e:
        result["error"] = str(e)
        print(f"[openfda] WARNING: label mechanism query failed for '{drug_name}': {e}")

    if result["error"] is None:
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
    # v2: filters non-adverse-event MedDRA PTs; v1 entries mixed reporting
    # artifacts (OFF LABEL USE, DRUG INEFFECTIVE, ...) into the AE list.
    cache_key = make_key("get_adverse_events_v2", drug_name, limit)
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
        # Drop non-adverse-event MedDRA PTs so the dossier's safety signal
        # contains only actual harms, not reporting artifacts.
        rows = [r for r in rows
                if (r.get("term") or "").strip().upper() not in _NON_EVENT_PT]
        events = [{"term": r.get("term"), "count": int(r.get("count", 0))} for r in rows[:limit]]
        result["adverse_events"] = events
        result["total_event_terms"] = len(rows)
    except Exception as e:
        result["error"] = str(e)
        print(f"[openfda] WARNING: adverse event query failed for '{drug_name}': {e}")

    # Only cache successful lookups: a transient failure cached as an empty
    # event list would masquerade as "no safety signal" for 7 days.
    if result["error"] is None:
        cache_set(cache_key, result, ttl_days=7)
    return result
