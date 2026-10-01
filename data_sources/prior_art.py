"""Bounded prior-art check: has this exact drug been proposed for this disease?

WHY THIS EXISTS
---------------
The pipeline had no prior-art signal at all. Its only novelty-adjacent numbers
were ``prior_trial_count`` (ClinicalTrials.gov, exact drug+disease) and
``literature_trial_count`` -- both of which count TRIALS. A preclinical paper
proposing exactly this drug for exactly this disease scores zero on both.

That gap produced a live false novelty claim. A blind run on resistance to
thyroid hormone beta promoted RESMETIROM with prior_trial_count=0 and a clear
literature gate, while Mitsutani et al., Biol Pharm Bull 2025;48(4):463-474
("Resmetirom Is an Effective Thyromimetics for the Chemical Rescue of Thyroid
Hormone Receptor Mutants", PMID 40301009) had already generated 57 mutant TRbetas
to test that precise hypothesis and worked out which mutations respond. The
pipeline could say the pair was plausible; it could not say it was unclaimed.

WHAT THIS CAN AND CANNOT DO
---------------------------
Prior art is unbounded. It lives in journals, preprints, patents, conference
abstracts, theses, grant records and registries, and no query closes that set.
This module therefore does NOT certify novelty and never emits a "novel"
verdict. It answers one bounded question: does a literature record exist in
which this drug and this disease co-occur?

Europe PMC is the single highest-yield index for that question -- it covers
PubMed and PMC, preprint servers, patent abstracts and agricultural/biomedical
conference abstracts behind one API -- and ClinicalTrials.gov is already
covered by the reviewer's ``trial_audit``. Together they catch the failure mode
that actually matters: a published record a reviewer would find in one search.

VERDICT SEMANTICS (fail-closed, consistent with the literature gate)
--------------------------------------------------------------------
``PRIOR_ART_FOUND``        a record was retrieved AND mechanically confirmed.
``NO_PRIOR_ART_FOUND``     the bounded search returned nothing. This is NOT
                           evidence of novelty; it is the absence of a hit in
                           one index.
``SEARCH_FAILED``          the provider errored. Unknown is not a negative --
                           it must never be read as either novelty or prior art.
``NOT_ASSESSED``           holdout is active, or inputs were insufficient.

The search engine's own relevance ranking is not trusted. Every hit is
re-verified in code by confirming that a drug term and a disease term literally
appear in the retrieved title/abstract.
"""

import os
import re
import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key
from data_sources import holdout

BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

SCHEMA_VERSION = "prior-art-v1"

VERDICT_FOUND = "PRIOR_ART_FOUND"
#: Exactly one abstract-level record, with no title-level record. One passing
#: mention is not a proposal. "Biochemistry, Cyclic GMP" names sildenafil and
#: achondroplasia 261 characters apart and advances neither for the other, so
#: proximity alone cannot separate it from a real hypothesis. This verdict
#: discloses the record for a human to judge without excluding the candidate.
VERDICT_UNCONFIRMED = "PRIOR_ART_UNCONFIRMED"
VERDICT_NONE = "NO_PRIOR_ART_FOUND"
VERDICT_FAILED = "SEARCH_FAILED"
VERDICT_NOT_ASSESSED = "NOT_ASSESSED"

STRENGTH_TITLE = "title_co_occurrence"
STRENGTH_PROXIMAL = "abstract_proximal_co_occurrence"
STRENGTH_DISTANT = "abstract_distant_co_occurrence"

#: Characters between a drug term and a disease term in an abstract, below
#: which the two are plausibly being discussed together.
#:
#: Bare co-occurrence in an abstract is not prior art. A broad review can name
#: a drug in one paragraph and a disease in another with no claim relating
#: them: "Biochemistry, Cyclic GMP" mentions both sildenafil and achondroplasia
#: and proposes neither for the other. Treating that as prior art would repeat
#: the literature gate's own failure -- a weak signal read as decisive -- and
#: would suppress good candidates. Distant hits are therefore disclosed, not
#: counted.
#:
#: The bound is set from the record this module exists for: PMID 40301009 names
#: "resistance to thyroid hormone" and "resmetirom" about 200 characters apart,
#: so the window must comfortably exceed that without spanning a whole abstract.
_PROXIMITY_WINDOW = 400

# v3: the result shape changed twice during construction -- first splitting
# hits by proximity, then adding the corroboration rule and
# requires_human_review. An older entry would replay a verdict computed under
# a contract this code no longer implements.
_CACHE_VERSION = "prior_art_v3_corroboration"
_TTL_DAYS = 7
_PAGE_SIZE = 25
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

#: Orphanet writes qualifier clauses that the literature drops. "Resistance to
#: thyroid hormone DUE TO A MUTATION IN thyroid hormone receptor beta" appears
#: in every abstract as "resistance to thyroid hormone". Searching only the
#: full string finds nothing, which would read as an absence of prior art.
_QUALIFIER_SEPARATORS = (
    " due to ", " caused by ", " secondary to ", " associated with ",
    " with ", ", ",
)

#: A term shorter than this matches too much to be evidence of anything. Disease
#: abbreviations ("RTH", "MS", "ALS") are deliberately excluded: they collide
#: across fields and would manufacture prior art.
_MIN_DISEASE_TERM = 8
_MIN_DRUG_TERM = 4


#: Set to off/0/false/no to stop prior art EXCLUDING a candidate. The search
#: still runs and is still reported either way.
#:
#: Discovery and validation want opposite things from the same fact. Hunting a
#: new pair, a published record is disqualifying. Auditing a hypothesis someone
#: already has -- the audit/triage front door, or reproducing a known pair to
#: show the machine reaches it -- prior art is the POINT, and a gate that
#: excluded it would refuse to score exactly the cases you are trying to
#: validate against. So the finding is always recorded; only whether it blocks
#: is configurable.
_GATE_ENV = "AGENTBIO_PRIOR_ART_GATE"
_GATE_OFF_VALUES = {"off", "0", "false", "no", "disabled"}


def gate_is_enabled() -> bool:
    """Whether a prior-art finding should EXCLUDE a candidate (default: yes)."""
    value = str(os.getenv(_GATE_ENV, "on")).strip().casefold()
    return value not in _GATE_OFF_VALUES


class _SourceUnavailable(RuntimeError):
    """Transient provider failure. Never cached, never a negative finding."""


def _normalize(text: Any) -> str:
    """Collapse all whitespace so line-wrapped phrases still match.

    This is not cosmetic. The literature gate's disease-name suppression missed
    a title that PubMed had wrapped as "resistance to thyroid\\nhormone", the
    unsuppressed match flipped a candidate's verdict, and that decided which
    drug the dossier promoted. Any phrase matching over fetched text has to
    normalize whitespace before comparing.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _contains_term(haystack: str, term: str) -> bool:
    """Whole-token containment of ``term`` in already-normalized ``haystack``.

    Lookarounds rather than \\b: drug identifiers carry hyphens and digits
    (MGL-3196), where \\b would match inside the token.
    """
    if not term:
        return False
    pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def disease_terms(disease_name: str, extra_aliases: Any = None) -> list[str]:
    """Search terms for a disease: the full name plus its qualifier-stripped core."""
    name = _normalize(disease_name)
    terms: list[str] = []
    if len(name) >= _MIN_DISEASE_TERM:
        terms.append(name)
        lowered = name.lower()
        for separator in _QUALIFIER_SEPARATORS:
            index = lowered.find(separator)
            if index >= _MIN_DISEASE_TERM:
                core = name[:index].strip()
                if len(core) >= _MIN_DISEASE_TERM:
                    terms.append(core)
    for alias in (extra_aliases or []):
        alias_text = _normalize(alias)
        if len(alias_text) >= _MIN_DISEASE_TERM:
            terms.append(alias_text)
    # Deduplicate case-insensitively, preserving order (longest-first matters
    # only for reporting, not for matching).
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def drug_terms(drug_name: str, aliases: Any = None) -> list[str]:
    """Search terms for a drug: its name plus any development codes/synonyms."""
    terms: list[str] = []
    for value in [drug_name, *(aliases or [])]:
        text = _normalize(value)
        if len(text) >= _MIN_DRUG_TERM:
            terms.append(text)
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def build_query(drugs: list[str], diseases: list[str]) -> str:
    """Europe PMC query requiring a drug term AND a disease term in title/abstract."""
    def clause(terms: list[str]) -> str:
        return " OR ".join(f'TITLE_ABS:"{term}"' for term in terms)
    return f"({clause(drugs)}) AND ({clause(diseases)})"


def _search(query: str, page_size: int = _PAGE_SIZE) -> dict[str, Any]:
    """GET the Europe PMC search endpoint. Raises _SourceUnavailable on failure."""
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
        payload = resp.json()
    except ValueError as e:
        raise _SourceUnavailable(f"europepmc returned non-JSON body: {e}") from e
    if not isinstance(payload, dict):
        raise _SourceUnavailable("search payload was not a JSON object")
    return payload


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result_list = payload.get("resultList")
    if result_list is None:
        return []
    if not isinstance(result_list, dict):
        raise _SourceUnavailable("resultList was not a JSON object")
    records = result_list.get("result", [])
    if not isinstance(records, list):
        raise _SourceUnavailable("result was not a JSON array")
    return [r for r in records if isinstance(r, dict)]


def _spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for term in terms:
        if not term:
            continue
        pattern = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            found.append((match.start(), match.end()))
    return found


def min_term_distance(
    text: str, drugs: list[str], diseases: list[str],
) -> Optional[int]:
    """Smallest gap between any drug mention and any disease mention.

    Returns None when either side is absent. Zero when the mentions overlap.
    """
    drug_spans = _spans(text, drugs)
    disease_spans = _spans(text, diseases)
    if not drug_spans or not disease_spans:
        return None
    best: Optional[int] = None
    for d_start, d_end in drug_spans:
        for s_start, s_end in disease_spans:
            if d_start < s_end and s_start < d_end:
                return 0
            gap = s_start - d_end if s_start >= d_end else d_start - s_end
            if best is None or gap < best:
                best = gap
    return best


def confirm_hit(
    record: dict[str, Any],
    drugs: list[str],
    diseases: list[str],
) -> Optional[dict[str, Any]]:
    """Re-verify one search hit in code, or return None.

    Europe PMC relevance is not evidence. A record counts only if a drug term
    and a disease term both literally appear in its title or abstract. Title
    co-occurrence is reported as the stronger signal, but abstract-level
    co-occurrence still counts -- the paper that motivated this module names
    the receptor in its title and the disease only in its abstract.
    """
    title = _normalize(record.get("title"))
    abstract = _normalize(record.get("abstractText"))
    combined = f"{title} {abstract}".strip()
    if not combined:
        return None

    matched_drug = next((t for t in drugs if _contains_term(combined, t)), None)
    matched_disease = next(
        (t for t in diseases if _contains_term(combined, t)), None)
    if not matched_drug or not matched_disease:
        return None

    in_title = (_contains_term(title, matched_drug)
                and _contains_term(title, matched_disease))
    distance = min_term_distance(combined, drugs, diseases)
    if in_title:
        strength = STRENGTH_TITLE
    elif distance is not None and distance <= _PROXIMITY_WINDOW:
        strength = STRENGTH_PROXIMAL
    else:
        strength = STRENGTH_DISTANT
    return {
        "pmid": record.get("pmid") or record.get("id"),
        "doi": record.get("doi"),
        "title": title,
        "journal": record.get("journalTitle"),
        "year": record.get("pubYear"),
        "source": record.get("source"),
        "strength": strength,
        "term_distance_chars": distance,
        "matched_drug_term": matched_drug,
        "matched_disease_term": matched_disease,
    }


def _envelope(verdict: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "found": verdict == VERDICT_FOUND,
        # An unconfirmed single mention is not prior art, so it does not block
        # the candidate -- but it is surfaced for a human to look at.
        "gate_cleared": verdict in (VERDICT_NONE, VERDICT_UNCONFIRMED),
        "requires_human_review": verdict == VERDICT_UNCONFIRMED,
        "source_status": "HEALTHY" if verdict in (
            VERDICT_FOUND, VERDICT_NONE, VERDICT_UNCONFIRMED)
            else "UNAVAILABLE",
        "searched_sources": ["Europe PMC"],
        "hits": [],
        "hit_count": 0,
        # Records where the two terms co-occur but far apart. Disclosed for a
        # human to judge; deliberately NOT counted as prior art.
        "incidental_hits": [],
        "incidental_hit_count": 0,
        "query": "",
        "reason": reason,
    }
    payload.update(extra)
    return payload


def check_prior_art(
    drug_name: str,
    disease_name: str,
    *,
    drug_aliases: Any = None,
    disease_aliases: Any = None,
    max_hits: int = _PAGE_SIZE,
) -> dict[str, Any]:
    """Search for published records pairing this drug with this disease.

    Returns an envelope whose ``verdict`` is one of the module constants. A
    ``NO_PRIOR_ART_FOUND`` result is the absence of a hit in one bounded index,
    never a novelty certification; ``SEARCH_FAILED`` is unknown and must not be
    read as either outcome.
    """
    if holdout.is_active():
        return _envelope(
            VERDICT_NOT_ASSESSED,
            "Holdout is active; prior-art search is not run under frozen "
            "study semantics.")

    drugs = drug_terms(drug_name, drug_aliases)
    diseases = disease_terms(disease_name, disease_aliases)
    if not drugs or not diseases:
        return _envelope(
            VERDICT_NOT_ASSESSED,
            "Drug or disease name was too short or absent to search on "
            "without matching unrelated records.")

    query = build_query(drugs, diseases)
    cache_key = make_key(_CACHE_VERSION, query, max_hits)
    cached = get(cache_key)
    if cached is not None:
        return cached

    try:
        payload = _search(query, page_size=max_hits)
        records = _records(payload)
    except _SourceUnavailable as e:
        # Never cached: an outage must not harden into a permanent "no prior
        # art", which would read as novelty on every later run.
        return _envelope(
            VERDICT_FAILED,
            f"Europe PMC was unavailable, so prior art is unknown: {e}",
            query=query)

    confirmed = [h for h in (confirm_hit(r, drugs, diseases) for r in records) if h]
    hits = [h for h in confirmed if h["strength"] != STRENGTH_DISTANT]
    incidental = [h for h in confirmed if h["strength"] == STRENGTH_DISTANT]

    # Same standard the literature gate already applies to a limitation claim:
    # one title-level record, or two independent abstract-level records. A lone
    # abstract mention is disclosed rather than acted on.
    title_hits = [h for h in hits if h["strength"] == STRENGTH_TITLE]
    distinct_ids = {str(h.get("pmid") or h.get("doi") or h["title"]) for h in hits}
    qualifies = bool(title_hits) or len(distinct_ids) >= 2

    if hits and not qualifies:
        result = _envelope(
            VERDICT_UNCONFIRMED,
            f"One Europe PMC record mentions this drug near this disease "
            f"({hits[0]['title']}), with no title-level record and no "
            "independent corroboration. A single passing mention is not a "
            "proposal; this is disclosed for human review, not counted as "
            "prior art.",
            hits=hits, hit_count=len(hits),
            incidental_hits=incidental, incidental_hit_count=len(incidental),
            query=query)
        cache_set(cache_key, result, ttl_days=_TTL_DAYS)
        return result

    if hits:
        titles = "; ".join(
            f"{h['title']} ({h.get('year') or 'n.d.'})" for h in hits[:3])
        result = _envelope(
            VERDICT_FOUND,
            f"{len(hits)} Europe PMC record(s) discuss this drug and this "
            f"disease together in title/abstract: {titles}",
            hits=hits, hit_count=len(hits),
            incidental_hits=incidental, incidental_hit_count=len(incidental),
            query=query)
    else:
        note = ""
        if incidental:
            note = (f" {len(incidental)} record(s) mention both terms far "
                    "apart without relating them; these are disclosed, not "
                    "counted as prior art.")
        result = _envelope(
            VERDICT_NONE,
            "No Europe PMC record discussed this drug together with this "
            "disease in a bounded title/abstract search. This is not evidence "
            "of novelty; prior art may exist in sources outside this index."
            + note,
            incidental_hits=incidental, incidental_hit_count=len(incidental),
            query=query)

    cache_set(cache_key, result, ttl_days=_TTL_DAYS)
    return result
