"""
ClinicalTrials.gov API v2 — trial history lookup.

v2 change (vs original):
  has_negative_repurposing_result is now set ONLY when at least one stopped
  trial is classified as an efficacy/safety failure via a Haiku LLM call on
  the trial's whyStopped text.

  Old behaviour: ANY terminated/withdrawn/suspended status → negative signal
  regardless of why (administrative stops counted as failures — wrong).

  New behaviour:
    - whyStopped present + LLM says EFFICACY_FAILURE → TRUE negative signal
    - whyStopped present + LLM says ADMINISTRATIVE or UNCLEAR → not negative
    - whyStopped absent/empty → classification = NO_REASON_GIVEN, not negative
    - LLM client unavailable → classification = UNCLASSIFIED_NO_CLIENT, not negative

  why_stopped and why_stopped_classification are surfaced on every trial dict
  for auditability.
"""

import os
import random
import threading
import time
import requests
from typing import Any, Optional
from cache.cache import get, set as cache_set, make_key
from data_sources import holdout
from data_sources.llm_failover import chat_text

import anthropic

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

NEGATIVE_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
COMPLETED_STATUSES = {"COMPLETED"}

HAIKU_MODEL = "claude-haiku-4-5-20251001"
_AI_TIMEOUT_SECONDS = 60.0
_AI_MAX_RETRIES = 0

VALID_CLASSIFICATIONS = {"EFFICACY_FAILURE", "ADMINISTRATIVE", "UNCLEAR"}

# ClinicalTrials.gov rate limiting. The reviewer fans out five provider lanes
# x N workers at this endpoint; unthrottled that reliably triggers sustained
# 429s. Each failure drops the trial term from a candidate's composite as a
# coverage gap, so a 429 storm silently thins the evidence a pool is scored
# on — a data-quality problem, not merely a slowness problem.
_RATE_LIMIT_ATTEMPTS = 4
_RETRY_BASE_SECONDS = 2.0
_MIN_REQUEST_INTERVAL = float(
    os.environ.get("AGENTBIO_CTG_MIN_INTERVAL_SECONDS", "0.35"))
_THROTTLE_LOCK = threading.Lock()
_last_request_at = 0.0


def _throttle() -> None:
    """Process-wide minimum spacing between ClinicalTrials.gov requests.

    The lock IS held across a sleep, deliberately: that is what serializes
    callers into a global rate limit. It is safe because the wait is bounded
    by _MIN_REQUEST_INTERVAL and never spans a network call — unlike the
    timeout-less lock that once wedged every prefetch lane at once.
    """
    global _last_request_at
    with _THROTTLE_LOCK:
        now = time.monotonic()
        wait = _last_request_at + _MIN_REQUEST_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
            now = time.monotonic()
        _last_request_at = now


def _retry_delay(resp: requests.Response, attempt: int) -> float:
    """Honour Retry-After when present, else exponential backoff + jitter."""
    retry_after = (resp.headers or {}).get("Retry-After")
    if retry_after:
        try:
            return max(0.0, min(60.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return _RETRY_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 0.5)


def _anthropic_client() -> Optional[anthropic.Anthropic]:
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key  = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        return None
    return anthropic.Anthropic(
        base_url=base_url,
        api_key=api_key,
        timeout=_AI_TIMEOUT_SECONDS,
        max_retries=_AI_MAX_RETRIES,
    )


def _classify_why_stopped(why_stopped: str, client: anthropic.Anthropic) -> str:
    """
    One Haiku LLM call (temperature=0) to classify a clinical trial's
    whyStopped text.

    Returns one of:
      EFFICACY_FAILURE  — trial stopped because treatment did not work or
                          caused harm (lack of efficacy, safety concern,
                          adverse events, futility, DSMB recommendation).
      ADMINISTRATIVE    — trial stopped for a non-clinical reason: funding,
                          business/sponsor decision, low enrollment, post-
                          marketing commitment fulfilled, protocol design
                          change, regulatory action unrelated to outcome.
      UNCLEAR           — text is ambiguous; cannot determine reason.
    """
    prompt = (
        "A clinical trial was stopped before completion.\n\n"
        f'Why stopped: "{why_stopped}"\n\n'
        "Classify the reason the trial was stopped.\n\n"
        "Reply with EXACTLY one of these three tokens and nothing else:\n"
        "EFFICACY_FAILURE — the trial stopped because the treatment did not "
        "work or caused harm (e.g. lack of efficacy, safety concern, adverse "
        "events, futility, DSMB recommendation based on clinical outcome)\n"
        "ADMINISTRATIVE — the trial stopped for a non-clinical reason "
        "(e.g. post-marketing commitment fulfilled, funding ended, sponsor "
        "or business decision, low enrollment, protocol design change, "
        "study purpose already met)\n"
        "UNCLEAR — cannot determine from the available text"
    )
    try:
        # Round-robin across providers + 429 failover (deterministic decoding).
        raw_text, _provider = chat_text(prompt, max_tokens=10)
        raw = raw_text.strip().split()[0].upper() if raw_text.strip() else ""
        return raw if raw in VALID_CLASSIFICATIONS else "UNCLEAR"
    except Exception as e:
        print(f"[clinicaltrials] WARNING: LLM classification failed ({e})")
        return "UNCLEAR"


def _search_trials(drug_name: str, disease_name: str) -> tuple[list[dict], bool]:
    """
    Returns (studies, query_failed).
    query_failed=True means the API was unreachable — callers must NOT treat
    this as "no trials exist" (fail-open). Callers should conservatively
    withhold the no-failed-trial scoring credit when query_failed=True.
    """
    query = f"{drug_name} AND {disease_name}"
    params = {
        "query.term": query,
        "fields": "NCTId,BriefTitle,OverallStatus,WhyStopped,ResultsFirstPostDate",
        "pageSize": 100,
        "format": "json",
    }
    for attempt in range(_RATE_LIMIT_ATTEMPTS):
        _throttle()
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            if resp.status_code == 429:
                # A 429 is NOT evidence that no trials exist. Back off and
                # retry; only after exhausting attempts do we report
                # query_failed so the caller records a coverage gap.
                if attempt < _RATE_LIMIT_ATTEMPTS - 1:
                    time.sleep(_retry_delay(resp, attempt))
                    continue
                print("[clinicaltrials] WARNING: rate limited (429) after "
                      f"{_RATE_LIMIT_ATTEMPTS} attempts")
                return [], True
            resp.raise_for_status()
            data = resp.json()
            return data.get("studies", []), False
        except Exception as e:
            print(f"[clinicaltrials] WARNING: API call failed ({e})")
            return [], True
    return [], True


def check_prior_trials(
    drug_name: str,
    disease_name: str,
    candidate_chembl_ids: Optional[list[str]] = None,
    candidate_inchikey: Optional[str] = None,
) -> dict[str, Any]:
    """
    Returns:
      - trials: list of {nct_id, title, status, why_stopped,
                         why_stopped_classification, has_results}
      - has_negative_repurposing_result: True ONLY if at least one stopped
        trial was classified EFFICACY_FAILURE by the LLM. Administrative
        stops (PMC fulfilment, sponsor decision, enrollment issues, etc.)
        do NOT count as negative signals.
      - trial_count: total trials found

    Cache key v2 — bumped from the original because the negative-signal
    classification logic changed. Old v1 entries are silently ignored.
    """
    # Retrospective holdout: a drug+disease trial lookup is direct indication
    # leakage. Return an explicit sealed result without touching the network.
    heldout_molecule = any(
        holdout.matches_molecule(mid)
        for mid in (candidate_chembl_ids or [])
        if mid
    )
    if holdout.is_active() and (
        holdout.matches_name(drug_name)
        or heldout_molecule
        or holdout.matches_inchikey(candidate_inchikey)
    ):
        return {
            "trials": [],
            "has_negative_repurposing_result": False,
            "trial_count": 0,
            "query_failed": False,
            "holdout_redacted": True,
        }

    # v2 key: caches the LLM-classified output (not just the raw status flag)
    cache_key = make_key("check_prior_trials_v2", drug_name, disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    raw_studies, query_failed = _search_trials(drug_name, disease_name)
    client = _anthropic_client()

    trials = []
    has_negative = False

    for study in raw_studies:
        protocol       = study.get("protocolSection", {})
        id_module      = protocol.get("identificationModule", {})
        status_module  = protocol.get("statusModule", {})
        results_module = study.get("resultsSection", {})

        nct_id      = id_module.get("nctId", "")
        title       = id_module.get("briefTitle", "")
        status      = status_module.get("overallStatus", "UNKNOWN")
        why_stopped = (status_module.get("whyStopped") or "").strip()
        has_results = bool(results_module)

        classification: Optional[str] = None

        if status.upper() in NEGATIVE_STATUSES:
            if not why_stopped:
                # No reason text available — do not assume failure.
                classification = "NO_REASON_GIVEN"
            elif client is not None:
                classification = _classify_why_stopped(why_stopped, client)
                if classification == "EFFICACY_FAILURE":
                    has_negative = True
            else:
                # LLM client unavailable (missing env vars); cannot classify.
                # Do NOT count as negative to avoid false penalisation.
                classification = "UNCLASSIFIED_NO_CLIENT"

        trials.append({
            "nct_id": nct_id,
            "title": title,
            "status": status,
            "why_stopped": why_stopped or None,
            "why_stopped_classification": classification,
            "has_results": has_results,
        })

    result = {
        "trials": trials,
        "has_negative_repurposing_result": has_negative,
        "trial_count": len(trials),
        # True when the API was unreachable — distinguishes "queried successfully,
        # found nothing" from "query failed".  Callers must not award
        # no-failed-trial scoring credit when this flag is set.
        "query_failed": query_failed,
        "holdout_redacted": False,
    }
    # Do not cache a failed query result — retry next time.
    if not query_failed:
        cache_set(cache_key, result, ttl_days=3)
    return result
