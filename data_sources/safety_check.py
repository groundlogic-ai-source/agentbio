"""
Layer 2 market-safety disclosure.

``confirmed`` is deliberately a compatibility projection.  In safety-v3 it is
true only when an authoritative regulator quote identifies the requested
active ingredient (including a known synonym), the affected jurisdiction and
formulation, and explicitly reports a safety withdrawal/discontinuation.
Commercial product discontinuation, availability notices, warnings, and boxed
warnings are separate dispositions and cannot set it.
"""

import json
import os
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

import anthropic
import requests

from cache.cache import get, set as cache_set, make_key
from data_sources.llm_failover import call_with_backoff, chat_text

SCHEMA_VERSION = "safety-v3"
_CACHE_NAMESPACE = "web_safety_check_safety-v3"
_NO_INFO_TEXT = (
    "No regulator-confirmed market-withdrawal information found in this search; "
    "this does not confirm the compound is safe."
)
_AI_TIMEOUT_SECONDS = 60.0
_AI_MAX_RETRIES = 0
_SOURCE_TIMEOUT_SECONDS = 8.0
_SOURCE_MAX_BYTES = 1_000_000
# The web-search tool can return full regulator pages in its transcript. Keep
# the second classification call bounded and fail closed if relevant evidence
# falls outside the retained prefix.
_SEARCH_TEXT_MAX_CHARS = 24_000

_DISPOSITIONS = {
    "WITHDRAWN_FOR_SAFETY",
    "SAFETY_DISCONTINUED",
    "BRAND_DISCONTINUED",
    "MANUFACTURER_DISCONTINUED",
    "NOT_MARKETED",
    "INGREDIENT_UNAVAILABLE",
    "ORDINARY_WARNING",
    "BOXED_WARNING",
    "NO_WITHDRAWAL",
    "UNCLEAR",
}
_CONFIRMING = {"WITHDRAWN_FOR_SAFETY", "SAFETY_DISCONTINUED"}
_AUTHORITY_HOSTS = (
    "fda.gov", "ema.europa.eu", "mhra.gov.uk", "gov.uk", "tga.gov.au",
    "canada.ca", "health.gov", "medsafe.govt.nz", "swissmedic.ch",
)
_ALIASES = {
    "glyburide": "glibenclamide",
    "glibenclamide": "glibenclamide",
}


def normalize_drug_identity(value: str | None) -> str:
    """Normalize identity for scoped evidence matching (not display)."""
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return _ALIASES.get(text, text)


def _authoritative_url(url: str | None) -> bool:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if parsed.port not in {None, 443}:
        return False
    return any(
        host == allowed or host.endswith("." + allowed)
        for allowed in _AUTHORITY_HOSTS
    )


def _fetch_regulator_source(url: str) -> dict[str, Any]:
    """Retrieve bounded readable text from one allowlisted regulator URL.

    Redirects are disabled so an allowlisted URL cannot bounce verification to
    an arbitrary host. Binary/PDF sources are disclosure-only because quote and
    scope cannot be independently verified as readable text here.
    """
    if not _authoritative_url(url):
        return {"verified": False, "text": "", "reason": "url_not_allowlisted"}
    try:
        response = requests.get(
            url,
            timeout=_SOURCE_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
            headers={"User-Agent": "AgentBio-Safety-Provenance/1.0"},
        )
        if response.status_code != 200:
            return {
                "verified": False,
                "text": "",
                "reason": f"http_status_{response.status_code}",
            }
        final_url = str(getattr(response, "url", "") or url)
        if not _authoritative_url(final_url):
            return {
                "verified": False,
                "text": "",
                "reason": "response_url_not_allowlisted",
            }
        content_type = str(response.headers.get("content-type") or "").lower()
        if (
            "pdf" in content_type
            or not (
                content_type.startswith("text/")
                or "application/xhtml+xml" in content_type
            )
        ):
            return {
                "verified": False,
                "text": "",
                "reason": "unreadable_content_type",
            }
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            total += len(chunk)
            if total > _SOURCE_MAX_BYTES:
                return {
                    "verified": False,
                    "text": "",
                    "reason": "source_too_large",
                }
            chunks.append(chunk)
        encoding = response.encoding or "utf-8"
        raw = b"".join(chunks).decode(encoding, errors="replace")
        # A conservative HTML-to-text projection is sufficient for exact quote
        # verification; scripts/styles are removed before tags.
        raw = re.sub(
            r"(?is)<(script|style)\b.*?</\1>", " ", raw)
        text = unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
        text = " ".join(text.split())
        if not text:
            return {"verified": False, "text": "", "reason": "empty_text"}
        return {"verified": True, "text": text, "reason": None}
    except (
        requests.RequestException, LookupError, UnicodeError, ValueError
    ) as exc:
        return {
            "verified": False,
            "text": "",
            "reason": f"request_failed:{type(exc).__name__}",
        }


def _normalized_contains(text: str, phrase: str | None) -> bool:
    haystack = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    needle = re.sub(r"[^a-z0-9]+", " ", (phrase or "").casefold()).strip()
    return bool(needle and re.search(rf"\b{re.escape(needle)}\b", haystack))


def _fetched_identity_matches(drug_name: str, matched_identity: str | None,
                              fetched_text: str) -> bool:
    if normalize_drug_identity(matched_identity) != normalize_drug_identity(
            drug_name):
        return False
    canonical = normalize_drug_identity(drug_name)
    accepted = {canonical}
    if canonical == "glibenclamide":
        accepted.add("glyburide")
    return any(_normalized_contains(fetched_text, name) for name in accepted)


def _parse_classifier(text: str) -> dict[str, Any]:
    """Accept the v3 JSON contract and conservative line-oriented fallbacks."""
    parsed: dict[str, Any] = {}
    try:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate,
                               flags=re.I)
        obj = json.loads(candidate)
        if isinstance(obj, dict):
            parsed = {str(k).lower(): v for k, v in obj.items()}
    except (ValueError, TypeError):
        pass

    if not parsed:
        key_map = {
            "safety_status": "safety_status", "disposition": "safety_status",
            "withdrawal": "withdrawal", "verdict": "withdrawal",
            "black_box": "black_box", "black box": "black_box",
            "source_url": "source_url", "citation": "source_url",
            "source_name": "source_name", "authority": "authority",
            "exact_quote": "exact_quote", "quote": "exact_quote",
            "jurisdiction": "jurisdiction", "formulation": "formulation",
            "matched_identity": "matched_identity",
            "contradictions": "contradictions",
        }
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            normalized = key_map.get(key.strip().lower())
            if normalized:
                parsed[normalized] = value.strip()
    return parsed


def _yn(value: Any) -> str:
    value = str(value or "").strip().upper()
    if value == "YES":
        return "YES"
    if value == "NO":
        return "NO"
    return "UNCLEAR"


def _text_contradictions(search_text: str) -> list[str]:
    """Find high-value contradictions independently of the LLM."""
    lowered = " ".join(search_text.lower().split())
    found = []
    patterns = (
        ("generics remain available",
         r"\bgeneric(?:s| versions?| products?)?\b.{0,80}\b(?:remain|are|is|still)\b.{0,40}\bavailable\b"),
        ("generic alternatives available",
         r"\bgeneric alternatives?\b.{0,60}\bavailable\b"),
        ("generic supply continues",
         r"\b(?:other )?manufacturers?\b.{0,80}\b(?:continue|continues|continuing)\b.{0,60}\b(?:supply|market|make|available)\b"),
        ("no formal withdrawal",
         r"\bno (?:formal |regulatory |market )?withdrawal\b"),
        ("not withdrawn",
         r"\b(?:has|have|was|were|is) not (?:been )?withdrawn\b"),
        ("remains marketed",
         r"\b(?:remains?|still) (?:on the market|marketed|available)\b"),
    )
    for label, pattern in patterns:
        if re.search(pattern, lowered):
            found.append(label)
    return found


def classify_safety_evidence(
    drug_name: str, search_text: str, classifier_text: str
) -> dict[str, Any]:
    """Validate a classifier answer against the evidence it was given."""
    data = _parse_classifier(classifier_text)
    status = str(data.get("safety_status") or "UNCLEAR").strip().upper()
    if status not in _DISPOSITIONS:
        status = "UNCLEAR"

    withdrawal = _yn(data.get("withdrawal"))
    black_box = _yn(data.get("black_box"))
    source_url = str(data.get("source_url") or "").strip() or None
    source_name = str(data.get("source_name") or "").strip() or None
    exact_quote = str(data.get("exact_quote") or "").strip() or None
    jurisdiction = str(data.get("jurisdiction") or "").strip() or None
    formulation = str(data.get("formulation") or "").strip() or None
    matched_identity = str(data.get("matched_identity") or "").strip() or None

    contradictions = _text_contradictions(search_text)
    supplied = data.get("contradictions")
    if isinstance(supplied, list):
        contradictions.extend(str(item) for item in supplied if str(item).strip())
    elif supplied and str(supplied).lower() not in {"none", "[]"}:
        contradictions.append(str(supplied))
    contradictions = list(dict.fromkeys(contradictions))

    identity_matches = (
        bool(matched_identity)
        and normalize_drug_identity(matched_identity)
        == normalize_drug_identity(drug_name)
    )
    candidate_yes = status in _CONFIRMING and withdrawal == "YES"
    source_verification_required = (
        candidate_yes or status == "INGREDIENT_UNAVAILABLE")
    retrieval = (
        _fetch_regulator_source(source_url)
        if source_verification_required and source_url
        and _authoritative_url(source_url)
        and not contradictions
        else {
            "verified": False,
            "text": "",
            "reason": (
                "contradictory_search_evidence" if contradictions
                else "no_allowlisted_candidate_source"
            ),
        }
    )
    fetched_text = str(retrieval.get("text") or "")
    contradictions.extend(_text_contradictions(fetched_text))
    contradictions = list(dict.fromkeys(contradictions))
    authority_verified = retrieval.get("verified") is True
    quote_verified = bool(
        authority_verified and exact_quote
        and " ".join(exact_quote.casefold().split())
        in " ".join(fetched_text.casefold().split())
    )
    fetched_identity_verified = bool(
        authority_verified
        and _fetched_identity_matches(
            drug_name, matched_identity, fetched_text)
    )
    jurisdiction_verified = bool(
        authority_verified
        and _normalized_contains(fetched_text, jurisdiction)
    )
    formulation_verified = bool(
        authority_verified
        and _normalized_contains(fetched_text, formulation)
    )
    scope_complete = bool(
        jurisdiction and formulation and identity_matches
        and fetched_identity_verified
        and jurisdiction_verified
        and formulation_verified
    )

    confidence_status = "CLEAR"
    if candidate_yes and contradictions:
        confidence_status = "CONFLICT"
    elif candidate_yes and not (
        authority_verified and quote_verified and scope_complete
    ):
        confidence_status = "UNCLEAR"
    elif status == "UNCLEAR" or withdrawal == "UNCLEAR":
        confidence_status = "UNCLEAR"

    confirmed = candidate_yes and confidence_status == "CLEAR"
    verdict = "YES" if confirmed else (
        "CONFLICT" if confidence_status == "CONFLICT" else
        "UNCLEAR" if candidate_yes or withdrawal == "UNCLEAR" else "NO"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "safety_status": status,
        "confidence_status": confidence_status,
        "authoritative_source": {
            "name": source_name,
            "url": source_url,
            "verified_regulator_domain": authority_verified,
            "retrieval_status": (
                "VERIFIED" if authority_verified else "UNVERIFIED"),
            "retrieval_reason": retrieval.get("reason"),
        },
        "exact_quote": exact_quote,
        "quote_verified_in_fetched_source": quote_verified,
        "scope": {
            "requested_identity": drug_name,
            "matched_identity": matched_identity,
            "normalized_identity": normalize_drug_identity(drug_name),
            "identity_matches": identity_matches,
            "fetched_identity_verified": fetched_identity_verified,
            "jurisdiction": jurisdiction,
            "jurisdiction_verified": jurisdiction_verified,
            "formulation": formulation,
            "formulation_verified": formulation_verified,
        },
        "contradictions": contradictions,
        # Compatibility projections consumed by existing reviewer/report code.
        "confirmed": confirmed,
        "black_box_advisory": black_box == "YES" and not confirmed,
        "layer": "web_search",
        "verdict": verdict,
        "black_box_verdict": black_box,
        "citation": source_url,
        "search_summary": search_text,
        "disclosure_text": (
            f"MARKET WITHDRAWAL / SAFETY DISCONTINUATION CONFIRMED "
            f"(regulator-confirmed {status}) for {drug_name} "
            f"in {jurisdiction} ({formulation}). Source: {source_url}"
            if confirmed else _NO_INFO_TEXT
        ),
    }


def _empty_result(drug_name: str, verdict: str = "SKIPPED") -> dict[str, Any]:
    result = classify_safety_evidence(drug_name, "", "")
    result["verdict"] = verdict
    result["black_box_verdict"] = verdict
    return result


def _bound_search_text(search_text: str) -> str:
    """Keep provider-returned evidence bounded before the classifier sees it."""
    if len(search_text) <= _SEARCH_TEXT_MAX_CHARS:
        return search_text
    return (
        search_text[:_SEARCH_TEXT_MAX_CHARS]
        + "\n[search transcript truncated; omitted text is not classified]"
    )


def web_safety_check(drug_name: str) -> dict[str, Any]:
    """Search and conservatively classify scoped market-safety evidence."""
    cache_key = make_key(_CACHE_NAMESPACE, drug_name)
    cached = get(cache_key)
    if (isinstance(cached, dict)
            and cached.get("schema_version") == SCHEMA_VERSION):
        return cached

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    result = _empty_result(drug_name)
    if not base_url or not api_key:
        result["disclosure_text"] = (
            "Layer 2 web-search check skipped — AI integration not configured. "
            + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)
        return result

    try:
        client = anthropic.Anthropic(
            base_url=base_url, api_key=api_key, timeout=_AI_TIMEOUT_SECONDS,
            max_retries=_AI_MAX_RETRIES,
        )
        query = (
            f'For the active ingredient "{drug_name}", find regulator records of '
            "formal safety withdrawal or safety discontinuation. Separately report "
            "brand/manufacturer discontinuation, not-marketed or unavailable "
            "ingredient notices, ordinary warnings, boxed warnings, and evidence "
            "that generics remain available or no formal withdrawal occurred. "
            "Give regulator URLs and verbatim quotes, jurisdiction, formulation, "
            "and the exact active ingredient identity. Return a concise summary "
            "with at most five relevant regulator records; do not paste full "
            "web pages or long unrelated search results."
        )
        response = call_with_backoff(
            lambda: client.messages.create(
                model="claude-sonnet-4-6", max_tokens=1024,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": query}],
            ),
            label="safety-web-search", provider="anthropic",
            model="claude-sonnet-4-6",
        )
        search_text = "\n".join(
            block.text for block in response.content
            if getattr(block, "text", None)
        ).strip()
        search_text = _bound_search_text(search_text)
        if not search_text:
            result = _empty_result(drug_name, "UNCLEAR")
            cache_set(cache_key, result, ttl_days=1)
            return result

        prompt = f"""Classify only the supplied text for {drug_name!r}.
Use exactly one safety_status: WITHDRAWN_FOR_SAFETY, SAFETY_DISCONTINUED,
BRAND_DISCONTINUED, MANUFACTURER_DISCONTINUED, NOT_MARKETED,
INGREDIENT_UNAVAILABLE, ORDINARY_WARNING, BOXED_WARNING, NO_WITHDRAWAL, UNCLEAR.
WITHDRAWAL may be YES only for an explicit regulator-confirmed safety action
covering this active ingredient. Commercial discontinuation and warnings are
never withdrawal. Preserve jurisdiction/formulation scope. Report contrary
evidence such as generics available or no formal withdrawal.

Return one JSON object with keys: safety_status, withdrawal (YES/NO/UNCLEAR),
black_box (YES/NO/UNCLEAR), source_name, source_url, exact_quote, jurisdiction,
formulation, matched_identity, contradictions (array). exact_quote must be a
verbatim substring of the supplied text.

--- supplied text ---
{search_text}
--- end text ---"""
        classify_text, _provider = chat_text(prompt, max_tokens=512)
        result = classify_safety_evidence(drug_name, search_text, classify_text)
        cache_set(cache_key, result, ttl_days=30)
    except Exception as exc:
        print(f"[safety_check] WARNING: web safety check failed for "
              f"'{drug_name}': {exc}")
        result = _empty_result(drug_name, "ERROR")
        result["disclosure_text"] = (
            f"Layer 2 web-search check encountered an error for '{drug_name}': "
            f"{exc}. Treating as unconfirmed (no cap applied from Layer 2). "
            + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)
    return result