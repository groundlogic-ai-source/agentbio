"""Retrieval-first literature limitation gate for candidate prioritization.

This lane asks a different question from mechanism direction: applicable
literature can reject a therapeutic class even when its target-level direction
looks plausible.  PubMed supplies every passage and identifier; an LLM may only
classify and quote those supplied abstracts.  Plain Python verifies quotes and
citations and decides whether the evidence is strong enough to block external
prioritization.
"""

from __future__ import annotations

import json
import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

import requests

from cache.cache import get, make_key, set as cache_set
from data_sources.llm_failover import chat_text
from data_sources.pubmed import BASE_URL, _api_key_params, _esearch


VERDICT_CONFIRMED = "CONFIRMED_APPLICABLE_LIMITATION"
VERDICT_CAUTION = "CAUTION_ONLY"
VERDICT_CONFLICTING = "CONFLICTING_EVIDENCE"
VERDICT_NONE = "NO_QUALIFYING_LIMITATION_FOUND"
VERDICT_FAILED = "SEARCH_FAILED"
VERDICT_NOT_ASSESSED = "NOT_ASSESSED"

_HIGH_AUTHORITY_TYPES = {
    "guideline",
    "practice guideline",
    "systematic review",
    "meta-analysis",
    "consensus development conference",
    "consensus development conference, nih",
}
_ALLOWED_LABELS = {
    "EXPLICIT_LIMITATION", "CAUTION", "SUPPORT", "IRRELEVANT",
}
_NEGATIVE_WORDS = re.compile(
    r"\b(ineffective|not effective|failed|no benefit|did not improve|"
    r"does not improve|lack(?:ed|s)? efficacy|not recommended|"
    r"poor response|resistan(?:t|ce)|worsen(?:ed|s|ing)?)\b",
    re.IGNORECASE,
)
_SUPPORT_WORDS = re.compile(
    r"\b(benefit(?:ed|s)?|effective|efficacy|improv(?:e|ed|ement|es)|"
    r"recommend(?:ed|s)?|respond(?:ed|s)?|successful)\b",
    re.IGNORECASE,
)
_TERM_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "disease", "for", "in", "of", "or",
    "the", "to", "treat", "treated", "treating", "treatment", "therapy",
    "use", "using", "with",
    # Generic action words do not identify a therapeutic class by themselves.
    "activate", "activates", "activator", "agonist", "antagonist", "block",
    "blocked", "blocker", "blocks", "inhibit", "inhibited", "inhibitor",
    "modulate", "modulator",
}


def _terms(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", str(value or ""))
        if len(token) >= 3 and token.casefold() not in _TERM_STOPWORDS
    }


def _phrase_or_term_match(
    text: str,
    value: str,
    *,
    minimum_overlap: int = 1,
    minimum_fraction: float = 0.75,
) -> bool:
    normalized_text = " ".join(re.findall(r"[A-Za-z0-9]+", text)).casefold()
    normalized_value = " ".join(re.findall(r"[A-Za-z0-9]+", value)).casefold()
    if normalized_value and normalized_value in normalized_text:
        return True
    wanted = _terms(value)
    present = _terms(text)
    if not wanted:
        return False
    overlap = len(wanted & present)
    return overlap >= minimum_overlap and overlap / len(wanted) >= minimum_fraction


def _use_terms(intended_use: str, disease_name: str, target_symbol: str) -> set[str]:
    """Return clinically meaningful use facets, excluding identity/genotype syntax."""
    terms = _terms(intended_use) - _terms(disease_name) - _terms(target_symbol)
    return {
        term for term in terms
        if not re.fullmatch(r"(?:p)?[a-z]?\d+[a-z]*", term, re.IGNORECASE)
        and term not in {"exon", "variant", "mutation", "genotype"}
    }


def _fetch_records(pmids: list[str]) -> list[dict[str, Any]]:
    """Fetch citable PubMed records. Raises on source failure; never returns it as empty."""
    if not pmids:
        return []
    response = requests.get(
        f"{BASE_URL}efetch.fcgi",
        params={
            "db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
            "rettype": "abstract", **_api_key_params(),
        },
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    records: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = (article.findtext(".//PMID") or "").strip()
        title = "".join(article.find(".//ArticleTitle").itertext()).strip() \
            if article.find(".//ArticleTitle") is not None else ""
        abstract = " ".join(
            "".join(node.itertext()).strip()
            for node in article.findall(".//Abstract/AbstractText")
        ).strip()
        if not pmid or not abstract:
            continue
        pub_types = sorted({
            (node.text or "").strip()
            for node in article.findall(".//PublicationType")
            if (node.text or "").strip()
        })
        year = (
            article.findtext(".//PubDate/Year")
            or article.findtext(".//ArticleDate/Year")
            or ""
        ).strip()
        records.append({
            "pmid": pmid,
            "title": title,
            "abstract": abstract,
            "publication_types": pub_types,
            "publication_year": year or None,
            "source_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        })
    return records


def _query_families(
    drug_name: str,
    disease_name: str,
    target_symbol: str,
    mechanism_of_action: Optional[str],
) -> list[str]:
    disease = f'"{disease_name}"'
    drug = f'"{drug_name}"'
    target = f'"{target_symbol}"'
    mechanism_words = " ".join(
        re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", mechanism_of_action or "")[:8]
    )
    mechanism = f' OR "{mechanism_words}"' if mechanism_words else ""
    limitation = (
        "ineffective OR efficacy OR failed OR failure OR \"no benefit\" OR "
        "\"not recommended\" OR resistance"
    )
    return [
        f"{disease} AND {drug} AND ({limitation})",
        f"{disease} AND ({target}{mechanism}) AND ({limitation})",
        f"{disease} AND ({drug} OR {target}) AND "
        "(guideline OR consensus OR \"systematic review\")",
        f"{disease} AND ({target}{mechanism}) AND "
        "(genotype OR mutation OR gating OR trafficking OR response)",
    ]


def retrieve_literature(
    drug_name: str,
    disease_name: str,
    target_symbol: str,
    mechanism_of_action: Optional[str],
    *,
    retmax_per_query: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Run bounded deterministic query families and return de-duplicated records."""
    queries = _query_families(
        drug_name, disease_name, target_symbol, mechanism_of_action)
    pmids: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for pmid in _esearch(query, retmax_per_query):
            if pmid not in seen:
                seen.add(pmid)
                pmids.append(pmid)
    return queries, _fetch_records(pmids)


def _extract_json(text: str) -> Optional[dict[str, Any]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def classify_record(
    record: dict[str, Any],
    *,
    drug_name: str,
    drug_class: str,
    disease_name: str,
    intended_use: str,
    target_symbol: str,
) -> dict[str, Any]:
    """Constrained extraction from one retrieved record; no external facts allowed."""
    prompt = f"""Classify one supplied PubMed abstract for a literature limitation gate.

Candidate: {drug_name}
Drug class/mechanism: {drug_class or 'unknown'}
Target: {target_symbol}
Exact disease: {disease_name}
Intended use: {intended_use}

Allowed labels:
- EXPLICIT_LIMITATION: the abstract explicitly says this drug/class is ineffective,
  failed, not recommended, lacks benefit, worsens disease, or has a mechanistic
  limitation for this exact disease/use.
- CAUTION: relevant concern or uncertainty, but no explicit ineffectiveness statement.
- SUPPORT: reports benefit or recommends the drug/class for this exact disease/use.
- IRRELEVANT: wrong disease/subtype/use/class, or only says evidence is insufficient.

Return one JSON object and nothing else:
{{"label":"...","quote":"exact contiguous quote from abstract or empty",
  "disease_match":true|false,"use_match":true|false,
  "drug_or_class_match":true|false,"reason":"one sentence"}}
The quote must be copied exactly from the abstract. Do not use outside knowledge.

PMID: {record.get('pmid')}
Title: {record.get('title')}
Abstract:
{str(record.get('abstract') or '')[:6000]}"""
    raw, provider = chat_text(prompt, max_tokens=500)
    parsed = _extract_json(raw) or {}
    return {**parsed, "classifier_provider": provider, "classifier_raw": raw}


def classify_records_batch(
    records: list[dict[str, Any]],
    *,
    drug_name: str,
    drug_class: str,
    disease_name: str,
    intended_use: str,
    target_symbol: str,
) -> list[dict[str, Any]]:
    """Classify a bounded record batch in one model call, keyed by retrieved PMID."""
    supplied = "\n\n".join(
        f"PMID {record['pmid']}\nTITLE: {record.get('title')}\n"
        f"ABSTRACT: {str(record.get('abstract') or '')[:2500]}"
        for record in records
    )
    prompt = f"""Classify supplied PubMed abstracts for a literature limitation gate.
Candidate: {drug_name}
Drug class/mechanism: {drug_class or 'unknown'}
Target: {target_symbol}
Exact disease: {disease_name}
Intended use: {intended_use}

For every supplied PMID return one finding. Allowed labels:
EXPLICIT_LIMITATION = explicit ineffective/failed/not recommended/no benefit/
mechanistic limitation for the exact disease and use.
CAUTION = relevant concern without explicit ineffectiveness.
SUPPORT = benefit or recommendation for the exact disease/use.
IRRELEVANT = wrong subtype/use/class or only insufficient evidence.

Return one JSON object only:
{{"findings":[{{"pmid":"retrieved PMID","label":"...","quote":"exact contiguous
quote or empty","disease_match":true|false,"use_match":true|false,
"drug_or_class_match":true|false,"reason":"one sentence"}}]}}
Use only supplied text. Never omit a PMID and never use outside knowledge.

{supplied}"""
    raw, provider = chat_text(prompt, max_tokens=2400)
    parsed = _extract_json(raw)
    if parsed is None or not isinstance(parsed.get("findings"), list):
        raise ValueError("Classifier returned invalid JSON or no findings array")
    findings = parsed["findings"]
    expected = [str(record.get("pmid")) for record in records]
    returned = [
        str(row.get("pmid"))
        for row in findings
        if isinstance(row, dict)
    ]
    if len(findings) != len(returned) or len(returned) != len(set(returned)):
        raise ValueError("Classifier returned malformed or duplicate findings")
    if set(returned) != set(expected):
        raise ValueError("Classifier omitted or invented a retrieved PMID")
    if any(
        str(row.get("label") or "").upper() not in _ALLOWED_LABELS
        for row in findings
    ):
        raise ValueError("Classifier returned an unsupported finding label")
    by_pmid = {str(row.get("pmid")): row for row in findings}
    return [
        {
            **by_pmid[str(record.get("pmid"))],
            "classifier_provider": provider,
            "classifier_raw": raw,
        }
        for record in records
    ]


def _quote_is_verbatim(quote: Any, abstract: Any) -> bool:
    quote_norm = " ".join(str(quote or "").split()).casefold()
    abstract_norm = " ".join(str(abstract or "").split()).casefold()
    return len(quote_norm) >= 20 and quote_norm in abstract_norm


def aggregate_findings(
    records: list[dict[str, Any]],
    classifications: list[dict[str, Any]],
    *,
    queries: Optional[list[str]] = None,
    disease_name: str = "",
    drug_name: str = "",
    drug_class: str = "",
    target_symbol: str = "",
    intended_use: str = "",
) -> dict[str, Any]:
    """Mechanically validate extractions and determine the gate verdict."""
    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    classifier_integrity_failures = 0
    for record, classification in zip(records, classifications):
        label = str(classification.get("label") or "").upper()
        classifier_match = all(classification.get(key) is True for key in (
            "disease_match", "use_match", "drug_or_class_match"))
        source_text = (
            f"{record.get('title') or ''} {record.get('abstract') or ''}")
        deterministic_disease = _phrase_or_term_match(
            source_text, disease_name, minimum_overlap=1,
            minimum_fraction=0.8)
        # Target presence is supporting context, not proof that the cited
        # limitation applies to this drug or therapeutic class.
        deterministic_intervention = any(
            _phrase_or_term_match(
                source_text,
                value,
                minimum_overlap=1 if value == drug_name else 2,
                minimum_fraction=0.8 if value == drug_name else 0.6,
            )
            for value in (drug_name, drug_class)
            if _terms(value)
        )
        use_specific_terms = _use_terms(
            intended_use, disease_name, target_symbol)
        use_overlap = use_specific_terms & _terms(source_text)
        deterministic_use = (
            deterministic_disease
            if not use_specific_terms
            else (
                len(use_overlap) >= min(2, len(use_specific_terms))
                and len(use_overlap) / len(use_specific_terms) >= 0.75
            )
        )
        exact_match = bool(
            classifier_match
            and deterministic_disease
            and deterministic_intervention
            and deterministic_use
        )
        quote = classification.get("quote")
        valid_quote = _quote_is_verbatim(quote, record.get("abstract"))
        pmid = str(record.get("pmid") or "").strip()
        expected_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        valid_citation = bool(
            pmid.isdigit() and record.get("source_url") == expected_url)
        valid_label = label in _ALLOWED_LABELS
        explicit_language = bool(_NEGATIVE_WORDS.search(str(quote or "")))
        supportive_language = bool(_SUPPORT_WORDS.search(str(quote or "")))
        source_has_negative_language = bool(
            _NEGATIVE_WORDS.search(str(record.get("abstract") or "")))
        valid = (
            valid_label
            and valid_citation
            and (label == "IRRELEVANT" or (exact_match and valid_quote))
            and (label != "EXPLICIT_LIMITATION" or explicit_language)
            and (
                label != "SUPPORT"
                or (supportive_language and not explicit_language)
            )
            # A retrieved record containing explicit negative language cannot
            # silently disappear behind an IRRELEVANT label. Without a valid
            # exact-applicability extraction, classification is unresolved.
            and not (
                label == "IRRELEVANT" and source_has_negative_language
            )
        )
        row = {
            "pmid": record.get("pmid"),
            "title": record.get("title"),
            "publication_year": record.get("publication_year"),
            "publication_types": record.get("publication_types", []),
            "source_url": record.get("source_url"),
            "label": label if valid_label else "INVALID",
            "quote": quote if valid_quote else "",
            "reason": classification.get("reason"),
            "exact_applicability": exact_match,
            "classifier_applicability": classifier_match,
            "deterministic_applicability": {
                "disease": deterministic_disease,
                "drug_or_class": deterministic_intervention,
                "intended_use": deterministic_use,
                "intended_use_terms": sorted(use_specific_terms),
                "intended_use_terms_matched": sorted(use_overlap),
            },
            "mechanically_verified": valid,
            "citation_verified": valid_citation,
        }
        (evidence if valid else rejected).append(row)
        if not valid:
            classifier_integrity_failures += 1

    explicit = [row for row in evidence if row["label"] == "EXPLICIT_LIMITATION"]
    support = [row for row in evidence if row["label"] == "SUPPORT"]
    cautions = [row for row in evidence if row["label"] == "CAUTION"]
    authoritative = [
        row for row in explicit
        if _HIGH_AUTHORITY_TYPES.intersection(
            {str(value).casefold() for value in row["publication_types"]})
    ]
    if explicit and support:
        verdict = VERDICT_CONFLICTING
        blocked = False
        reason = (
            "Applicable limitation and supportive findings conflict; the candidate "
            "is not automatically blocked, and the conflict requires qualified review."
        )
    elif authoritative or len({row["pmid"] for row in explicit}) >= 2:
        verdict = VERDICT_CONFIRMED
        blocked = True
        reason = (
            "An applicable limitation is supported by a guideline/systematic review "
            "or by at least two independent PubMed records."
        )
    elif classifier_integrity_failures:
        verdict = VERDICT_FAILED
        blocked = False
        reason = (
            "One or more retrieved records could not be mechanically reconciled "
            "with complete, verbatim, applicable classifier output. The result "
            "is unknown and paid validation is not authorized."
        )
    elif explicit or cautions:
        verdict = VERDICT_CAUTION
        blocked = False
        reason = (
            "A relevant limitation or caution was found, but it does not meet the "
            "deterministic threshold for an automatic prioritization block."
        )
    else:
        verdict = VERDICT_NONE
        blocked = False
        reason = (
            "No mechanically qualifying limitation was found in this bounded search; "
            "this is not evidence of novelty, efficacy, or safety."
        )
    return {
        "schema_version": "literature-limitation-v2",
        "verdict": verdict,
        "source_status": (
            "CLASSIFIER_INTEGRITY_FAILED"
            if verdict == VERDICT_FAILED else "HEALTHY"
        ),
        "blocked": blocked,
        "gate_cleared": verdict != VERDICT_FAILED,
        "reason": reason,
        "queries": queries or [],
        "records_screened": len(records),
        "evidence": evidence,
        "rejected_extractions": rejected,
        "explicit_limitation_count": len(explicit),
        "support_count": len(support),
        "classifier_integrity_failures": classifier_integrity_failures,
        "post_benchmark_production_gate": True,
    }


def check_literature_limitation(
    drug_name: str,
    disease_name: str,
    target_symbol: str,
    action_type: Optional[str],
    mechanism_of_action: Optional[str],
    intended_use: Optional[str] = None,
    *,
    retriever: Callable[..., tuple[list[str], list[dict[str, Any]]]] = retrieve_literature,
    classifier: Callable[..., dict[str, Any]] = classify_record,
) -> dict[str, Any]:
    """Retrieve, classify, mechanically verify, and aggregate limitation evidence."""
    intended = intended_use or f"treatment of {disease_name}"
    cache_key = make_key(
        "literature_limitation_v2_batch8_policy1",
        drug_name, disease_name, target_symbol,
        action_type or "", mechanism_of_action or "", intended,
    )
    if retriever is retrieve_literature and classifier is classify_record:
        cached = get(cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("schema_version") == "literature-limitation-v2"
            and cached.get("source_status") == "HEALTHY"
            and isinstance(cached.get("gate_cleared"), bool)
            and isinstance(cached.get("blocked"), bool)
            and cached.get("source_record_fingerprint")
        ):
            return cached
    try:
        queries, records = retriever(
            drug_name, disease_name, target_symbol, mechanism_of_action)
        # One bounded model call per candidate, not one per search hit. Prefer
        # explicit-negative and source-authority records, then retain source order.
        records = sorted(
            records,
            key=lambda row: (
                not bool(_NEGATIVE_WORDS.search(str(row.get("abstract") or ""))),
                not bool(re.search(
                    r"\b(consensus|guideline|systematic review|meta-analysis)\b",
                    str(row.get("title") or ""), re.IGNORECASE)),
            ),
        )[:8]
        drug_class = " / ".join(
            value for value in (action_type, mechanism_of_action) if value)
        if classifier is classify_record:
            classifications = classify_records_batch(
                records,
                drug_name=drug_name,
                drug_class=drug_class,
                disease_name=disease_name,
                intended_use=intended,
                target_symbol=target_symbol,
            )
        else:
            classifications = [
                classifier(
                    record,
                    drug_name=drug_name,
                    drug_class=drug_class,
                    disease_name=disease_name,
                    intended_use=intended,
                    target_symbol=target_symbol,
                )
                for record in records
            ]
        result = aggregate_findings(
            records,
            classifications,
            queries=queries,
            disease_name=disease_name,
            drug_name=drug_name,
            drug_class=drug_class,
            target_symbol=target_symbol,
            intended_use=intended,
        )
        result["source_record_fingerprint"] = hashlib.sha256(
            json.dumps(
                [{
                    "pmid": row.get("pmid"),
                    "title": row.get("title"),
                    "abstract": row.get("abstract"),
                    "publication_types": row.get("publication_types"),
                } for row in records],
                sort_keys=True,
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        if retriever is retrieve_literature and classifier is classify_record:
            # Authorization evidence is a short-lived source snapshot, not an
            # indefinitely reusable negative finding.
            cache_set(cache_key, result, ttl_days=7)
        return result
    except Exception as exc:
        # Source/classifier failures are not cached: a transient outage must not
        # become a durable "no limitation found" result.
        return {
            "schema_version": "literature-limitation-v2",
            "verdict": VERDICT_FAILED,
            "source_status": "FAILED",
            "blocked": False,
            "gate_cleared": False,
            "reason": (
                f"Literature limitation search failed: {exc}. No negative "
                "conclusion was inferred and paid validation is not authorized."
            ),
            "queries": [],
            "records_screened": 0,
            "evidence": [],
            "rejected_extractions": [],
            "post_benchmark_production_gate": True,
        }