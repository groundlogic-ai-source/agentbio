"""Run the bounded, low-cost Orphanet six-filter screen.

This runner deliberately does not call the full LangGraph/UI pipeline.  The
bulk pass is LLM-free and fail-closed:

  1. Open Targets says the exact disease has no approved treatment.
  2. ClinicalTrials.gov has no disease-name trial records (raw count only).
  3. Open Targets contains a confirmed Orphanet gene among its direct targets.
  4. The target has an approved-drug pool with complete source coverage.

Only survivors enter the expensive mechanism-direction lane.  The two hard
stops are independent:

  --max-successes          stop after this many full compatible hits
  --max-expensive-checks   stop even if fewer hits are found

The output is checkpointed after every row, so a stopped workflow can resume
without repeating completed bulk checks.  Safety review, rationale prose, and
Boltz structure validation are intentionally absent from this discovery pass.
They belong only on the final outreach dossier candidates.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
from typing import Any

import requests

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from cache.cache import get, make_key, set as cache_set
from data_sources.mechanism_direction import check_mechanism_direction
from data_sources.multisource_candidates import collect_target_candidates
from data_sources.open_targets import (
    get_disease_known_drugs,
    get_target_disease_score,
    search_disease_efo,
)
from agents.target_selection import _enrich_approved_via_parents


QUEUE_PATH = os.path.join(REPO_ROOT, "output", "orphanet_prefilter_queue.json")
RESULT_PATH = os.path.join(REPO_ROOT, "output", "orphanet_bounded_screen.json")
CLINICAL_TRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
ENABLED_SOURCES = ("chembl", "gtopdb", "drugcentral", "bindingdb")
SCHEMA_VERSION = "orphanet-bounded-screen-v1"
_KATP_HYPERINSULINISM_TARGETS = frozenset({"KCNJ11", "ABCC8"})
_KATP_BLOCKERS = frozenset({"glibenclamide", "glyburide"})

_TRIAL_LOCK = threading.Lock()
_LAST_TRIAL_REQUEST = 0.0
_TRIAL_MIN_INTERVAL = float(
    os.environ.get("AGENTBIO_CTG_MIN_INTERVAL_SECONDS", "0.35")
)


def _atomic_write(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".orphanet-screen-", suffix=".json", dir=os.path.dirname(path)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _throttle_trials() -> None:
    global _LAST_TRIAL_REQUEST
    with _TRIAL_LOCK:
        now = time.monotonic()
        wait = _LAST_TRIAL_REQUEST + _TRIAL_MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        _LAST_TRIAL_REQUEST = time.monotonic()


def _raw_trial_count(disease_name: str) -> dict[str, Any]:
    """Count disease-name trial records without classifying stopped trials."""
    cache_key = make_key("orphanet_bounded_raw_trial_count_v1", disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    params = {
        "query.term": disease_name,
        "countTotal": "true",
        "pageSize": 1,
        "format": "json",
        "fields": "NCTId",
    }
    try:
        _throttle_trials()
        response = requests.get(CLINICAL_TRIALS_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        result = {
            "trial_count": int(data.get("totalCount", 0)),
            "query_failed": False,
        }
    except Exception as exc:
        result = {
            "trial_count": None,
            "query_failed": True,
            "error": str(exc),
        }
        # Do not cache transient failures.  An unavailable source is not zero.
        return result

    cache_set(cache_key, result, ttl_days=3)
    return result


def _status_value(status: Any) -> str:
    if isinstance(status, dict):
        return str(status.get("status") or "").casefold()
    return str(status or "").casefold()


def _pool_has_complete_coverage(
    pool: dict[str, Any],
) -> tuple[bool, str]:
    candidates = pool.get("candidates") or []
    statuses = pool.get("source_status") or {}
    if not candidates:
        return False, "approved_drug_pool_empty"

    unavailable = [
        source
        for source in ENABLED_SOURCES
        if _status_value(statuses.get(source)) in {
            "unavailable",
            "error",
            "degraded",
            "failed",
        }
    ]
    if unavailable:
        return False, "source_coverage_incomplete:" + ",".join(unavailable)

    return True, "approved_drug_pool_verified"


def _gene_set(row: dict[str, Any]) -> set[str]:
    return {
        str(assoc.get("symbol") or "").casefold()
        for assoc in row.get("gene_associations") or []
        if assoc.get("symbol")
    }


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, str]:
    def number(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0

    return (
        -number(candidate.get("pchembl_value")),
        -number(candidate.get("confidence_score")),
        str(candidate.get("drug_name") or "").casefold(),
    )


def _cheap_polarity_exclusion(
    disease_name: str,
    target_symbol: str,
    candidate: dict[str, Any],
) -> str | None:
    """Exclude a known opposite-polarity KATP pairing before LLM review.

    KCNJ11/ABCC8 loss-of-function hyperinsulinism is the channel-closed
    disease direction.  Glibenclamide/glyburide is a KATP blocker used in the
    opposite gain-of-function neonatal-diabetes biology, so it is not a
    credible discovery candidate for this disease family.
    """
    if (
        target_symbol.upper() in _KATP_HYPERINSULINISM_TARGETS
        and "hyperinsulinism" in disease_name.casefold()
        and str(candidate.get("drug_name") or "").casefold() in _KATP_BLOCKERS
    ):
        return "opposite_katp_polarity_for_hyperinsulinism"
    return None


def _stage1_row(row: dict[str, Any]) -> dict[str, Any]:
    disease = row["disease_name"]
    result: dict[str, Any] = {
        "disease_name": disease,
        "orpha_code": row.get("orpha_code"),
        "gene_associations": row.get("gene_associations") or [],
        "stage1_status": "rejected",
        "rejection_reason": None,
        "efo_id": None,
        "direct_targets": [],
        "survivors": [],
    }

    efo_id = search_disease_efo(disease)
    result["efo_id"] = efo_id
    if not efo_id:
        result["rejection_reason"] = "efo_unresolved"
        return result

    treatment = get_disease_known_drugs(efo_id)
    (
        parent_has_approved,
        parent_approved_names,
        parent_efo_id,
        parent_drug_names,
    ) = _enrich_approved_via_parents(
        efo_id,
        treatment.get("has_approved_treatment"),
        treatment.get("approved_drug_names") or [],
    )
    treatment = dict(treatment)
    treatment["has_approved_treatment"] = parent_has_approved
    treatment["approved_drug_names"] = parent_approved_names
    treatment["parent_umbrella_efo_id"] = parent_efo_id
    treatment["parent_umbrella_approved_drug_names"] = parent_drug_names
    result["approved_treatment"] = treatment
    if treatment.get("has_approved_treatment") is not False:
        result["rejection_reason"] = (
            "approved_treatment_present"
            if treatment.get("has_approved_treatment") is True
            else "approved_treatment_unknown"
        )
        return result

    trials = _raw_trial_count(disease)
    result["prior_trials"] = trials
    if trials.get("query_failed"):
        result["rejection_reason"] = "prior_trial_status_unknown"
        return result
    if trials.get("trial_count") != 0:
        result["rejection_reason"] = "prior_trials_present"
        return result

    target_rows = get_target_disease_score(efo_id)
    confirmed_genes = _gene_set(row)
    direct_targets = [
        target
        for target in target_rows
        if str(target.get("target_symbol") or "").casefold() in confirmed_genes
        and target.get("uniprot_id")
    ]
    result["direct_targets"] = direct_targets
    if not direct_targets:
        result["rejection_reason"] = "confirmed_gene_not_in_ot_direct_targets"
        return result

    for target in direct_targets:
        gene = target.get("target_symbol") or ""
        pool = collect_target_candidates(
            target["uniprot_id"],
            gene,
            disease,
            target.get("association_score"),
            "genetic_association",
            repurposing_only=True,
            enabled_sources=ENABLED_SOURCES,
        )
        pool_ok, pool_reason = _pool_has_complete_coverage(pool)
        target_result = {
            "target": target,
            "pool_reason": pool_reason,
            "source_status": pool.get("source_status") or {},
            "candidate_count": len(pool.get("candidates") or []),
        }
        if pool_ok:
            kept_candidates = []
            excluded_candidates = []
            for candidate in sorted(
                pool.get("candidates") or [], key=_candidate_sort_key
            ):
                exclusion = _cheap_polarity_exclusion(
                    disease,
                    str(target.get("target_symbol") or ""),
                    candidate,
                )
                if exclusion:
                    excluded_candidates.append(
                        {
                            "drug_name": candidate.get("drug_name"),
                            "reason": exclusion,
                        }
                    )
                else:
                    kept_candidates.append(candidate)
            target_result["candidate_exclusions"] = excluded_candidates
            target_result["candidates"] = kept_candidates
            if kept_candidates:
                result["survivors"].append(target_result)
            else:
                target_result["pool_reason"] = "all_candidates_failed_cheap_polarity_gate"

    if result["survivors"]:
        result["stage1_status"] = "survivor"
        result["rejection_reason"] = None
    else:
        result["rejection_reason"] = "no_verified_approved_target_pool"
    return result


def repair_parent_treatment_reconciliation(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Repair completed Stage 1 rows written before parent reconciliation.

    This is deliberately a separate cheap operation: it does not repeat the
    source fan-out or spend mechanism checks.  Any candidate invalidated here
    is marked in the existing Stage 2 audit rather than pretending the already
    spent request never happened.
    """
    invalidated: list[str] = []
    for row in payload.get("stage1", {}).get("rows") or []:
        if row.get("stage1_status") != "survivor" or not row.get("efo_id"):
            continue
        treatment = row.get("approved_treatment") or {}
        if treatment.get("has_approved_treatment") is True:
            continue
        (
            parent_has_approved,
            parent_approved_names,
            parent_efo_id,
            parent_drug_names,
        ) = _enrich_approved_via_parents(
            row["efo_id"],
            treatment.get("has_approved_treatment"),
            treatment.get("approved_drug_names") or [],
        )
        treatment = dict(treatment)
        treatment["has_approved_treatment"] = parent_has_approved
        treatment["approved_drug_names"] = parent_approved_names
        treatment["parent_umbrella_efo_id"] = parent_efo_id
        treatment["parent_umbrella_approved_drug_names"] = parent_drug_names
        row["approved_treatment"] = treatment
        if parent_has_approved is True:
            disease = row.get("disease_name")
            row["stage1_status"] = "rejected"
            row["rejection_reason"] = (
                "approved_treatment_present_via_parent_umbrella"
            )
            row["survivors"] = []
            invalidated.append(disease)

        for survivor in row.get("survivors") or []:
            target = survivor.get("target") or {}
            kept = []
            excluded = list(survivor.get("candidate_exclusions") or [])
            for candidate in survivor.get("candidates") or []:
                exclusion = _cheap_polarity_exclusion(
                    row.get("disease_name") or "",
                    str(target.get("target_symbol") or ""),
                    candidate,
                )
                if exclusion:
                    excluded.append(
                        {
                            "drug_name": candidate.get("drug_name"),
                            "reason": exclusion,
                        }
                    )
                else:
                    kept.append(candidate)
            survivor["candidates"] = kept
            survivor["candidate_exclusions"] = excluded
            survivor["candidate_count"] = len(kept)

        if row.get("stage1_status") == "survivor":
            row["survivors"] = [
                survivor
                for survivor in row.get("survivors") or []
                if survivor.get("candidates")
            ]
            if not row["survivors"]:
                row["stage1_status"] = "rejected"
                row["rejection_reason"] = "no_candidate_after_cheap_polarity_gate"
                invalidated.append(row.get("disease_name"))

    if invalidated:
        for check in payload.get("stage2", {}).get("checks") or []:
            if check.get("disease_name") in invalidated:
                check["invalidated_stage1_gate"] = (
                    "approved_treatment_present_via_parent_umbrella"
                )
        payload["stage1"]["survivor_count"] = sum(
            1
            for row in payload["stage1"].get("rows") or []
            if row.get("stage1_status") == "survivor"
        )
        payload["stage2"]["invalidated_check_count"] = sum(
            1
            for check in payload["stage2"].get("checks") or []
            if check.get("invalidated_stage1_gate")
        )
    payload["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _atomic_write(RESULT_PATH, payload)
    return payload, invalidated


def _new_payload(max_successes: int, max_expensive_checks: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_at_utc": None,
        "config": {
            "max_successes": max_successes,
            "max_expensive_checks": max_expensive_checks,
            "enabled_sources": list(ENABLED_SOURCES),
            "llm_rationales": False,
            "structure_validation": False,
            "stage1_llm_calls": 0,
        },
        "stage1": {
            "completed": 0,
            "total": 0,
            "rows": [],
            "survivor_count": 0,
        },
        "stage2": {
            "expensive_checks": 0,
            "successes": [],
            "checks": [],
            "stop_reason": None,
        },
    }


def _load_or_create(max_successes: int, max_expensive_checks: int) -> dict[str, Any]:
    if not os.path.exists(RESULT_PATH):
        return _new_payload(max_successes, max_expensive_checks)
    with open(RESULT_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != SCHEMA_VERSION:
        return _new_payload(max_successes, max_expensive_checks)
    old_config = payload.get("config") or {}
    if (
        old_config.get("max_successes") != max_successes
        or old_config.get("max_expensive_checks") != max_expensive_checks
    ):
        # Permit changing the backup budget while the cheap bulk pass is still
        # running.  No expensive result has been consumed in this state, so
        # updating the limits cannot invalidate a Stage 2 conclusion.
        if (
            payload.get("stage1", {}).get("completed", 0)
            < payload.get("stage1", {}).get("total", 0)
            and not payload.get("stage2", {}).get("checks")
        ):
            payload["config"].update(
                {
                    "max_successes": max_successes,
                    "max_expensive_checks": max_expensive_checks,
                }
            )
            return payload
        raise RuntimeError(
            "Existing bounded-screen checkpoint has different stop limits. "
            "Delete output/orphanet_bounded_screen.json only if a fresh run is "
            "intended."
        )
    return payload


def _run_stage2(
    payload: dict[str, Any],
    max_successes: int,
    max_expensive_checks: int,
) -> None:
    """Run Stage 2 up to an absolute checkpoint cap.

    Existing checks are keyed and skipped, so callers can safely append a
    bounded batch without replaying or rewriting the prior mechanism work.
    """
    stage1_survivors = [
        completed
        for completed in payload["stage1"].get("rows") or []
        if completed.get("stage1_status") == "survivor"
    ]
    stage1_survivors.sort(
        key=lambda completed: (
            min(
                (
                    target.get("candidate_count", 10**9)
                    for target in completed.get("survivors") or []
                ),
                default=10**9,
            ),
            str(completed.get("disease_name") or "").casefold(),
        )
    )
    checked_keys = {
        (
            check.get("disease_name"),
            (check.get("target") or {}).get("target_symbol"),
            (check.get("candidate") or {}).get("drug_name"),
        )
        for check in payload["stage2"].get("checks") or []
    }
    for survivor_row in stage1_survivors:
        disease = survivor_row["disease_name"]
        for survivor in survivor_row.get("survivors") or []:
            target = survivor["target"]
            for candidate in survivor.get("candidates") or []:
                if len(payload["stage2"].get("successes") or []) >= max_successes:
                    payload["stage2"]["stop_reason"] = "max_successes_reached"
                    break
                if (
                    payload["stage2"].get("expensive_checks", 0)
                    >= max_expensive_checks
                ):
                    payload["stage2"]["stop_reason"] = (
                        "max_expensive_checks_reached"
                    )
                    break

                drug_name = candidate.get("drug_name") or candidate.get("pref_name")
                check_key = (disease, target.get("target_symbol"), drug_name)
                if not drug_name or check_key in checked_keys:
                    continue
                print(
                    f"[bounded-screen] Stage 2 check "
                    f"{payload['stage2']['expensive_checks'] + 1}/"
                    f"{max_expensive_checks}: {disease} / {drug_name}",
                    flush=True,
                )
                direction = check_mechanism_direction(
                    drug_name=drug_name,
                    target_symbol=target.get("target_symbol") or "",
                    action_type=candidate.get("action_type"),
                    mechanism_of_action=candidate.get("mechanism_of_action"),
                    disease_name=disease,
                    candidate_chembl_ids=(
                        [candidate["molecule_chembl_id"]]
                        if candidate.get("molecule_chembl_id")
                        else None
                    ),
                    candidate_inchikey=candidate.get("inchikey"),
                )
                check = {
                    "disease_name": disease,
                    "orpha_code": survivor_row.get("orpha_code"),
                    "target": target,
                    "candidate": {
                        key: candidate.get(key)
                        for key in (
                            "drug_name",
                            "molecule_chembl_id",
                            "inchikey",
                            "action_type",
                            "mechanism_of_action",
                            "pchembl_value",
                            "confidence_score",
                        )
                    },
                    "direction": direction,
                }
                payload["stage2"]["checks"].append(check)
                payload["stage2"]["expensive_checks"] += 1
                checked_keys.add(check_key)
                if direction.get("compatible") is True:
                    payload["stage2"]["successes"].append(check)
                    break
                payload["updated_at_utc"] = dt.datetime.now(
                    dt.timezone.utc
                ).isoformat()
                _atomic_write(RESULT_PATH, payload)
            if (
                len(payload["stage2"].get("successes") or []) >= max_successes
                or payload["stage2"].get("expensive_checks", 0)
                >= max_expensive_checks
            ):
                break
        if (
            len(payload["stage2"].get("successes") or []) >= max_successes
            or payload["stage2"].get("expensive_checks", 0)
            >= max_expensive_checks
        ):
            break

    if payload["stage2"].get("stop_reason") is None:
        if len(payload["stage2"].get("successes") or []) >= max_successes:
            payload["stage2"]["stop_reason"] = "max_successes_reached"
        elif (
            payload["stage2"].get("expensive_checks", 0)
            >= max_expensive_checks
        ):
            payload["stage2"]["stop_reason"] = "max_expensive_checks_reached"
        else:
            payload["stage2"]["stop_reason"] = "queue_exhausted"


def continue_stage2(additional_expensive_checks: int) -> dict[str, Any]:
    """Append one bounded Stage 2 batch to a completed checkpoint."""
    if not os.path.exists(RESULT_PATH):
        raise RuntimeError(f"checkpoint not found: {RESULT_PATH}")
    with open(RESULT_PATH, encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("checkpoint schema does not support continuation")
    if (
        payload.get("stage1", {}).get("completed", 0)
        < payload.get("stage1", {}).get("total", 0)
    ):
        raise RuntimeError("cannot continue Stage 2 before Stage 1 is complete")

    current_checks = payload["stage2"].get("expensive_checks", 0)
    total_cap = current_checks + additional_expensive_checks
    config = payload.setdefault("config", {})
    config["last_appended_stage2_batch"] = additional_expensive_checks
    config["max_expensive_checks"] = total_cap
    payload["stage2"]["stop_reason"] = None
    _run_stage2(
        payload,
        max_successes=config.get("max_successes", 5),
        max_expensive_checks=total_cap,
    )
    payload["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _atomic_write(RESULT_PATH, payload)
    return payload


def run(max_successes: int, max_expensive_checks: int) -> dict[str, Any]:
    with open(QUEUE_PATH, encoding="utf-8") as handle:
        queue = json.load(handle)
    rows = queue.get("candidates") or []

    payload = _load_or_create(max_successes, max_expensive_checks)
    payload["stage1"]["total"] = len(rows)
    # An earlier runner version interleaved Stage 1 and Stage 2.  If that
    # version stopped on the expensive-check cap before the bulk queue was
    # complete, its Stage 2 results are not representative of the queue.
    # Preserve a compact diagnostic record, reset that budget, and finish the
    # LLM-free bulk pass before starting Stage 2.
    if (
        payload["stage1"].get("completed", 0) < len(rows)
        and payload["stage2"].get("checks")
    ):
        payload["superseded_stage2_diagnostic"] = [
            {
                "disease_name": check.get("disease_name"),
                "drug_name": (check.get("candidate") or {}).get("drug_name"),
                "verdict": (check.get("direction") or {}).get("verdict"),
            }
            for check in payload["stage2"].get("checks") or []
        ]
        payload["stage2"] = {
            "expensive_checks": 0,
            "successes": [],
            "checks": [],
            "stop_reason": None,
        }
        _atomic_write(RESULT_PATH, payload)

    completed_names = {
        row.get("disease_name")
        for row in payload["stage1"].get("rows") or []
    }

    for index, row in enumerate(rows, start=1):
        disease = row["disease_name"]
        if disease in completed_names:
            continue

        print(
            f"[bounded-screen] Stage 1 {index}/{len(rows)}: {disease}",
            flush=True,
        )
        try:
            stage1 = _stage1_row(row)
        except Exception as exc:
            stage1 = {
                "disease_name": disease,
                "orpha_code": row.get("orpha_code"),
                "stage1_status": "error",
                "rejection_reason": "stage1_exception",
                "error": repr(exc),
                "survivors": [],
            }
        payload["stage1"]["rows"].append(stage1)
        payload["stage1"]["completed"] = len(payload["stage1"]["rows"])
        payload["stage1"]["survivor_count"] = sum(
            1
            for completed in payload["stage1"]["rows"]
            if completed.get("stage1_status") == "survivor"
        )
        payload["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _atomic_write(RESULT_PATH, payload)

    # Do not spend any LLM calls until every disease has received the cheap
    # Stage 1 checks.  This prevents alphabetical ordering or an early
    # survivor from consuming the whole expensive budget.
    if payload["stage1"].get("completed") >= payload["stage1"].get("total"):
        _run_stage2(payload, max_successes, max_expensive_checks)
    payload["updated_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _atomic_write(RESULT_PATH, payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-successes", type=int, default=3)
    parser.add_argument("--max-expensive-checks", type=int, default=10)
    parser.add_argument(
        "--repair-parent-treatment",
        action="store_true",
        help="repair completed Stage 1 rows without starting new mechanism checks",
    )
    parser.add_argument(
        "--continue-expensive-checks",
        type=int,
        default=0,
        help="append this many Stage 2 checks to the existing checkpoint",
    )
    args = parser.parse_args()
    if args.max_successes < 1 or args.max_expensive_checks < 1:
        raise SystemExit("stop limits must both be positive integers")
    if args.continue_expensive_checks < 0:
        raise SystemExit("--continue-expensive-checks cannot be negative")
    if args.repair_parent_treatment and args.continue_expensive_checks:
        raise SystemExit(
            "--repair-parent-treatment and --continue-expensive-checks "
            "cannot be combined"
        )
    if args.repair_parent_treatment:
        if not os.path.exists(RESULT_PATH):
            raise SystemExit(f"checkpoint not found: {RESULT_PATH}")
        with open(RESULT_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
        payload, invalidated = repair_parent_treatment_reconciliation(payload)
        print(
            json.dumps(
                {
                    "repaired_diseases": invalidated,
                    "survivors_remaining": payload["stage1"]["survivor_count"],
                    "invalidated_stage2_checks": payload["stage2"].get(
                        "invalidated_check_count", 0
                    ),
                },
                indent=2,
            )
        )
        return
    if args.continue_expensive_checks:
        payload = continue_stage2(args.continue_expensive_checks)
        print(
            json.dumps(
                {
                    "stage1_survivors": payload["stage1"]["survivor_count"],
                    "prior_and_new_expensive_checks": payload["stage2"][
                        "expensive_checks"
                    ],
                    "new_batch_size": args.continue_expensive_checks,
                    "successes": len(payload["stage2"]["successes"]),
                    "stop_reason": payload["stage2"]["stop_reason"],
                    "result_path": RESULT_PATH,
                },
                indent=2,
            )
        )
        return
    payload = run(args.max_successes, args.max_expensive_checks)
    print(
        json.dumps(
            {
                "stage1_completed": payload["stage1"]["completed"],
                "stage1_total": payload["stage1"]["total"],
                "stage1_survivors": payload["stage1"]["survivor_count"],
                "expensive_checks": payload["stage2"]["expensive_checks"],
                "successes": len(payload["stage2"]["successes"]),
                "stop_reason": payload["stage2"]["stop_reason"],
                "result_path": RESULT_PATH,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()