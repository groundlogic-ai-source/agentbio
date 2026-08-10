"""Bounded, failure-honest drug→mechanism literature assertions.

This audit-only lane resolves both entities through PubTator3, executes an
entity-relation query, and admits only citable same-sentence action assertions.
It does not generate candidates and does not affect ranking.
"""
from __future__ import annotations

from datetime import date
import hashlib
import html
import re
import threading
import time
from typing import Any, Optional

import requests

from cache.cache import get, make_key, set as cache_set

_BASE = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api"
_EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_TIMEOUT = 10
_TTL_DAYS = 30
_CACHE_VERSION = "v2"
_CITATION_CUTOFF = date(2026, 8, 10)
_MAX_RESULTS = 20
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0

_ACTION_RULES = (
    ("antagonist", re.compile(r"\b(?:antagonis(?:t|m)|blocks?)\b", re.I)),
    ("agonist", re.compile(r"\b(?:agonis(?:t|m))\b", re.I)),
    ("inhibitor", re.compile(
        r"\b(?:inhibit(?:s|ed|ion)?|suppress(?:es|ed|ion)?|decreas(?:e|es|ed))\b",
        re.I)),
    ("activator", re.compile(
        r"\b(?:activat(?:e|es|ed|ion)|increas(?:e|es|ed)|induc(?:e|es|ed))\b",
        re.I)),
    ("modulator", re.compile(r"\b(?:modulat(?:e|es|ed|ion)|allosteric)\b", re.I)),
)
_REJECT = re.compile(
    r"\b(?:did not|does not|no (?:effect|inhibition)|failed to|"
    r"may|might|could|possibly|hypothesi[sz]|speculat|associated with)\b",
    re.I,
)
_REVIEW = re.compile(r"\b(?:review|meta-analysis|systematic review)\b", re.I)
_ANIMAL = re.compile(
    r"\b(?:mouse|mice|murine|rat|rats|zebrafish|drosophila|rabbit|canine|"
    r"porcine|animal model|in vivo)\b", re.I)
_HUMAN = re.compile(
    r"\b(?:human|patient|patients|participant|participants|clinical|"
    r"volunteer|homo sapiens)\b", re.I)
_IN_VITRO = re.compile(
    r"\b(?:in vitro|cell line|cells|culture|organoid|isolated protein|"
    r"biochemical assay|primary cells)\b", re.I)


def _envelope(status: str, drug: dict[str, Any], mechanism: dict[str, Any],
              assertions: list[dict[str, Any]], error: Optional[str] = None,
              *, retrieved_count: int = 0,
              filtered_count: int = 0,
              source_status: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "provider": "pubtator3",
        "status": status,
        "drug": drug,
        "mechanism": mechanism,
        "assertions": assertions,
        "retrieved_count": retrieved_count,
        "filtered_count": filtered_count,
        "release": "PubTator3 live; retrieved at request time",
        "source_status": source_status or {
            "pubtator3": {"status": status, "error": error},
            "europepmc": {"status": "not_queried", "error": None},
        },
        "citation_cutoff": "2026-08-10",
        "error": error,
    }


def _request_timeout(deadline_monotonic: Optional[float]) -> float:
    if deadline_monotonic is None:
        return float(_TIMEOUT)
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("audit source deadline exceeded")
    return max(0.25, min(float(_TIMEOUT), remaining))


def _json_get(
    path: str,
    params: dict[str, Any],
    *,
    deadline_monotonic: Optional[float] = None,
) -> Any:
    # PubTator asks API clients to remain at or below three requests/second.
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        delay = (1.0 / 3.0) - (time.monotonic() - _LAST_REQUEST_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST_AT = time.monotonic()
    # Never hold the process-wide rate-scheduling lock across network I/O.
    response = requests.get(
        f"{_BASE}{path}",
        params=params,
        timeout=_request_timeout(deadline_monotonic),
    )
    if response.status_code == 404:
        return None
    if response.status_code == 429 or response.status_code >= 500:
        raise requests.RequestException(f"HTTP {response.status_code}")
    if response.status_code != 200:
        raise requests.RequestException(f"HTTP {response.status_code}")
    return response.json()


def _metadata_for_pmids(
    pmids: list[str],
    *,
    deadline_monotonic: Optional[float] = None,
) -> tuple[str, dict[str, dict[str, Any]], Optional[str]]:
    """Fetch publication type/date metadata in one bounded Europe PMC query."""
    if not pmids:
        return "empty", {}, None
    query = " OR ".join(f"EXT_ID:{pmid}" for pmid in pmids[:_MAX_RESULTS])
    try:
        response = requests.get(
            _EUROPE_PMC_SEARCH,
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": _MAX_RESULTS,
            },
            timeout=_request_timeout(deadline_monotonic),
        )
    except requests.RequestException as exc:
        return "unavailable", {}, f"{type(exc).__name__}: {exc}"
    if response.status_code == 429 or response.status_code >= 500:
        return "unavailable", {}, f"HTTP {response.status_code}"
    if response.status_code != 200:
        return "unavailable", {}, f"HTTP {response.status_code}"
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        return "parse_failed", {}, f"invalid JSON: {exc}"
    try:
        rows = payload["resultList"]["result"]
    except (KeyError, TypeError):
        return "parse_failed", {}, "resultList.result is missing"
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return "parse_failed", {}, "resultList.result is not a list of objects"
    metadata: dict[str, dict[str, Any]] = {}
    for row in rows:
        pmid = str(row.get("pmid") or row.get("id") or "").strip()
        if not pmid:
            continue
        pub_types = (row.get("pubTypeList") or {}).get("pubType") or []
        if isinstance(pub_types, str):
            pub_types = [pub_types]
        if not isinstance(pub_types, list):
            return "parse_failed", {}, "pubTypeList.pubType is malformed"
        metadata[pmid] = {
            "publication_types": sorted({
                str(value).strip() for value in pub_types
                if str(value).strip()
            }, key=str.casefold),
            "publication_date": (
                row.get("firstPublicationDate")
                or row.get("electronicPublicationDate")
                or row.get("journalInfo", {}).get("printPublicationDate")
            ),
        }
    return "ok", metadata, None


def _resolve(
    name: str,
    concept: str,
    *,
    deadline_monotonic: Optional[float] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    payload = _json_get(
        "/entity/autocomplete/",
        {"query": name, "concept": concept, "limit": 10},
        deadline_monotonic=deadline_monotonic,
    )
    if payload is None or payload == []:
        return "empty", None
    if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
        return "parse_failed", None
    wanted = re.sub(r"[^a-z0-9]+", "", name.lower())
    exact = [
        row for row in payload
        if re.sub(r"[^a-z0-9]+", "", str(row.get("name") or "").lower()) == wanted
    ]
    if not exact:
        return "filtered_empty", None
    row = exact[0]
    entity_id = str(row.get("_id") or "")
    if not entity_id.startswith("@"):
        return "parse_failed", None
    return "ok", {
        "canonical_id": entity_id,
        "database_id": str(row.get("db_id") or ""),
        "name": str(row.get("name") or name),
        "type": str(row.get("biotype") or concept),
    }


def _clean_highlight(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"@</?m>", "", text)
    text = re.sub(r"@[A-Z]+_[^\s@]+", "", text)
    text = re.sub(r"@@@([^@]+)@@@", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _matched_surface(raw: Any, entity_id: str) -> str:
    """Recover the surface text for a relation-matched PubTator entity."""
    text = str(raw or "")
    canonical = entity_id[1:] if entity_id.startswith("@") else entity_id
    match = re.search(
        rf"@<m>{re.escape(canonical)}</m>.*?@@@([^@]+)@@@",
        text,
    )
    return html.unescape(match.group(1)).strip() if match else ""


def _entity_span(
    sentence: str,
    surface: str,
    entity: dict[str, Any],
) -> Optional[dict[str, Any]]:
    start = sentence.casefold().find(surface.casefold()) if surface else -1
    if start < 0:
        return None
    result = dict(entity)
    result.update({
        "text": surface,
        "start": start,
        "end": start + len(surface),
    })
    return result


def _admitted_sentence_and_spans(
    clean_text: str,
    raw_highlight: Any,
    drug: dict[str, Any],
    mechanism: dict[str, Any],
    action: str,
) -> Optional[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Require both resolved entities and action in one exact sentence."""
    drug_surface = _matched_surface(
        raw_highlight, str(drug.get("canonical_id") or ""))
    mechanism_surface = _matched_surface(
        raw_highlight, str(mechanism.get("canonical_id") or ""))
    if not drug_surface or not mechanism_surface or not action:
        return None
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", clean_text)
        if part.strip()
    ]
    for sentence in sentences:
        drug_entity = _entity_span(sentence, drug_surface, drug)
        mechanism_entity = _entity_span(sentence, mechanism_surface, mechanism)
        action_start = sentence.casefold().find(action.casefold())
        if drug_entity is None or mechanism_entity is None or action_start < 0:
            continue
        if (drug_entity["start"], drug_entity["end"]) == (
                mechanism_entity["start"], mechanism_entity["end"]):
            continue
        relation_span = {
            "text": action,
            "start": action_start,
            "end": action_start + len(action),
        }
        return sentence, drug_entity, mechanism_entity, relation_span
    return None


def _publication_date(value: Any) -> tuple[Optional[str], bool]:
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return (raw or None), False
    try:
        parsed = date(*(int(part) for part in match.groups()))
    except ValueError:
        return raw, False
    return parsed.isoformat(), parsed < _CITATION_CUTOFF


def _context(sentence: str) -> tuple[str, str, str]:
    animal = _ANIMAL.search(sentence)
    human = _HUMAN.search(sentence)
    vitro = _IN_VITRO.search(sentence)
    if human:
        species = "human"
    elif animal:
        species = "animal"
    else:
        species = "unknown"
    if vitro:
        setting = "in_vitro"
    elif human:
        setting = "clinical_or_human_in_vivo"
    elif animal:
        setting = "animal_in_vivo"
    else:
        setting = "unknown"
    observed = [
        label for label, match in (
            ("human", human), ("animal", animal), ("in_vitro", vitro)
        ) if match
    ]
    return (
        species,
        setting,
        ", ".join(observed) or "not stated in evidence sentence",
    )


def _direction(sentence: str) -> tuple[str, str]:
    for direction, pattern in _ACTION_RULES:
        match = pattern.search(sentence)
        if match:
            return direction, match.group(0)
    return "unknown", ""


def search_drug_mechanism_assertions(
    drug_name: str,
    mechanism_symbol: str,
    *,
    deadline_monotonic: Optional[float] = None,
) -> dict[str, Any]:
    """Resolve both entities and return admitted relation assertions.

    ``filtered_empty`` is used when results were retrieved but none met the
    predeclared citation/action/primary-paper gate.  Parse and transport failures
    are not cached.
    """
    drug_name = (drug_name or "").strip()
    mechanism_symbol = (mechanism_symbol or "").strip()
    unresolved_drug = {
        "canonical_id": "", "database_id": "", "name": drug_name,
        "type": "chemical",
    }
    unresolved_mechanism = {
        "canonical_id": "", "database_id": "", "name": mechanism_symbol,
        "type": "gene",
    }
    key = make_key(
        f"pubtator_audit_assertions_{_CACHE_VERSION}",
        drug_name.lower(), mechanism_symbol.upper(),
    )
    cached = get(key)
    if cached is not None:
        return cached
    if not drug_name or not mechanism_symbol:
        result = _envelope(
            "empty", unresolved_drug, unresolved_mechanism, [])
        cache_set(key, result, ttl_days=_TTL_DAYS)
        return result

    try:
        drug_status, drug = _resolve(
            drug_name, "chemical",
            deadline_monotonic=deadline_monotonic)
        if drug_status != "ok":
            result = _envelope(
                drug_status, unresolved_drug, unresolved_mechanism, [])
            if drug_status in {"empty", "filtered_empty"}:
                cache_set(key, result, ttl_days=_TTL_DAYS)
            return result
        mechanism_status, mechanism = _resolve(
            mechanism_symbol, "gene",
            deadline_monotonic=deadline_monotonic)
        if mechanism_status != "ok":
            result = _envelope(
                mechanism_status, drug, unresolved_mechanism, [])
            if mechanism_status in {"empty", "filtered_empty"}:
                cache_set(key, result, ttl_days=_TTL_DAYS)
            return result

        query = (
            f"relations:ANY|{drug['canonical_id']}|"
            f"{mechanism['canonical_id']}"
        )
        payload = _json_get(
            "/search/", {"text": query},
            deadline_monotonic=deadline_monotonic)
    except (requests.RequestException, requests.Timeout) as exc:
        return _envelope(
            "unavailable", unresolved_drug, unresolved_mechanism, [],
            f"{type(exc).__name__}: {exc}")
    except (ValueError, TypeError) as exc:
        return _envelope(
            "parse_failed", unresolved_drug, unresolved_mechanism, [],
            f"invalid JSON: {exc}")

    if payload is None:
        result = _envelope("empty", drug, mechanism, [])
        cache_set(key, result, ttl_days=_TTL_DAYS)
        return result
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return _envelope(
            "parse_failed", drug, mechanism, [],
            "search results is not a list")
    rows = payload["results"][:_MAX_RESULTS]
    if any(not isinstance(row, dict) for row in rows):
        return _envelope(
            "parse_failed", drug, mechanism, [],
            "search result row is not an object",
            retrieved_count=len(rows))
    if not rows:
        result = _envelope("empty", drug, mechanism, [])
        cache_set(key, result, ttl_days=_TTL_DAYS)
        return result

    pmids = sorted({
        str(row.get("pmid") or "").strip()
        for row in rows
        if str(row.get("pmid") or "").strip()
    })
    metadata_status, metadata, metadata_error = _metadata_for_pmids(
        pmids, deadline_monotonic=deadline_monotonic)
    if metadata_status in {"unavailable", "parse_failed"}:
        # PubTator relation hits cannot be admitted without independently dated
        # publication-type metadata. Preserve this as degradation, not absence.
        return _envelope(
            "degraded", drug, mechanism, [],
            f"Europe PMC metadata {metadata_status}: {metadata_error}",
            retrieved_count=len(rows),
            source_status={
                "pubtator3": {"status": "ok", "error": None},
                "europepmc": {
                    "status": metadata_status,
                    "error": metadata_error,
                },
            },
        )

    admitted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        pmid = str(row.get("pmid") or "").strip()
        if not pmid:
            continue
        publication = metadata.get(pmid)
        if not publication:
            continue
        publication_types = publication["publication_types"]
        if not publication_types:
            continue
        raw_highlight = row.get("text_hl")
        sentence = _clean_highlight(raw_highlight)
        publication_type_text = " ".join(publication_types)
        if (not sentence or _REJECT.search(sentence)
                or _REVIEW.search(
                    f"{row.get('title', '')} {sentence} {publication_type_text}")):
            continue
        direction, action = _direction(sentence)
        if direction == "unknown":
            continue
        span_bundle = _admitted_sentence_and_spans(
            sentence, raw_highlight, drug, mechanism, action)
        if span_bundle is None:
            continue
        sentence, drug_entity, mechanism_entity, relation_span = span_bundle
        publication_date, citation_eligible = _publication_date(
            publication.get("publication_date") or row.get("date"))
        if not citation_eligible:
            continue
        source_row_id = str(row.get("_id") or pmid)
        sentence_digest = hashlib.sha256(
            sentence.encode("utf-8")).hexdigest()[:16]
        lineage = (
            f"pub:{pmid}|drug={drug['canonical_id']}|"
            f"mechanism={mechanism['canonical_id']}|direction={direction}|"
            f"row={source_row_id}|sentence={sentence_digest}"
        )
        if lineage in seen:
            continue
        seen.add(lineage)
        species, experimental_setting, experimental_context = _context(sentence)
        admitted.append({
            "source_row_id": source_row_id,
            "pmid": pmid,
            "pmcid": str(row.get("pmcid") or ""),
            "doi": str(row.get("doi") or ""),
            "title": str(row.get("title") or ""),
            "journal": str(row.get("journal") or ""),
            "publication_types": publication_types,
            "publication_date": publication_date,
            "citation_eligible": citation_eligible,
            "drug_entity": drug_entity,
            "mechanism_entity": mechanism_entity,
            "species": species,
            "organism": "",
            "experimental_setting": experimental_setting,
            "experimental_context": experimental_context,
            "relation": "PubTator3 entity-relation search",
            "action": action,
            "direction": direction,
            "evidence_sentence": sentence,
            "evidence_location": "PubTator3 relation-search highlighted passage",
            "relation_span": relation_span,
            "primary_experiment": True,
            "publication_type_status": "admitted by Europe PMC publication type",
            "source": "pubtator3",
            "lineage_id": lineage,
        })

    status = "ok" if admitted else "filtered_empty"
    result = _envelope(
        status, drug, mechanism, admitted,
        retrieved_count=len(rows),
        filtered_count=len(rows) - len(admitted),
        source_status={
            "pubtator3": {"status": "ok", "error": None},
            "europepmc": {"status": metadata_status, "error": None},
        },
    )
    cache_set(key, result, ttl_days=_TTL_DAYS)
    return result