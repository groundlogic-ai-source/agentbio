"""
openFDA drug adverse event data (FAERS) — https://api.fda.gov/drug/event.json.

Returns the most-frequently reported adverse event terms for a drug. This is a
real-world safety signal from spontaneous reports; it is NOT a causal or
incidence measure. No API key required (anonymous rate limits apply).
"""

import os
import re
from typing import Any, Optional

import requests
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://api.fda.gov/drug/event.json"
LABEL_URL = "https://api.fda.gov/drug/label.json"


def _with_key(params: dict[str, Any]) -> dict[str, Any]:
    """Attach the openFDA API key when one is configured.

    Anonymous callers share a per-IP daily quota that a single validation
    sweep exhausts; openFDA then answers 429 for the rest of the day and
    escalates to an outright 403 block under sustained retries. A (free)
    API key raises the ceiling by orders of magnitude. The key is optional:
    with no OPENFDA_API_KEY set this is a no-op and behaviour is unchanged.
    """
    key = os.environ.get("OPENFDA_API_KEY")
    return {**params, "api_key": key} if key else params
_LABEL_EVIDENCE_CACHE_VERSION = "v1"
_AUDIT_CITATION_CUTOFF = "20260810"

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


# --------------------------------------------------------------------------- #
# Failure-honest structured regulatory-label evidence for the audit surface
# --------------------------------------------------------------------------- #

def _values(value: Any) -> list[str]:
    """Coerce an openFDA scalar/list field to normalized non-empty strings."""
    raw = value if isinstance(value, list) else [value]
    return sorted({
        re.sub(r"\s+", " ", str(item)).strip()
        for item in raw
        if item is not None and str(item).strip()
    }, key=str.casefold)


def _nested_values(row: dict[str, Any], field: str) -> list[str]:
    block = row.get("openfda")
    return _values(block.get(field)) if isinstance(block, dict) else []


def _norm_product_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _label_matches(row: dict[str, Any], drug_name: str) -> bool:
    """Deterministic product-identity gate after the broad API query."""
    wanted = _norm_product_name(drug_name)
    if not wanted:
        return False
    names: list[str] = []
    for field in ("generic_name", "brand_name", "substance_name"):
        names.extend(_nested_values(row, field))
    return any(
        wanted == _norm_product_name(name)
        or wanted in _norm_product_name(name)
        or _norm_product_name(name) in wanted
        for name in names
        if _norm_product_name(name)
    )


def _ingredient_names(row: dict[str, Any]) -> list[dict[str, str]]:
    """Return structured active ingredients without guessing from drug names."""
    substances = _nested_values(row, "substance_name")
    if substances:
        return [{"name": name, "strength": ""} for name in substances]

    out: list[dict[str, str]] = []
    for text in _values(row.get("active_ingredient")):
        for part in re.split(
                r"\s*(?:;|\n|\+|\band\b)\s*", text, flags=re.IGNORECASE):
            part = part.strip(" .:-")
            if not part:
                continue
            match = re.match(
                r"^(.*?)(?:\s+(\d+(?:\.\d+)?\s*"
                r"(?:mg|mcg|g|unit|units|%)(?:/\S+)?))?$",
                part, flags=re.IGNORECASE)
            name = (match.group(1) if match else part).strip(" .:-")
            strength = (match.group(2) if match and match.group(2) else "")
            if name:
                out.append({"name": name, "strength": strength})
    unique = {
        (item["name"].casefold(), item["strength"].casefold()): item
        for item in out
    }
    return [unique[key] for key in sorted(unique)]


def _product_modality(
    product_types: list[str],
    application_numbers: list[str],
) -> tuple[str, str]:
    joined = " ".join(product_types).lower()
    if "vaccine" in joined:
        return "vaccine", "openfda.product_type"
    if "biologic" in joined:
        return "biologic", "openfda.product_type"
    if any(value.upper().startswith("BLA") for value in application_numbers):
        return "biologic", "openfda.application_number BLA prefix"
    if "device" in joined:
        return "device", "openfda.product_type"
    if any(value.upper().startswith(("NDA", "ANDA"))
           for value in application_numbers):
        return "drug", "openfda.application_number NDA/ANDA prefix"
    # A generic "prescription drug" label category does not prove molecular
    # modality: FDA labeling APIs include biologics under drug-label surfaces.
    return "unknown", "not resolved by product type/application number"


def _label_quote_rows(
    row: dict[str, Any],
    source_id: str,
) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for field in (
        "active_ingredient", "indications_and_usage", "mechanism_of_action",
        "clinical_pharmacology", "description", "dosage_and_administration",
        "purpose",
    ):
        for quote in _values(row.get(field)):
            evidence.append({
                "field": field,
                "quote": quote,
                "source_id": source_id,
            })
    return evidence


def _empty_label_evidence(
    drug_name: str,
    status: str,
    error: Optional[str] = None,
    *,
    retrieved_count: int = 0,
    filtered_count: int = 0,
) -> dict[str, Any]:
    return {
        "provider": "openfda",
        "status": status,
        "query": {"drug_name": drug_name},
        "products": [],
        "retrieved_count": retrieved_count,
        "filtered_count": filtered_count,
        "citation_cutoff": "2026-08-10",
        "error": error,
    }


def get_label_evidence(
    drug_name: str,
    *,
    deadline_monotonic: Optional[float] = None,
) -> dict[str, Any]:
    """Return a dated regulatory product envelope for audit disclosures.

    Only healthy ``ok``/``empty`` and deterministic ``filtered_empty`` results
    are cached.  ``degraded``, ``parse_failed`` and ``unavailable`` are never
    converted to biological absence and are never cached.
    """
    drug_name = (drug_name or "").strip()
    cache_key = make_key(
        f"openfda_label_evidence_{_LABEL_EVIDENCE_CACHE_VERSION}",
        drug_name.lower(),
    )
    cached = get(cache_key)
    if cached is not None:
        return cached
    if not drug_name:
        result = _empty_label_evidence(drug_name, "empty")
        cache_set(cache_key, result, ttl_days=30)
        return result

    params = {
        "search": (
            f'openfda.generic_name:"{drug_name}" OR '
            f'openfda.brand_name:"{drug_name}" OR '
            f'openfda.substance_name:"{drug_name}"'
        ),
        "limit": 100,
    }
    try:
        timeout = 10.0
        if deadline_monotonic is not None:
            import time
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                return _empty_label_evidence(
                    drug_name, "unavailable", "audit source deadline exceeded")
            timeout = max(0.25, min(timeout, remaining))
        response = requests.get(LABEL_URL, params=params, timeout=timeout)
    except requests.RequestException as exc:
        return _empty_label_evidence(
            drug_name, "unavailable", f"{type(exc).__name__}: {exc}")

    if response.status_code == 404:
        result = _empty_label_evidence(drug_name, "empty")
        cache_set(cache_key, result, ttl_days=30)
        return result
    if response.status_code == 429 or response.status_code >= 500:
        return _empty_label_evidence(
            drug_name, "unavailable", f"HTTP {response.status_code}")
    if response.status_code != 200:
        return _empty_label_evidence(
            drug_name, "unavailable", f"HTTP {response.status_code}")

    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        return _empty_label_evidence(
            drug_name, "parse_failed", f"invalid JSON: {exc}")
    if not isinstance(payload, dict) or not isinstance(
            payload.get("results"), list):
        return _empty_label_evidence(
            drug_name, "parse_failed", "results is not a list")

    if not payload["results"]:
        result = _empty_label_evidence(drug_name, "empty")
        cache_set(cache_key, result, ttl_days=30)
        return result
    malformed_count = sum(
        1 for row in payload["results"] if not isinstance(row, dict))
    retrieved = [
        row for row in payload["results"] if isinstance(row, dict)
    ]
    if not retrieved:
        return _empty_label_evidence(
            drug_name, "parse_failed", "all result rows are malformed",
            retrieved_count=len(payload["results"]))
    matched = [row for row in retrieved if _label_matches(row, drug_name)]
    if not matched:
        result = _empty_label_evidence(
            drug_name,
            "degraded" if malformed_count else "filtered_empty",
            "some result rows were malformed" if malformed_count else None,
            retrieved_count=len(payload["results"]),
            filtered_count=len(retrieved),
        )
        if result["status"] == "filtered_empty":
            cache_set(cache_key, result, ttl_days=30)
        return result

    # Retain the latest version for each SPL set id. Rows without an SPL set id
    # remain separate under their document id rather than being conflated.
    latest: dict[str, dict[str, Any]] = {}
    for row in matched:
        set_ids = (
            _nested_values(row, "spl_set_id") or _values(row.get("set_id")))
        document_ids = (
            _nested_values(row, "spl_id") or _values(row.get("id")))
        anchor = (
            set_ids[0] if set_ids else
            (document_ids[0] if document_ids else
             f"unidentified:{len(latest)}")
        )
        versions = (
            _nested_values(row, "version") or _values(row.get("version")))
        try:
            version_number = int(versions[0]) if versions else 0
        except ValueError:
            version_number = 0
        previous = latest.get(anchor)
        if previous is None or version_number > previous["_version_number"]:
            latest[anchor] = {
                "row": row,
                "_version_number": version_number,
            }

    products: list[dict[str, Any]] = []
    degraded = malformed_count > 0
    for anchor in sorted(latest, key=str.casefold):
        row = latest[anchor]["row"]
        set_ids = (
            _nested_values(row, "spl_set_id") or _values(row.get("set_id")))
        spl_ids = _nested_values(row, "spl_id")
        versions = (
            _nested_values(row, "version") or _values(row.get("version")))
        effective_dates = _values(row.get("effective_time"))
        document_ids = _values(row.get("id"))
        ingredients = _ingredient_names(row)
        product_types = _nested_values(row, "product_type")
        application_numbers = _nested_values(row, "application_number")
        modality, modality_basis = _product_modality(
            product_types, application_numbers)
        effective = effective_dates[0] if effective_dates else None
        if not set_ids or not effective:
            degraded = True
        products.append({
            "identity": {
                "generic_names": _nested_values(row, "generic_name"),
                "brand_names": _nested_values(row, "brand_name"),
                "substance_names": _nested_values(row, "substance_name"),
                "ndc_product_ids": sorted(set(
                    _nested_values(row, "product_ndc")
                    + _nested_values(row, "package_ndc")
                ), key=str.casefold),
            },
            "regulatory": {
                "active_ingredients": ingredients,
                "combination": len({
                    item["name"].casefold() for item in ingredients
                }) >= 2,
                "routes": _nested_values(row, "route"),
                "dosage_forms": _nested_values(row, "dosage_form"),
                "product_modality": modality,
                "modality_basis": modality_basis,
                "product_types": product_types,
                "application_numbers": application_numbers,
            },
            "spl": {
                "set_id": set_ids[0] if set_ids else None,
                "version": versions[0] if versions else None,
                "effective_date": effective,
                "spl_id": spl_ids[0] if spl_ids else None,
                "document_id": document_ids[0] if document_ids else None,
            },
            "citation_eligible": bool(
                effective and re.fullmatch(r"\d{8}", effective)
                and effective < _AUDIT_CITATION_CUTOFF
            ),
            "source_url": (
                "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?"
                f"setid={set_ids[0]}" if set_ids else None
            ),
            "evidence": _label_quote_rows(row, anchor),
        })

    result = {
        "provider": "openfda",
        "status": "degraded" if degraded else "ok",
        "query": {"drug_name": drug_name},
        "products": products,
        "retrieved_count": len(payload["results"]),
        "filtered_count": len(retrieved) - len(matched),
        "citation_cutoff": "2026-08-10",
        "error": (
            "one or more label rows lacked parseable SPL provenance"
            if degraded else None
        ),
    }
    if result["status"] == "ok":
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
