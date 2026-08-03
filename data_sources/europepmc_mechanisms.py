"""
Disease-process mechanism discovery via the Europe PMC REST API.

VERIFIED public endpoint:
  https://www.ebi.ac.uk/europepmc/webservices/rest/search

DESIGN — this lane is a *disease-process* discovery source, NOT a drug lookup.
It never queries by drug name. Given a disease it asks the literature which
broad, disease-agnostic mechanism CLASSES are asserted for that disease's
process, and returns the canonical human TARGET(s) implicated by each admitted
class. Concretely it answers: "for this disease, does the primary literature
support inhibitory GABA-A neurotransmission / a voltage-gated sodium channel /
the mitotic spindle / purine-nucleotide antimetabolism / dopaminergic
symptomatic modulation (etc.) being part of the disease process or its
rational point of therapeutic intervention?"

The mechanism ontology is FIXED and broad. It is authored once here, is not
disease-specific, and carries several negative-control classes (complement,
JAK/STAT, lysosomal enzyme) that a correct disease query must NOT surface.
Admission is evidence-gated: a class is only returned when at least
`min_support` distinct Europe PMC records mention BOTH the (normalized) disease
tokens AND a class term in the title/abstract, AND at least one mechanistic
relation keyword. That guards against bare co-mention.

HOLDOUT (leave-one-out) discipline:
  This is a discovery-side source, so it participates in benchmark holdout.
  Before a record can count toward class admission it is screened against
  data_sources.holdout: any record whose normalized title+abstract contains an
  active held-out drug's normalized name is DISCARDED. This prevents a
  retrospective benchmark from "discovering" a mechanism only because the paper
  names the very drug that was held out. The cache key folds in the holdout
  fingerprint (the sorted normalized held-out names) so a redacted run and an
  un-redacted run can never collide in the cache.

Cache discipline (mirrors the other v2 adapters):
  - Only HEALTHY responses ('ok' / 'empty') are cached.
  - 'unavailable' (transient HTTP / timeout / connection error) and
    'parse_failed' (non-JSON or wrong-shape payload) are NEVER cached, so a
    transient outage cannot masquerade as "this disease has no mechanism".

Common return envelope (shared shape across the v2 source adapters):
  {
    "source":  "europepmc",
    "status":  "ok" | "empty" | "unavailable" | "parse_failed",
    "targets": [ {...}, ... ],
    "error":   str | None,
    "release": str | None,     # provider release/version if advertised
  }

No LLM is used anywhere in this module.
"""

import html
import re
import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key
from data_sources import holdout

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Cache key version — bump when the ontology or the admission gate changes so
# rows produced under an older contract can never be served.
_ONTOLOGY_VERSION = "v6"
_TTL_DAYS = 7

# HTTP statuses that mean "temporarily unavailable" — never cached.
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# Bounded page size for the Europe PMC search — we only need enough records to
# clear a small support threshold, and an unbounded page wastes the provider.
_PAGE_SIZE = 100

# Mechanistic relation keywords. A record must contain at least one so a bare
# disease+class co-mention (e.g. a review listing many entities) cannot admit a
# class on its own. Matched as substrings against the normalized-with-spaces
# text so stems ('modulat', 'metabol', 'inhibit') catch their inflections.
_INTERVENTION_KEYWORDS = (
    "therapeut", "treat", "target", "inhibit", "block", "antagon",
    "agon", "modulat", "depend", "vulnerab", "sensitive", "efficacy",
    "effective", "antitumor", "anti tumor", "chemotherap",
)
_CAUSAL_KEYWORDS = (
    "cause", "causal", "pathogenic", "mutation", "variant", "defect",
    "deficien", "dysfunction", "loss of function", "gain of function",
    "linked", "associated", "pathophysiolog", "underlies",
)
_SYMPTOM_KEYWORDS = (
    "symptom", "psychosis", "psychotic", "psychiatric", "neuropsychiatric",
    "agitation", "delusion", "hallucination",
)
_POSITIVE_TREATMENT_KEYWORDS = (
    "benefit", "effective", "efficacy", "improv", "resolv", "response",
    "remission", "control", "alleviat", "reduc", "safe treatment",
)
_ADVERSE_TITLE_MARKERS = (
    "subsequent to", "following treatment", "following therapy",
    "after treatment", "after therapy", "induced by", "triggered by",
    "associated with treatment", "associated with therapy",
)

# ---------------------------------------------------------------------------
# The fixed, broad, disease-AGNOSTIC mechanism ontology.
#
# Each class maps to 1-3 canonical human targets ({symbol, uniprot_id}), a
# mechanism_class label, a therapeutic_role (default 'disease_modifying';
# dopaminergic / neuropsychiatric symptomatic classes are 'symptom_treatment'),
# and the class query terms used both to build the Europe PMC query and to
# gate admission from the returned title/abstract text.
#
# The final three classes are NEGATIVE CONTROLS: broadly real mechanism classes
# that must NOT surface for the positive test diseases, proving the gate keys on
# genuine disease<->class co-assertion rather than always emitting the ontology.
# ---------------------------------------------------------------------------
MECHANISM_ONTOLOGY: list[dict[str, Any]] = [
    {
        "mechanism_class": "inhibitory_neurotransmission_gaba_a",
        "therapeutic_role": "symptom_treatment",
        "evidence_policy": "causal_or_therapeutic",
        "context_terms": ["epilep", "seizure", "neurolog", "neural",
                          "encephalopath"],
        "targets": [
            {"symbol": "GABRA1", "uniprot_id": "P14867"},
            {"symbol": "GABRG2", "uniprot_id": "P18507"},
        ],
        "terms": ["GABA-A receptor", "inhibitory neurotransmission",
                  "GABAergic"],
    },
    {
        "mechanism_class": "voltage_gated_sodium_channel",
        "therapeutic_role": "symptom_treatment",
        "evidence_policy": "causal_or_therapeutic",
        "context_terms": ["epilep", "seizure", "neurolog", "neural",
                          "encephalopath"],
        "targets": [
            {"symbol": "SCN1A", "uniprot_id": "P35498"},
            {"symbol": "SCN2A", "uniprot_id": "Q99250"},
        ],
        "terms": ["voltage-gated sodium channel", "sodium channel",
                  "Nav1.1"],
    },
    {
        "mechanism_class": "microtubule_mitotic_spindle",
        "therapeutic_role": "disease_modifying",
        "evidence_policy": "therapeutic",
        "context_terms": ["cancer", "tumor", "tumour", "sarcoma", "leukem",
                          "malignan", "neoplas"],
        "targets": [
            {"symbol": "TUBB", "uniprot_id": "P07437"},
            {"symbol": "TUBA1A", "uniprot_id": "Q71U36"},
        ],
        "terms": ["microtubule", "mitotic spindle", "tubulin"],
    },
    {
        "mechanism_class": "purine_nucleotide_antimetabolite",
        "therapeutic_role": "disease_modifying",
        "evidence_policy": "therapeutic",
        "targets": [
            {"symbol": "PNP", "uniprot_id": "P00491"},
            {"symbol": "HPRT1", "uniprot_id": "P00492"},
            {"symbol": "IMPDH1", "uniprot_id": "P20839"},
        ],
        "terms": ["purine metabolism", "nucleotide metabolism",
                  "antimetabolite"],
    },
    {
        "mechanism_class": "dopamine_receptor_symptomatic_modulation",
        # Symptomatic, not disease-modifying — see module docstring / spec.
        "therapeutic_role": "symptom_treatment",
        "evidence_policy": "symptom_therapeutic",
        "targets": [
            {"symbol": "DRD2", "uniprot_id": "P14416"},
            {"symbol": "DRD1", "uniprot_id": "P21728"},
        ],
        "terms": ["dopamine receptor", "dopaminergic", "dopamine"],
    },
    {
        "mechanism_class": "antipsychotic_symptom_management",
        "therapeutic_role": "symptom_treatment",
        "evidence_policy": "symptom_therapeutic",
        "targets": [
            {"symbol": "DRD2", "uniprot_id": "P14416"},
            {"symbol": "DRD3", "uniprot_id": "P35462"},
        ],
        "terms": ["antipsychotic", "phenothiazine", "psychosis",
                  "neuropsychiatric"],
    },
    # ---- negative-control classes ---------------------------------------- #
    {
        "mechanism_class": "complement_cascade",
        "therapeutic_role": "disease_modifying",
        "evidence_policy": "therapeutic",
        "targets": [
            {"symbol": "C5", "uniprot_id": "P01031"},
        ],
        "terms": ["complement cascade", "complement activation",
                  "complement C5"],
    },
    {
        "mechanism_class": "jak_stat_signaling",
        "therapeutic_role": "disease_modifying",
        "evidence_policy": "therapeutic",
        "targets": [
            {"symbol": "JAK1", "uniprot_id": "P23458"},
            {"symbol": "STAT3", "uniprot_id": "P40763"},
        ],
        "terms": ["JAK-STAT signaling", "Janus kinase", "STAT signaling"],
    },
    {
        "mechanism_class": "glucocerebrosidase_lysosomal",
        "therapeutic_role": "disease_modifying",
        "evidence_policy": "therapeutic",
        "targets": [
            {"symbol": "GBA", "uniprot_id": "P04062"},
        ],
        "terms": ["glucocerebrosidase", "GBA enzyme", "beta-glucosidase"],
    },
]


class _SourceUnavailable(Exception):
    """Transient failure (HTTP 429/5xx, timeout, connection error). Must render
    the source 'unavailable' and must NOT be cached."""


class _ParseFailed(Exception):
    """The payload was reachable but not the shape the verified contract
    promises (non-JSON, or wrong JSON structure). Rendered 'parse_failed' and
    NOT cached, so a broken provider response cannot poison the cache."""


def _envelope(status: str, targets: list[dict[str, Any]],
              error: Optional[str], release: Optional[str]) -> dict[str, Any]:
    return {
        "source": "europepmc",
        "status": status,
        "targets": targets,
        "error": error,
        "release": release,
    }


def _norm(s: Any) -> str:
    """Collapse to lowercase alphanumerics only (drops punctuation/spaces).

    Used for held-out drug-name containment checks, matching holdout._norm's
    convention so name matching is consistent across the redaction layer.
    """
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _norm_tokens(s: Any) -> list[str]:
    """Lowercase alphanumeric tokens (split on any non-alphanumeric).

    Disease-token matching uses these so 'Lesch-Nyhan syndrome' matches text
    containing 'Lesch Nyhan' regardless of punctuation.
    """
    out: list[str] = []
    cur = []
    for ch in str(s or "").lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _norm_spaced(s: Any) -> str:
    """Lowercase text with punctuation collapsed to single spaces.

    Substring gating (class terms, relation keywords) runs against this so a
    term like 'sodium channel' or a stem like 'modulat' matches naturally.
    """
    return " ".join(_norm_tokens(s))


def _evidence_sentences(title: str, abstract: str) -> list[str]:
    """Normalize title/abstract into sentence-sized evidence units.

    Europe PMC core abstracts often contain HTML section tags.  Requiring all
    admission elements in one sentence prevents a disease mention in a case
    description from combining with an unrelated mechanism elsewhere in a long
    review or cell-line paper.
    """
    raw = f"{title}. {abstract}"
    raw = html.unescape(re.sub(r"<[^>]+>", ". ", raw))
    return [
        _norm_spaced(part)
        for part in re.split(r"(?<=[.!?;])\s+|\n+", raw)
        if _norm_spaced(part)
    ]


def _disease_only_used_as_model(text_spaced: str,
                                disease_tokens: list[str]) -> bool:
    """Reject records where every exact disease phrase is only a cell/model tag."""
    phrase = " ".join(disease_tokens)
    starts = [m.start() for m in re.finditer(re.escape(phrase), text_spaced)]
    if not starts:
        return False
    return all(
        any(marker in text_spaced[max(0, pos - 25):pos + len(phrase) + 35]
            for marker in (" cell", "cells", "cell line", " model"))
        for pos in starts
    )


def _holdout_fingerprint() -> list[str]:
    """Sorted, normalized held-out drug names — the cache-key discriminator."""
    return sorted({_norm(d) for d in holdout.drugs() if _norm(d)})


def _record_is_held_out(text_norm: str) -> bool:
    """True if the record's normalized title+abstract contains any active
    held-out drug's normalized name (substring, catching salt/spacing)."""
    if not holdout.is_active():
        return False
    for drug in holdout.drugs():
        dn = _norm(drug)
        if dn and dn in text_norm:
            return True
    return False


def _get_search_json(query: str, page_size: int) -> Any:
    """GET the Europe PMC search endpoint and return parsed JSON.

    Raises _SourceUnavailable on transient HTTP / timeout / connection error,
    and _ParseFailed on a non-JSON body. Callers map those to the matching
    non-cached envelope.
    """
    params = {
        "query": query,
        "format": "json",
        "resultType": "core",
        "pageSize": page_size,
    }
    try:
        resp = requests.get(BASE_URL, params=params,
                            headers={"Accept": "application/json"}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise _SourceUnavailable(f"request to {BASE_URL} failed: {e}") from e

    if resp.status_code in _TRANSIENT_STATUSES:
        raise _SourceUnavailable(
            f"europepmc returned transient HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise _SourceUnavailable(
            f"europepmc returned unexpected HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as e:
        raise _ParseFailed(f"europepmc returned non-JSON body: {e}") from e


def _extract_records(payload: Any) -> list[dict[str, Any]]:
    """Pull the record list from the Europe PMC search payload.

    Verified contract: {"resultList": {"result": [ {...}, ... ]}}. A payload
    that is not a dict, or whose resultList/result is the wrong type, is
    malformed → _ParseFailed. An absent/empty result list is a legitimate
    healthy 'no hits' (returns [])."""
    if not isinstance(payload, dict):
        raise _ParseFailed("search payload was not a JSON object")
    result_list = payload.get("resultList")
    if result_list is None:
        return []
    if not isinstance(result_list, dict):
        raise _ParseFailed("resultList was not a JSON object")
    records = result_list.get("result", [])
    if records is None:
        return []
    if not isinstance(records, list):
        raise _ParseFailed("resultList.result was not a JSON list")
    for rec in records:
        if not isinstance(rec, dict):
            raise _ParseFailed("a search result row was not an object")
    return records


def _build_query(disease_name: str, class_terms: list[str]) -> str:
    """Europe PMC query: exact disease phrase AND (any class term phrase).

    The disease is quoted so it is matched as an exact phrase; the class terms
    are OR'd together, each quoted. Never references a drug.
    """
    disease_phrase = f'"{disease_name.strip()}"'
    term_clause = " OR ".join(f'"{t}"' for t in class_terms)
    return f'({disease_phrase}) AND ({term_clause})'


def _record_excerpt(rec: dict[str, Any]) -> str:
    """A short human-readable excerpt (abstract preferred, else title)."""
    abstract = rec.get("abstractText")
    if isinstance(abstract, str) and abstract.strip():
        return abstract.strip()[:400]
    title = rec.get("title")
    return str(title).strip()[:400] if title else ""


def _admit_class(cls: dict[str, Any], records: list[dict[str, Any]],
                 disease_tokens: list[str], query: str,
                 min_support: int) -> Optional[dict[str, Any]]:
    """Decide whether a mechanism class clears admission for this disease.

    A record supports the class only when its normalized title+abstract:
      1. is NOT held out (no active held-out drug name present),
      2. contains every normalized disease token,
      3. contains at least one of the class's terms, and
      4. contains at least one mechanistic relation keyword.
    The class is admitted when >= min_support DISTINCT supporting records exist
    (distinct by PMID/PMCID/id). Returns the normalized target rows with full
    provenance, or None if not admitted.
    """
    term_norms = [_norm_spaced(t) for t in cls["terms"]]
    supporting: dict[str, dict[str, Any]] = {}

    for rec in records:
        title = rec.get("title") or ""
        abstract = rec.get("abstractText") or ""
        combined = f"{title}. {abstract}"
        text_norm = _norm(combined)          # for held-out containment

        # (1) holdout redaction — discard before it can count as evidence.
        if _record_is_held_out(text_norm):
            continue

        text_spaced = _norm_spaced(combined)
        if not all(tok in text_spaced.split() for tok in disease_tokens):
            continue
        if not any(tn and tn in text_spaced for tn in term_norms):
            continue
        context_terms = cls.get("context_terms") or []
        if context_terms and not any(term in text_spaced
                                     for term in context_terms):
            continue
        if _disease_only_used_as_model(text_spaced, disease_tokens):
            continue

        policy = cls.get("evidence_policy", "therapeutic")
        sentences = _evidence_sentences(title, abstract)
        class_sentences = [
            sentence for sentence in sentences
            if any(tn and tn in sentence for tn in term_norms)
        ]
        has_intervention = any(
            any(kw in sentence for kw in _INTERVENTION_KEYWORDS)
            for sentence in class_sentences
        )
        has_causal = any(
            any(kw in sentence for kw in _CAUSAL_KEYWORDS)
            for sentence in class_sentences
        )
        has_symptom_intervention = any(
            any(sym in sentence for sym in _SYMPTOM_KEYWORDS)
            and any(kw in sentence for kw in _INTERVENTION_KEYWORDS)
            and any(outcome in sentence
                    for outcome in _POSITIVE_TREATMENT_KEYWORDS)
            for sentence in class_sentences
        )
        adverse_title = any(
            marker in _norm_spaced(title)
            for marker in _ADVERSE_TITLE_MARKERS
        )
        qualifies = (
            (policy == "therapeutic" and has_intervention
             and not adverse_title)
            or (policy == "causal_or_therapeutic"
                and (has_causal or has_intervention))
            or (policy == "symptom_therapeutic"
                and has_symptom_intervention)
        )
        if not qualifies:
            continue

        # Preserve the tightest sentence containing a class term for audit,
        # while the fixed policy is evaluated across the citable record.
        qualifying_sentences = [
            sentence for sentence in class_sentences
            if (
                (policy == "therapeutic"
                 and any(kw in sentence for kw in _INTERVENTION_KEYWORDS)
                 and not adverse_title)
                or (policy == "causal_or_therapeutic"
                    and (any(kw in sentence for kw in _CAUSAL_KEYWORDS)
                         or any(kw in sentence
                                for kw in _INTERVENTION_KEYWORDS)))
                or (policy == "symptom_therapeutic"
                    and any(sym in sentence for sym in _SYMPTOM_KEYWORDS)
                    and any(kw in sentence for kw in _INTERVENTION_KEYWORDS)
                    and any(outcome in sentence
                            for outcome in _POSITIVE_TREATMENT_KEYWORDS))
            )
        ]
        evidence_sentence = (
            qualifying_sentences[0] if qualifying_sentences
            else text_spaced[:800]
        )

        rec_id = rec.get("pmid") or rec.get("pmcid")
        if not rec_id:
            # Admission evidence must have a stable, citable publication ID.
            continue
        if rec_id in supporting:
            continue
        supporting[str(rec_id)] = {
            "pmid": rec.get("pmid"),
            "pmcid": rec.get("pmcid"),
            "title": rec.get("title"),
            "excerpt": _record_excerpt(rec),
            "evidence_sentence": evidence_sentence,
        }

    if len(supporting) < min_support:
        return None

    support_records = list(supporting.values())
    admitted: list[dict[str, Any]] = []
    for tgt in cls["targets"]:
        admitted.append({
            "symbol": tgt["symbol"],
            "uniprot_id": tgt["uniprot_id"],
            "mechanism_class": cls["mechanism_class"],
            "therapeutic_role": cls["therapeutic_role"],
            "target_priority": cls["targets"].index(tgt),
            "class_priority": MECHANISM_ONTOLOGY.index(cls),
            "evidence_policy": cls.get("evidence_policy", "therapeutic"),
            "class_terms": list(cls["terms"]),
            "support": len(support_records),
            "supporting_records": support_records,
            # provenance / lineage
            "query": query,
            "source": "europepmc",
            "ontology_version": _ONTOLOGY_VERSION,
        })
    return {"targets": admitted}


def discover_disease_process_targets(disease_name: str,
                                     min_support: int = 1) -> dict[str, Any]:
    """Disease-only mechanism discovery over the fixed ontology.

    For each mechanism class, queries Europe PMC for the exact disease phrase
    AND the class's terms, screens the returned records (holdout redaction +
    disease-token + class-term + relation-keyword gate), and admits the class's
    canonical targets when >= min_support distinct records support it.

    Never queries by drug name. Returns the common envelope:
      {source, status, targets, error, release}

    status:
      "ok"           — at least one class admitted.
      "empty"        — reachable, healthy, but no class cleared the gate
                       (cacheable — a genuine negative for this disease).
      "unavailable"  — transient HTTP / timeout / connection error (NOT cached).
      "parse_failed" — payload reachable but malformed (NOT cached).
    """
    disease_name = (disease_name or "").strip()
    disease_tokens = _norm_tokens(disease_name)

    cache_key = make_key(
        f"europepmc_discover_disease_process_{_ONTOLOGY_VERSION}",
        disease_name.lower(),
        min_support,
        _holdout_fingerprint(),
    )
    cached = get(cache_key)
    if cached is not None:
        return cached

    if not disease_tokens:
        # No disease to query — a healthy empty (nothing to cache poison here).
        result = _envelope("empty", [], None, None)
        cache_set(cache_key, result, ttl_days=_TTL_DAYS)
        return result

    all_targets: list[dict[str, Any]] = []
    try:
        for cls in MECHANISM_ONTOLOGY:
            query = _build_query(disease_name, cls["terms"])
            payload = _get_search_json(query, _PAGE_SIZE)
            records = _extract_records(payload)
            admitted = _admit_class(cls, records, disease_tokens, query,
                                    min_support)
            if admitted:
                all_targets.extend(admitted["targets"])
    except _SourceUnavailable as e:
        # Transient — never cache; a cached empty would look like "no mechanism"
        # for the whole TTL.
        print(f"[europepmc] WARNING: source unavailable for "
              f"'{disease_name}': {e}")
        return _envelope("unavailable", [], str(e), None)
    except _ParseFailed as e:
        print(f"[europepmc] WARNING: malformed payload for "
              f"'{disease_name}': {e}")
        return _envelope("parse_failed", [], str(e), None)

    status = "ok" if all_targets else "empty"
    result = _envelope(status, all_targets, None, None)
    cache_set(cache_key, result, ttl_days=_TTL_DAYS)
    return result
