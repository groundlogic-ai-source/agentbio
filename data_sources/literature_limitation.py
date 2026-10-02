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
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any, Callable, Optional

import requests

from cache.cache import get, make_key, set as cache_set
from data_sources.llm_failover import MODEL_TIER_CHEAP, chat_text
from data_sources.pubmed import BASE_URL, _api_key_params, _esearch
from data_sources.provider_request_policy import request as provider_request
from data_sources.literature_semantics import (
    APPLICABLE_SUPPORT,
    DRUG_ALIAS,
    DRUG_CLASS,
    EVIDENCE_LEVELS,
    EXACT_DRUG,
    EXPLICIT_LIMITATION,
    NO_INTERVENTION_MATCH,
    NOT_APPLICABLE_TO_EXACT_DRUG_USE,
    UNKNOWN_INTEGRITY_FAILED,
    canonical_exact_use_label,
    intervention_identity,
    legacy_projection,
)


VERDICT_CONFIRMED = "CONFIRMED_APPLICABLE_LIMITATION"
VERDICT_CAUTION = "CAUTION_ONLY"
VERDICT_CONFLICTING = "CONFLICTING_EVIDENCE"
VERDICT_NONE = "NO_QUALIFYING_LIMITATION_FOUND"
VERDICT_FAILED = "SEARCH_FAILED"
VERDICT_NOT_ASSESSED = "NOT_ASSESSED"
_LOGGER = logging.getLogger(__name__)

_HIGH_AUTHORITY_TYPES = {
    "guideline",
    "practice guideline",
    "systematic review",
    "meta-analysis",
    "consensus development conference",
    "consensus development conference, nih",
}
_ALLOWED_LABELS = {
    EXPLICIT_LIMITATION, APPLICABLE_SUPPORT,
    NOT_APPLICABLE_TO_EXACT_DRUG_USE, UNKNOWN_INTEGRITY_FAILED, "CAUTION",
}
_NEGATIVE_WORDS = re.compile(
    r"\b(ineffective|not effective|failed|no benefit|did not improve|"
    r"does not improve|lack(?:ed|s)? efficacy|not recommended|"
    r"poor response|resistan(?:t|ce)|worsen(?:ed|s|ing)|"
    r"abolish(?:es|ed)?|incomplet(?:e|ely) effective|"
    r"no significant (?:change|changes|improvement|difference)s?|"
    r"mixed (?:effect|effects|result|results)|unclear)\b",
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

_PHARMACOLOGY_CONTEXT_MARKERS = re.compile(
    r"\b(?:cns|central nervous|neurolog\w*|brain|blood[- ]brain|bbb|"
    r"p[- ]glycoprotein|abc[bc]1|compartment|tissue|exposure|penetration|"
    r"pharmacokinetic|distribution|selectiv(?:e|ity)|subtype|subunit|"
    r"isoform)\b",
    re.IGNORECASE,
)
_PHARMACOLOGY_CONTEXT_QUOTE_MARKERS = re.compile(
    r"\b(?:blood[- ]brain barrier|brain penetration|brain exposure|"
    r"cns penetration|cns exposure|p[- ]glycoprotein|abc[bc]1|"
    r"pharmacokinetic|pharmacokinetics|exposure|tissue distribution|"
    r"selectiv(?:e|ity)|subtype|subunit|isoform)\b",
    re.IGNORECASE,
)


def _fold_text(value: Any) -> str:
    """Fold Unicode accents before applying ASCII-oriented token matching."""
    return unicodedata.normalize("NFKD", str(value or "")).encode(
        "ascii", "ignore"
    ).decode("ascii")


def _terms(value: Any) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", _fold_text(value))
        if len(token) >= 3 and token.casefold() not in _TERM_STOPWORDS
    }


def _phrase_or_term_match(
    text: str,
    value: str,
    *,
    minimum_overlap: int = 1,
    minimum_fraction: float = 0.75,
) -> bool:
    normalized_text = " ".join(
        re.findall(r"[A-Za-z0-9]+", _fold_text(text))
    ).casefold()
    normalized_value = " ".join(
        re.findall(r"[A-Za-z0-9]+", _fold_text(value))
    ).casefold()
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
    response = provider_request(
        "ncbi", requests.get, f"{BASE_URL}efetch.fcgi",
        params={
            "db": "pubmed", "id": ",".join(pmids), "retmode": "xml",
            "rettype": "abstract", **_api_key_params(),
        },
        timeout=60,
    )
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


def _pharmacology_context_queries(
    drug_name: str,
    target_symbol: str,
    hypothesis_context: Any,
) -> list[str]:
    """Build bounded drug-level context searches from explicit hypothesis claims.

    These searches are intentionally separate from the disease/use limitation
    gate. They surface pharmacology facts for expert review, but generic
    exposure or selectivity papers must never become an automatic disease-
    specific veto.
    """
    if isinstance(hypothesis_context, dict):
        context = " ".join(
            str(value or "") for value in hypothesis_context.values())
    else:
        context = str(hypothesis_context or "")
    if not _PHARMACOLOGY_CONTEXT_MARKERS.search(context):
        return []

    drug = f'"{drug_name}"'
    target = f'"{target_symbol}"'
    queries: list[str] = []
    if re.search(
        r"\b(?:cns|central nervous|neurolog\w*|brain|blood[- ]brain|bbb|"
        r"p[- ]glycoprotein|abc[bc]1|compartment|tissue|exposure|penetration|"
        r"pharmacokinetic|distribution)\b",
        context,
        re.IGNORECASE,
    ):
        queries.extend([
            f"{drug} AND (\"blood-brain barrier\" OR \"brain penetration\" "
            "OR \"brain exposure\" OR CNS OR \"P-glycoprotein\" OR ABCB1)",
            f"{drug} AND (pharmacokinetic OR exposure OR distribution OR "
            "penetration OR compartment)",
        ])
    if re.search(
        r"\b(?:selectiv(?:e|ity)|subtype|subunit|isoform)\b",
        context,
        re.IGNORECASE,
    ):
        queries.extend([
            f"{drug} AND (selectivity OR subtype OR subunit OR isoform)",
            f"{target} AND (selectivity OR subtype OR subunit OR isoform)",
        ])
    return list(dict.fromkeys(queries))


def retrieve_pharmacology_context(
    drug_name: str,
    target_symbol: str,
    hypothesis_context: Any,
    *,
    retmax_per_query: int = 5,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Retrieve generic drug-level pharmacology for disclosure-only review."""
    queries = _pharmacology_context_queries(
        drug_name, target_symbol, hypothesis_context)
    pmids: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for pmid in _esearch(query, retmax_per_query):
            if pmid not in seen:
                seen.add(pmid)
                pmids.append(pmid)
    return queries, _fetch_records(pmids)


def _pharmacology_context_evidence(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract exact context sentences without making applicability claims."""
    evidence: list[dict[str, Any]] = []
    for record in records:
        abstract = str(record.get("abstract") or "")
        sentences = re.split(r"(?<=[.!?])\s+", abstract)
        quotes = [
            sentence.strip() for sentence in sentences
            if len(sentence.strip()) >= 20
            and _PHARMACOLOGY_CONTEXT_QUOTE_MARKERS.search(sentence)
        ]
        if not quotes:
            continue
        evidence.append({
            "pmid": record.get("pmid"),
            "title": record.get("title"),
            "publication_year": record.get("publication_year"),
            "publication_types": record.get("publication_types", []),
            "source_url": record.get("source_url"),
            "quote": " ".join(quotes[:2]),
            "context_type": "generic_drug_pharmacology",
            "disclosure_only": True,
            "exact_disease_use_applicability": False,
        })
    return evidence


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

Allowed exact_use_label values:
- EXPLICIT_LIMITATION: the abstract explicitly says this drug/class is ineffective,
  failed, not recommended, lacks benefit, worsens disease, or has a mechanistic
  limitation for this exact disease/use.
- APPLICABLE_SUPPORT: reports benefit for this exact drug (or an alias) and exact use.
- NOT_APPLICABLE_TO_EXACT_DRUG_USE: related class/mechanistic evidence, wrong
  drug, disease/subtype/use, tool/probe use rather than treatment, or merely
  insufficient evidence. Use this when the abstract is clear but not applicable;
  do not use UNKNOWN just because it is not an efficacy study.
- UNKNOWN/INTEGRITY_FAILED: the supplied abstract cannot support a reliable extraction.
  Reserve this for unreadable, internally contradictory, or genuinely ambiguous
  text where none of the other labels can be assigned safely.

Return one JSON object and nothing else:
{{"exact_use_label":"...","quote":"exact contiguous quote from abstract or empty",
  "disease_match":true|false,"subtype_match":true|false,"use_match":true|false,
  "drug_or_class_match":true|false,
  "intervention_identity":"EXACT_DRUG|DRUG_ALIAS|DRUG_CLASS|NO_INTERVENTION_MATCH",
  "evidence_level":"mechanistic|disease_model|case_report_clinical",
  "reason":"one sentence"}}
The quote must be copied exactly from the abstract. Do not use outside knowledge.

PMID: {record.get('pmid')}
Title: {record.get('title')}
Abstract:
{str(record.get('abstract') or '')[:6000]}"""
    raw, provider = chat_text(
        prompt, max_tokens=500, model_tier=MODEL_TIER_CHEAP,
        operation_label="literature-limitation-record-classification")
    parsed = _extract_json(raw) or {}
    if "label" not in parsed and "exact_use_label" in parsed:
        parsed["label"] = parsed["exact_use_label"]
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

For every supplied PMID return one finding. Allowed exact_use_label values:
EXPLICIT_LIMITATION = explicit ineffective/failed/not recommended/no benefit/
mechanistic limitation for the exact disease and use.
APPLICABLE_SUPPORT = benefit for the exact drug (or an alias) and exact use.
NOT_APPLICABLE_TO_EXACT_DRUG_USE = related class/mechanistic evidence, wrong
 drug, subtype/use, tool/probe use rather than treatment, or only insufficient
 evidence. Use this for a clear but non-applicable abstract.
UNKNOWN/INTEGRITY_FAILED = no reliable extraction can be made.
Reserve this for unreadable, contradictory, or genuinely ambiguous text.

Return one JSON object only:
{{"findings":[{{"pmid":"retrieved PMID","exact_use_label":"...",
"quote":"exact contiguous quote or empty","disease_match":true|false,
"subtype_match":true|false,"use_match":true|false,
"drug_or_class_match":true|false,
"intervention_identity":"EXACT_DRUG|DRUG_ALIAS|DRUG_CLASS|NO_INTERVENTION_MATCH",
"evidence_level":"mechanistic|disease_model|case_report_clinical",
"reason":"one sentence"}}]}}
Use only supplied text. Never omit a PMID and never use outside knowledge.

{supplied}"""
    raw, provider = chat_text(
        prompt, max_tokens=2400, model_tier=MODEL_TIER_CHEAP,
        operation_label="literature-limitation-batch-classification")
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
    for row in findings:
        if "label" not in row and "exact_use_label" in row:
            row["label"] = row["exact_use_label"]
    if any(
        canonical_exact_use_label(row.get("label")) not in _ALLOWED_LABELS
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


def _disease_name_spans(text: str, disease_name: str) -> list[tuple[int, int]]:
    """Character spans where the disease's own name appears in the text.

    Some disease names contain a negative word sitting directly beside the
    drug class they implicate -- "Resistance to thyroid hormone" pairs
    "resistance" with "thyroid hormone". Proximity scoping then fires on the
    disease's own nomenclature in every abstract about it, which is a property
    of the name, not a finding about any candidate.
    """
    name = str(disease_name or "").strip()
    if len(name) < 8:
        return []
    # Orphanet names carry qualifier clauses the literature drops:
    # "Resistance to thyroid hormone DUE TO A MUTATION IN thyroid hormone
    # receptor beta" is written "resistance to thyroid hormone" in every
    # abstract about it. Matching only the full string finds nothing, so the
    # suppression never fires. Try the core name as well.
    variants = [name]
    lowered = name.lower()
    for separator in (" due to ", " caused by ", " secondary to ",
                      " associated with ", " with ", ", "):
        index = lowered.find(separator)
        if index >= 8:
            variants.append(name[:index].strip())
    spans: list[tuple[int, int]] = []
    for variant in variants:
        if len(variant) < 8:
            continue
        for match in re.finditer(re.escape(variant), text, re.IGNORECASE):
            spans.append((match.start(), match.end()))
    return spans


def _support_quote_has_candidate_negative_language(
    quote: Any,
    *,
    drug_name: str,
    drug_class: str,
    aliases: list[str],
    disease_name: str = "",
) -> bool:
    """Reject support only when negative language applies to this intervention.

    Disease descriptions and background/mechanism text routinely contain
    negative-sounding words that have nothing to do with this intervention's
    efficacy -- "ERT-resistant symptoms", "the organ-level dynamics ... remain
    unclear", an engineered drug-resistant channel mutant used as a lab
    control. Every negative-word match gets the same treatment regardless of
    which word matched: only one appearing near this drug/class counts. A
    match elsewhere in the text is disease or methodology background, not a
    finding about the candidate.

    A negative word inside the disease's OWN NAME is discounted for the same
    reason. "Resistance to thyroid hormone" puts "resistance" adjacent to
    "thyroid hormone", so every abstract about that disease looked like a
    negative finding about a thyroid hormone analogue, every record failed
    reconciliation, and the run terminated degraded_unscorable before scoring.
    The disease being named after a resistance phenotype says nothing about
    whether a given drug works in it.
    """
    text = str(quote or "")
    disease_spans = _disease_name_spans(text, disease_name)
    intervention_terms = [
        value for value in (drug_name, drug_class, *aliases)
        if str(value or "").strip()
    ]
    for match in _NEGATIVE_WORDS.finditer(text):
        # A negative word that is part of the disease's own name is
        # nomenclature, not a finding. Skip it before proximity scoring.
        if any(start <= match.start() and match.end() <= end
               for start, end in disease_spans):
            continue
        # Keep the window tight enough not to associate negative language
        # elsewhere in the same abstract with this candidate.
        context = text[max(0, match.start() - 45):match.end() + 45]
        if any(
            _phrase_or_term_match(
                context,
                value,
                minimum_overlap=1 if value == drug_name else 2,
                minimum_fraction=0.8 if value == drug_name else 0.6,
            )
            for value in intervention_terms
            if _terms(value)
        ):
            return True
    return False


def classify_record_validity(
    *,
    valid_label: bool,
    valid_citation: bool,
    label: str,
    requested_label: str,
    recovered_from_unknown: bool,
    exact_match: bool,
    valid_quote: bool,
    explicit_language: bool,
    supportive_language: bool,
    source_has_negative_language: bool,
) -> tuple[bool, bool]:
    """Split "the classifier misbehaved" from "we could not corroborate it".

    Returns ``(classifier_integrity_ok, mechanically_corroborated)``.

    These were one boolean, and every way of failing it counted as a classifier
    integrity failure -- which is what escalates to SEARCH_FAILED and blocks a
    candidate. A Niemann-Pick type C run died there: GPT-5.4 correctly labelled
    three records APPLICABLE_SUPPORT, but they phrase benefit as "leading to
    clinical stabilization", "established treatment" and "the only specific
    drug approved". ``_SUPPORT_WORDS`` matches seven stems and none of those
    appear, so a gap in our own vocabulary was reported as the model returning
    unusable output. The gate failed, the strongest candidate (composite
    0.8315) was excluded, and the run terminated no_eligible_candidate.

    Integrity covers what makes the classifier's OUTPUT untrustworthy: an
    unknown label, an unverifiable citation, an explicit integrity marker, or a
    negative finding hidden behind an IRRELEVANT label.

    It also still covers an uncorroborated NEGATIVE claim, and that asymmetry
    is deliberate. If a record says a drug is ineffective and we cannot
    establish that it refers to our candidate, the deterministic drug matcher
    may simply be wrong, and dropping a real limitation is the dangerous
    direction to err in -- so that remains fatal. An uncorroborated POSITIVE
    claim is merely uncounted: nothing is lost by declining to credit support
    we could not confirm, whereas failing the whole search over one is.
    """
    structurally_usable = (
        valid_label
        and valid_citation
        and label != UNKNOWN_INTEGRITY_FAILED
        # A retrieved record containing explicit negative language cannot
        # silently disappear behind an IRRELEVANT label.
        and not (
            label == NOT_APPLICABLE_TO_EXACT_DRUG_USE
            and source_has_negative_language
        )
    )
    mechanically_corroborated = (
        (
            (
                label == NOT_APPLICABLE_TO_EXACT_DRUG_USE
                and (
                    requested_label == NOT_APPLICABLE_TO_EXACT_DRUG_USE
                    or recovered_from_unknown
                )
            )
            or (exact_match and valid_quote)
            or (
                label == NOT_APPLICABLE_TO_EXACT_DRUG_USE
                and requested_label == APPLICABLE_SUPPORT
                and valid_quote
            )
        )
        and (label != EXPLICIT_LIMITATION or explicit_language)
        and (
            label != APPLICABLE_SUPPORT
            or (supportive_language and not explicit_language)
        )
    )

    # The asymmetry that matters: what was the uncorroborated record CLAIMING?
    #
    # An uncorroborated NEGATIVE claim stays fatal. If a paper says a drug is
    # ineffective and we cannot establish whether it means our candidate, we
    # must not clear the candidate -- the deterministic drug matcher could be
    # wrong, and silently dropping a real limitation is the dangerous error.
    #
    # An uncorroborated POSITIVE claim costs nothing to drop. We simply do not
    # credit it as support. Failing the entire search over one is pure loss,
    # and it is what killed the Niemann-Pick run: three APPLICABLE_SUPPORT
    # records whose phrasing our seven-stem vocabulary did not recognise.
    asserts_something_negative = (
        label in {EXPLICIT_LIMITATION, "CAUTION"}
        or source_has_negative_language
    )
    classifier_integrity_ok = structurally_usable and not (
        asserts_something_negative and not mechanically_corroborated
    )
    return classifier_integrity_ok, mechanically_corroborated


def _mechanical_evidence_level(
    record: dict[str, Any], classification: dict[str, Any],
) -> str:
    supplied = (
        str(classification.get("evidence_level") or "")
        .strip().casefold().replace(" ", "_").replace("/", "_")
    )
    if supplied in EVIDENCE_LEVELS:
        return supplied
    source = " ".join([
        str(record.get("title") or ""),
        str(record.get("abstract") or ""),
        " ".join(str(value) for value in record.get("publication_types", [])),
    ])
    if re.search(
        r"\b(case report|patient|participant|clinical|trial)\b",
        source, re.IGNORECASE,
    ):
        return "case_report_clinical"
    if re.search(
        r"\b(mouse|mice|murine|zebrafish|animal model|disease model|"
        r"cell(?:ular)? model|iPSC)\b",
        source, re.IGNORECASE,
    ):
        return "disease_model"
    return "mechanistic"


def _recover_unknown_label(
    label: str,
    *,
    quote: Any,
    valid_quote: bool,
    classifier_match: bool,
    deterministic_disease: bool,
    deterministic_intervention: bool,
    deterministic_use: bool,
    exact_drug_identity: bool,
) -> str:
    """Recover only classifier labels supported by source text and mechanics.

    A model sometimes emits a valid verbatim extraction while leaving the
    categorical label at UNKNOWN. This is not a quote-integrity failure. The
    recovery is deliberately narrow: applicable support/limitation requires
    exact lexical identity and deterministic disease/use matching; unrelated
    evidence requires an explicit deterministic mismatch. Everything else
    remains unresolved and fail-closed.
    """
    if label != UNKNOWN_INTEGRITY_FAILED:
        return label
    quote_text = str(quote or "")
    if valid_quote and deterministic_disease and deterministic_intervention:
        if (
            deterministic_use
            and exact_drug_identity
            and _NEGATIVE_WORDS.search(quote_text)
        ):
            return EXPLICIT_LIMITATION
        if (
            deterministic_use
            and exact_drug_identity
            and _SUPPORT_WORDS.search(quote_text)
            and not _NEGATIVE_WORDS.search(quote_text)
        ):
            return APPLICABLE_SUPPORT
    if (
        not classifier_match
        and (
            not deterministic_disease
            or not deterministic_intervention
            or not deterministic_use
        )
        and not _NEGATIVE_WORDS.search(quote_text)
        and not _SUPPORT_WORDS.search(quote_text)
    ):
        return NOT_APPLICABLE_TO_EXACT_DRUG_USE
    return label


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
    drug_aliases: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Mechanically validate extractions and determine the gate verdict."""
    aliases = [str(value) for value in (drug_aliases or []) if str(value).strip()]
    alignment_failed = len(records) != len(classifications)
    if not alignment_failed and classifications and all(
        classification.get("pmid") is not None
        for classification in classifications
    ):
        expected = [str(record.get("pmid")) for record in records]
        returned = [str(row.get("pmid")) for row in classifications]
        alignment_failed = (
            len(returned) != len(set(returned)) or set(returned) != set(expected)
        )
        if not alignment_failed:
            by_pmid = {str(row.get("pmid")): row for row in classifications}
            classifications = [by_pmid[pmid] for pmid in expected]

    evidence: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    related_evidence: list[dict[str, Any]] = []
    classifier_integrity_failures = int(alignment_failed)
    for record, classification in zip(records, classifications):
        raw_label = (
            classification.get("exact_use_label")
            if classification.get("exact_use_label") is not None
            else classification.get("label")
        )
        label = canonical_exact_use_label(raw_label)
        classifier_match = all(classification.get(key) is True for key in (
            "disease_match", "use_match", "drug_or_class_match"))
        if classification.get("subtype_match") is False:
            classifier_match = False
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
        source_identity = intervention_identity(
            source_text, drug_name, aliases, drug_class)
        exact_drug_identity = source_identity in {EXACT_DRUG, DRUG_ALIAS}
        deterministic_intervention = (
            deterministic_intervention
            or source_identity != NO_INTERVENTION_MATCH
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
        limitation_match = bool(
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
        explicit_language = (
            _support_quote_has_candidate_negative_language(
                quote,
                drug_name=drug_name,
                drug_class=drug_class,
                aliases=aliases,
                disease_name=disease_name,
            )
            if label == APPLICABLE_SUPPORT else
            bool(_NEGATIVE_WORDS.search(str(quote or "")))
        )
        supportive_language = bool(_SUPPORT_WORDS.search(str(quote or "")))
        # Scoped to the candidate drug/class, not a raw whole-abstract search:
        # background/mechanism abstracts routinely contain words like
        # "resistance" or "unclear" describing disease biology or an unrelated
        # experimental control (e.g. a dihydropyridine-resistant channel mutant
        # used to isolate a current), not a negative finding about this
        # candidate's use. An unscoped search flags nearly every such record,
        # exactly the false-positive pattern _support_quote_has_candidate_
        # negative_language was already built to avoid for the APPLICABLE_
        # SUPPORT path — reused here for the same reason.
        source_has_negative_language = _support_quote_has_candidate_negative_language(
            record.get("abstract"),
            drug_name=drug_name,
            drug_class=drug_class,
            aliases=aliases,
            disease_name=disease_name,
        )
        requested_label = label
        original_label = label
        label = _recover_unknown_label(
            label,
            quote=quote,
            valid_quote=valid_quote,
            classifier_match=classifier_match,
            deterministic_disease=deterministic_disease,
            deterministic_intervention=deterministic_intervention,
            deterministic_use=deterministic_use,
            exact_drug_identity=exact_drug_identity,
        )
        recovered_from_unknown = (
            original_label == UNKNOWN_INTEGRITY_FAILED
            and label != UNKNOWN_INTEGRITY_FAILED
        )
        valid_label = label in _ALLOWED_LABELS
        mechanically_exact_match = bool(
            limitation_match
            or (
                recovered_from_unknown
                and deterministic_disease
                and deterministic_intervention
                and deterministic_use
            )
        )
        exact_support_match = bool(mechanically_exact_match and exact_drug_identity)
        if label == APPLICABLE_SUPPORT and not exact_support_match:
            # Class, disease-model, and adjacent-use support is useful context,
            # but is not evidence for the exact candidate drug/use.
            label = NOT_APPLICABLE_TO_EXACT_DRUG_USE
        exact_match = (
            mechanically_exact_match
            if label in {EXPLICIT_LIMITATION, "CAUTION"}
            else exact_support_match
        )
        # Two separate questions: is the classifier's output usable, and do our
        # own checks corroborate it? Only the first may fail the search. See
        # classify_record_validity for why they were split.
        classifier_integrity_ok, mechanically_corroborated = (
            classify_record_validity(
                valid_label=valid_label,
                valid_citation=valid_citation,
                label=label,
                requested_label=requested_label,
                recovered_from_unknown=recovered_from_unknown,
                exact_match=exact_match,
                valid_quote=valid_quote,
                explicit_language=explicit_language,
                supportive_language=supportive_language,
                source_has_negative_language=source_has_negative_language,
            )
        )
        valid = classifier_integrity_ok and mechanically_corroborated
        evidence_level = _mechanical_evidence_level(record, classification)
        row = {
            "pmid": record.get("pmid"),
            "title": record.get("title"),
            "publication_year": record.get("publication_year"),
            "publication_types": record.get("publication_types", []),
            "source_url": record.get("source_url"),
            # label is retained as a compatibility projection. New consumers
            # must use exact_use_label, whose vocabulary is unambiguous.
            "label": legacy_projection(label) if valid else "INVALID",
            "exact_use_label": label if valid else UNKNOWN_INTEGRITY_FAILED,
            "quote": quote if valid_quote else "",
            "reason": classification.get("reason"),
            "exact_applicability": exact_match,
            "intervention_identity": source_identity,
            "evidence_level": evidence_level,
            "applicability": {
                "disease": deterministic_disease,
                "subtype": (
                    deterministic_disease
                    and classification.get("subtype_match") is not False
                ),
                "use": deterministic_use,
            },
            "classifier_applicability": classifier_match,
            "deterministic_applicability": {
                "disease": deterministic_disease,
                "subtype": (
                    deterministic_disease
                    and classification.get("subtype_match") is not False
                ),
                "drug_or_class": deterministic_intervention,
                "intended_use": deterministic_use,
                "intended_use_terms": sorted(use_specific_terms),
                "intended_use_terms_matched": sorted(use_overlap),
            },
            "mechanically_verified": valid,
            "citation_verified": valid_citation,
            # Kept distinct so a dossier can say WHICH of the two failed:
            # unusable classifier output, or a label we could not corroborate.
            "classifier_integrity_ok": classifier_integrity_ok,
            "mechanically_corroborated": mechanically_corroborated,
        }
        if not valid:
            # integrity_ok / corroborated / supportive_language /
            # explicit_language are logged because every field this line used
            # to print read True on the records that failed, twice, which sent
            # two separate investigations looking in the wrong place.
            _LOGGER.warning(
                "[literature_gate] reconciliation_failed pmid=%s "
                "integrity_ok=%r corroborated=%r supportive_language=%r "
                "explicit_language=%r exact_match=%r "
                "raw_label=%r canonical_label=%r valid_quote=%r "
                "classifier_match=%r deterministic=%r quote=%r abstract=%r",
                pmid,
                classifier_integrity_ok,
                mechanically_corroborated,
                supportive_language,
                explicit_language,
                exact_match,
                raw_label,
                label,
                valid_quote,
                classifier_match,
                {
                    "disease": deterministic_disease,
                    "use": deterministic_use,
                    "drug_or_class": deterministic_intervention,
                    "exact_drug": exact_drug_identity,
                },
                quote,
                record.get("abstract"),
            )
        (evidence if valid else rejected).append(row)
        # ONLY a genuine integrity failure may escalate to SEARCH_FAILED. A
        # record we simply could not corroborate is dropped from evidence and
        # left at that -- unknown, not broken.
        if not classifier_integrity_ok:
            classifier_integrity_failures += 1
        if (
            valid_citation and valid_quote
            and not explicit_language
            and (
                supportive_language
                or (
                    requested_label == APPLICABLE_SUPPORT
                )
            )
            and deterministic_disease
            and (
                source_identity == DRUG_CLASS
                or (exact_drug_identity and not deterministic_use)
            )
        ):
            related_evidence.append({
                **row,
                "exact_use_label": NOT_APPLICABLE_TO_EXACT_DRUG_USE,
                "label": "IRRELEVANT",
                "exact_applicability": False,
                "mechanically_verified": True,
                "disclosure_only": True,
                "efficacy_score_boost": 0,
            })

    explicit = [
        row for row in evidence
        if row["exact_use_label"] == EXPLICIT_LIMITATION
    ]
    support = [
        row for row in evidence
        if row["exact_use_label"] == APPLICABLE_SUPPORT
    ]
    cautions = [row for row in evidence if row["label"] == "CAUTION"]
    authoritative = [
        row for row in explicit
        if _HIGH_AUTHORITY_TYPES.intersection(
            {str(value).casefold() for value in row["publication_types"]})
    ]
    if alignment_failed:
        verdict = VERDICT_FAILED
        blocked = False
        reason = (
            "Classifier rows could not be mechanically aligned one-to-one with "
            "the retrieved PMIDs. The result is unknown and paid validation is "
            "not authorized."
        )
    elif explicit and support:
        verdict = VERDICT_CONFLICTING
        blocked = False
        reason = (
            "Applicable limitation and supportive findings conflict; the candidate "
            "is not automatically blocked. Both findings are recorded below."
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
        "schema_version": "literature-limitation-v4",
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
        "related_support": {
            "disclosure_only": True,
            "efficacy_score_boost": 0,
            "evidence": related_evidence,
            "count": len(related_evidence),
        },
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
    drug_aliases: Optional[list[str]] = None,
    hypothesis_context: Any = None,
    retriever: Callable[..., tuple[list[str], list[dict[str, Any]]]] = retrieve_literature,
    classifier: Callable[..., dict[str, Any]] = classify_record,
) -> dict[str, Any]:
    """Retrieve, classify, mechanically verify, and aggregate limitation evidence."""
    intended = intended_use or f"treatment of {disease_name}"
    cache_key = make_key(
        "literature_limitation_v6_pharmacology_context",
        drug_name, disease_name, target_symbol,
        action_type or "", mechanism_of_action or "", intended,
        sorted(drug_aliases or []),
        hypothesis_context or {},
    )
    if retriever is retrieve_literature and classifier is classify_record:
        cached = get(cache_key)
        if (
            isinstance(cached, dict)
            and cached.get("schema_version") == "literature-limitation-v4"
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
            # Batch classification is efficient, but a long mixed batch can
            # cause the model to emit UNKNOWN for records it could safely mark
            # as unrelated or for which it omitted a required verbatim quote.
            # Retry only those rows with the stricter one-record prompt. Never
            # turn an unresolved row into a favorable result: if recovery also
            # returns UNKNOWN or malformed output, aggregate_findings still
            # fails closed.
            def _needs_record_recovery(row: dict[str, Any]) -> bool:
                label = canonical_exact_use_label(
                    row.get("exact_use_label")
                    if row.get("exact_use_label") is not None
                    else row.get("label")
                )
                if label == UNKNOWN_INTEGRITY_FAILED:
                    return True
                if label in {EXPLICIT_LIMITATION, APPLICABLE_SUPPORT}:
                    return (
                        len(str(row.get("quote") or "").strip()) < 20
                        or any(
                            row.get(key) is not True
                            for key in (
                                "disease_match",
                                "use_match",
                                "drug_or_class_match",
                            )
                        )
                    )
                return False

            for index, (record, classification) in enumerate(
                zip(records, classifications)
            ):
                if not _needs_record_recovery(classification):
                    continue
                try:
                    recovered = classify_record(
                        record,
                        drug_name=drug_name,
                        drug_class=drug_class,
                        disease_name=disease_name,
                        intended_use=intended,
                        target_symbol=target_symbol,
                    )
                except Exception:
                    continue
                recovered_label = canonical_exact_use_label(
                    recovered.get("exact_use_label")
                    if recovered.get("exact_use_label") is not None
                    else recovered.get("label")
                )
                if recovered_label != UNKNOWN_INTEGRITY_FAILED:
                    classifications[index] = recovered
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
            drug_aliases=drug_aliases,
        )
        context_result: dict[str, Any] = {
            "status": "NOT_REQUESTED",
            "queries": [],
            "records_screened": 0,
            "evidence": [],
            "disclosure_only": True,
        }
        if hypothesis_context:
            try:
                context_queries, context_records = retrieve_pharmacology_context(
                    drug_name,
                    target_symbol,
                    hypothesis_context,
                )
                context_result = {
                    "status": "HEALTHY" if context_queries else "NOT_REQUESTED",
                    "queries": context_queries,
                    "records_screened": len(context_records),
                    "evidence": _pharmacology_context_evidence(context_records),
                    "disclosure_only": True,
                    "reason": (
                        "Generic drug-level pharmacology was retrieved for expert "
                        "review; it is not exact disease/use evidence and does not "
                        "alter the limitation gate, ranking, or score."
                    ),
                }
            except Exception as context_exc:
                context_result = {
                    "status": "FAILED",
                    "queries": [],
                    "records_screened": 0,
                    "evidence": [],
                    "disclosure_only": True,
                    "reason": (
                        f"Generic drug-level pharmacology search failed: "
                        f"{context_exc}. Exposure and selectivity remain unknown; "
                        "the exact disease/use gate result is unchanged."
                    ),
                }
        result["pharmacology_context"] = context_result
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
        if (
            retriever is retrieve_literature
            and classifier is classify_record
            and (result.get("pharmacology_context") or {}).get("status")
            != "FAILED"
        ):
            # Authorization evidence is a short-lived source snapshot, not an
            # indefinitely reusable negative finding.
            cache_set(cache_key, result, ttl_days=7)
        return result
    except Exception as exc:
        # Source/classifier failures are not cached: a transient outage must not
        # become a durable "no limitation found" result.
        return {
            "schema_version": "literature-limitation-v4",
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
            "related_support": {
                "disclosure_only": True,
                "efficacy_score_boost": 0,
                "evidence": [],
                "count": 0,
            },
            "pharmacology_context": {
                "status": "FAILED" if hypothesis_context else "NOT_REQUESTED",
                "queries": [],
                "records_screened": 0,
                "evidence": [],
                "disclosure_only": True,
            },
            "post_benchmark_production_gate": True,
        }