"""Is anyone already working on this disease, and did they recently start again?

WHY THIS EXISTS
---------------
Six flagship hunts failed, and the post-mortems all converge on one missing
measurement. Every gate this repo owns asks about a DRUG: is this molecule
published for this disease, can it reach the tissue, is its association
measured. Nothing asked about the DISEASE: is this a space with people already
in it.

Myotonic dystrophy type 1 is the case that forced the issue. The pair-level
gates were clean -- lacosamide had zero registered trials in myotonia, and the
prior-art search found nothing. On those numbers it looked unclaimed. The
disease-level numbers said the opposite:

    myotonic dystrophy, registered trials                        142
    myotonic dystrophy, currently recruiting                      41
    NCT06523400  mexiletine PR, PHASE 3, RECRUITING, Lupin Ltd.    1

A pharmaceutical company is running a phase 3 on the adjacent sodium-channel
blocker for the identical symptom. Whatever is true about lacosamide, the
claim "if AgentBio did not exist these patients would have suffered" cannot
survive a funded clinical team standing on the same ground. That is a
disease-level fact, it costs three HTTP calls, and it was knowable before any
run was spent. It killed DM1 after the spend instead of before it.

WHAT IT MEASURES
----------------
Two independent axes, because they fail differently:

  TRIALS       ClinicalTrials.gov -- is there a clinical program. Counts
               interventional studies, those currently enrolling, those
               started recently, and those with an industry lead sponsor.
  LITERATURE   Europe PMC -- is there a research program. Counts all records
               and recent records, so a dormant literature can be told from a
               reviving one.

A disease with no trials and a flat, old literature is dormant. A disease with
no trials but a publication count climbing in the last three years has renewed
interest: someone is building toward something, and arriving a year ahead of
them is not the same as arriving where nobody is.

SYNONYMS ARE NOT OPTIONAL
-------------------------
The Orphanet label is frequently not the name a registry or a journal uses,
and searching it verbatim is how this codebase produced its worst false
negative: "Steinert myotonic dystrophy" returned 0 Europe PMC hits for a drug
with a published randomised controlled trial. A zero obtained from the wrong
string is indistinguishable from dormancy and far more dangerous, because
dormancy is the thing being certified. Every count here is the MAXIMUM across
the disease's canonical name and exact synonyms -- the name that finds the
most is the name that tells the truth.

FAIL-CLOSED
-----------
A failed query is never zero. ``assessed`` is False and the verdict is
``ATTENTION_UNKNOWN``, which does not qualify a disease. This module exists
because an unknown was being read as a clean result; it must not reintroduce
the same error one layer up.
"""

from __future__ import annotations

import datetime as _dt
import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key
from data_sources import provider_request_policy
from data_sources.prior_art import disease_synonyms

CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
EPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

SCHEMA_VERSION = "research-attention-v1"
_CACHE_VERSION = "research_attention_v1"
_TTL_DAYS = 7
_TIMEOUT_SECONDS = 30

#: Statuses meaning a trial is open or still running. NOT_YET_RECRUITING is
#: included deliberately: a registered-but-unopened study is a funded program
#: with a protocol, which is exactly the condition that disqualifies a space.
OPEN_STATUSES = (
    "NOT_YET_RECRUITING",
    "RECRUITING",
    "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING",
)

#: How far back "recent" reaches, for both trial starts and publications.
RECENT_YEARS = 3

VERDICT_DORMANT = "DORMANT"
VERDICT_ACTIVE_TRIALS = "ACTIVE_CLINICAL_PROGRAM"
VERDICT_RENEWED_INTEREST = "RENEWED_RESEARCH_INTEREST"
VERDICT_UNKNOWN = "ATTENTION_UNKNOWN"

#: The dormancy bar. Set to zero on the user's call: a single registered
#: interventional trial means somebody has already taken a hypothesis in this
#: disease as far as a protocol, an ethics board and a sponsor, and "nobody is
#: working on this" is then false regardless of what the drug-level gates say.
#:
#: This is strict by design and will reject most of the universe. That is the
#: point -- the previous selection method was recall, which selects for
#: diseases memorable enough to be well funded, and 142 trials is what "I have
#: heard of this disease" turned out to mean. The surviving set is reported
#: with its raw counts so the bar can be moved against observed distribution
#: rather than guessed at again.
MAX_TRIALS_FOR_DORMANT = 0

#: Renewed interest, measured on the literature when the registry is empty.
#: A disease can have no trials and still be actively worked: a group
#: publishing toward a first-in-human has a protocol in preparation, and
#: arriving twelve months ahead of them does not satisfy the conditional this
#: screen exists to protect. Both conditions must hold -- a share alone fires
#: on a disease with four lifetime papers, and a count alone fires on a large
#: old literature ticking over.
#:
#: The share is derived, not picked. Europe PMC indexes a rare disease's
#: literature over roughly thirty years, so a FLAT publication rate puts about
#: 3/30 = 10% of records inside a three-year window. 25% is ~2.5x that
#: baseline, i.e. a rate that has visibly turned upward rather than one that
#: happens to be noisy. Observed: Hallermann-Streiff syndrome, a genuinely
#: quiet disease, sits at 41/327 = 12.5% -- just above the flat baseline and
#: well under the bar, which is the behaviour wanted.
RENEWED_RECENT_FLOOR = 10
RENEWED_RECENT_SHARE = 0.25


def _recent_cutoff_year() -> int:
    return _dt.date.today().year - RECENT_YEARS


def _ctgov_count(params: dict[str, str]) -> Optional[int]:
    """Total matching studies, or None if the registry did not answer.

    ``countTotal`` with the smallest possible page makes this one cheap call
    per question instead of paging the result set.
    """
    query = dict(params)
    query.update({"countTotal": "true", "pageSize": "1", "format": "json"})
    try:
        resp = provider_request_policy.request(
            "clinicaltrials", requests.get, CTGOV_URL,
            params=query, timeout=_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 — unknown count, never a zero
        return None
    if getattr(resp, "status_code", None) != 200:
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    total = payload.get("totalCount")
    return int(total) if isinstance(total, int) else None


def _epmc_count(query: str) -> Optional[int]:
    try:
        resp = provider_request_policy.request(
            "europepmc", requests.get, EPMC_URL,
            params={"query": query, "format": "json", "pageSize": 1},
            timeout=_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        return None
    if getattr(resp, "status_code", None) != 200:
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    count = payload.get("hitCount")
    return int(count) if isinstance(count, int) else None


def search_names(disease_name: str) -> list[str]:
    """The disease's own label plus the names Open Targets says papers use."""
    name = str(disease_name or "").strip()
    names = [name] if name else []
    for synonym in disease_synonyms(name):
        if synonym and synonym.casefold() not in {n.casefold() for n in names}:
            names.append(synonym)
    return names


def _max_over_names(
    names: list[str], fetch: Any,
) -> tuple[Optional[int], bool]:
    """Largest count any name returned, and whether every name failed.

    Taking the maximum is the only safe reduction. A name that finds nothing
    may simply be the wrong string, and averaging or summing lets that wrong
    string drag a real count toward zero -- which is the direction that
    manufactures false dormancy.
    """
    best: Optional[int] = None
    for name in names:
        value = fetch(name)
        if value is None:
            continue
        best = value if best is None else max(best, value)
    return best, best is not None


def trial_attention(disease_name: str) -> dict[str, Any]:
    """Registered clinical activity in this disease, across its names."""
    names = search_names(disease_name)
    if not names:
        return {"assessed": False, "reason": "No disease name supplied."}

    def interventional(name: str) -> Optional[int]:
        return _ctgov_count({
            "query.cond": name,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL"})

    def open_now(name: str) -> Optional[int]:
        return _ctgov_count({
            "query.cond": name,
            "filter.advanced": "AREA[StudyType]INTERVENTIONAL",
            "filter.overallStatus": "|".join(OPEN_STATUSES)})

    def recent(name: str) -> Optional[int]:
        return _ctgov_count({
            "query.cond": name,
            "filter.advanced":
                "AREA[StudyType]INTERVENTIONAL AND "
                f"AREA[StartDate]RANGE[{_recent_cutoff_year()}-01-01,MAX]"})

    def industry(name: str) -> Optional[int]:
        return _ctgov_count({
            "query.cond": name,
            "filter.advanced":
                "AREA[StudyType]INTERVENTIONAL AND "
                "AREA[LeadSponsorClass]INDUSTRY"})

    n_total, ok = _max_over_names(names, interventional)
    if not ok:
        return {
            "assessed": False,
            "names_searched": names,
            "reason": ("ClinicalTrials.gov did not answer for any name. "
                       "That is not a trial count of zero."),
        }
    n_open, _ = _max_over_names(names, open_now)
    n_recent, _ = _max_over_names(names, recent)
    n_industry, _ = _max_over_names(names, industry)
    return {
        "assessed": True,
        "names_searched": names,
        "n_interventional": n_total,
        "n_open": n_open,
        "n_recent_start": n_recent,
        "n_industry_sponsored": n_industry,
        "recent_since_year": _recent_cutoff_year(),
    }


def literature_attention(disease_name: str) -> dict[str, Any]:
    """Publication volume overall and in the recent window."""
    names = search_names(disease_name)
    if not names:
        return {"assessed": False, "reason": "No disease name supplied."}
    cutoff = _recent_cutoff_year()

    def total(name: str) -> Optional[int]:
        return _epmc_count(f'"{name}"')

    def recent(name: str) -> Optional[int]:
        return _epmc_count(
            f'"{name}" AND (FIRST_PDATE:[{cutoff}-01-01 TO 3000-01-01])')

    n_total, ok = _max_over_names(names, total)
    if not ok:
        return {
            "assessed": False,
            "names_searched": names,
            "reason": ("Europe PMC did not answer for any name. That is not "
                       "a publication count of zero."),
        }
    n_recent, _ = _max_over_names(names, recent)
    share = None
    if n_total and n_recent is not None and n_total > 0:
        share = n_recent / n_total
    return {
        "assessed": True,
        "names_searched": names,
        "n_publications": n_total,
        "n_recent_publications": n_recent,
        "recent_share": share,
        "recent_since_year": cutoff,
    }


def attention_verdict(disease_name: str) -> dict[str, Any]:
    """Is this disease dormant enough that arriving first would matter?

    Returned as a named condition plus the raw counts that produced it. No
    composite, no weight: this repo has spent two cycles removing uncalibrated
    constants that competed with measured evidence, and a disease-attention
    score would be a third.
    """
    name = str(disease_name or "").strip()
    cache_key = make_key(_CACHE_VERSION, name.casefold(),
                         str(MAX_TRIALS_FOR_DORMANT), str(RECENT_YEARS))
    cached = get(cache_key)
    if cached is not None:
        return dict(cached)

    trials = trial_attention(name)
    literature = literature_attention(name)
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "disease_name": name,
        "trials": trials,
        "literature": literature,
    }

    if not trials.get("assessed"):
        envelope["verdict"] = VERDICT_UNKNOWN
        envelope["reason"] = str(
            trials.get("reason")
            or "Clinical activity could not be measured.")
        return envelope  # deliberately not cached: retry a provider outage

    n_total = int(trials.get("n_interventional") or 0)
    n_open = trials.get("n_open")
    n_industry = trials.get("n_industry_sponsored")

    if n_total > MAX_TRIALS_FOR_DORMANT:
        envelope["verdict"] = VERDICT_ACTIVE_TRIALS
        detail = f"{n_total} registered interventional trial(s)"
        if n_open:
            detail += f", {n_open} open"
        if n_industry:
            detail += f", {n_industry} with an industry lead sponsor"
        envelope["reason"] = (
            f"{detail}. Somebody has already taken a hypothesis in this "
            "disease as far as a protocol and a sponsor, so a candidate found "
            "here would arrive beside an existing clinical program rather "
            "than in its absence.")
        cache_set(cache_key, envelope, ttl_days=_TTL_DAYS)
        return envelope

    n_recent_pubs = literature.get("n_recent_publications")
    share = literature.get("recent_share")
    if (literature.get("assessed")
            and isinstance(n_recent_pubs, int)
            and n_recent_pubs >= RENEWED_RECENT_FLOOR
            and isinstance(share, float)
            and share >= RENEWED_RECENT_SHARE):
        envelope["verdict"] = VERDICT_RENEWED_INTEREST
        envelope["reason"] = (
            f"No registered trials, but {n_recent_pubs} of "
            f"{literature.get('n_publications')} publications "
            f"({share:.0%}) appeared since {literature.get('recent_since_year')}. "
            "The registry is empty because the work has not reached the clinic "
            "yet, not because nobody is doing it.")
        cache_set(cache_key, envelope, ttl_days=_TTL_DAYS)
        return envelope

    envelope["verdict"] = VERDICT_DORMANT
    envelope["reason"] = (
        f"No registered interventional trials across {len(trials.get('names_searched') or [])} "
        "name(s), and no recent surge in publications. Nothing in either index "
        "shows a group currently working toward a treatment.")
    cache_set(cache_key, envelope, ttl_days=_TTL_DAYS)
    return envelope
