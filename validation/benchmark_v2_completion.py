"""Fail-closed completion guard for the single pre-registered v2 benchmark.

Benchmark v2 completed in production on 2026-08-09 and its result artifact was
pulled back into the repository.  The protocol permits one run, not a reroll.
This module pins that artifact and gives the API and production supervisor one
authoritative way to recognize the frozen completion state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from typing import Any


RESULTS_JSON = "validation/benchmark_results_v2.json"
FREEZE_TAG = "benchmark-freeze-v2"
EXPECTED_SHA256 = "b318f61c892b76df63c1e4a673d7e9be082b16b609e19722991b673e1cfc2c1d"
EXPECTED_CASES = 47
EXPECTED_PRIMARY = 32
EXPECTED_DEVELOPMENT = 15
SELECTED_PRIMARY = 50
COMPLETED_ON = "2026-08-09"


def _base(phase: str, detail: str) -> dict[str, Any]:
    return {
        "phase": phase,
        "detail": detail,
        "complete": False,
        "frozen": True,
        "rerun_allowed": False,
        "completed_on": COMPLETED_ON,
    }


def inspect_frozen_result(path: str = RESULTS_JSON) -> dict[str, Any]:
    """Verify and summarize the immutable benchmark-v2 result artifact.

    Any missing, unreadable, or modified artifact remains a hard rerun refusal.
    It requires provenance review; it never falls back to launching a new run.
    """
    if not os.path.exists(path):
        return _base(
            "frozen_result_missing",
            "The frozen benchmark-v2 result artifact is missing; manual provenance review is required.",
        )

    try:
        with open(path, "rb") as result_file:
            raw = result_file.read()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _base(
            "frozen_result_unreadable",
            f"The frozen benchmark-v2 result artifact is unreadable: {exc}",
        )

    digest = hashlib.sha256(raw).hexdigest()
    cases = payload.get("cases")
    if not isinstance(cases, list):
        cases = []

    subset_counts = Counter(str(case.get("subset")) for case in cases)
    primary = [case for case in cases if case.get("subset") == "primary"]
    in_scope_primary = [
        case for case in primary
        if case.get("in_universe") is True
        and case.get("status") in {"hit", "miss"}
    ]
    hits = [case for case in in_scope_primary if case.get("found") is True]
    status_counts = Counter(str(case.get("status")) for case in cases)

    summary = {
        "result_sha256": digest,
        "freeze_tag": payload.get("freeze_tag"),
        "freeze_mode": payload.get("freeze_mode"),
        "cases_completed": len(cases),
        "selected_primary": SELECTED_PRIMARY,
        "screened_primary": subset_counts.get("primary", 0),
        "funnel_feasibility_rate": (
            subset_counts.get("primary", 0) / SELECTED_PRIMARY
        ),
        "primary_executed": subset_counts.get("primary", 0),
        "development_executed": subset_counts.get("development", 0),
        "primary_in_scope": len(in_scope_primary),
        "primary_rediscovered": len(hits),
        "primary_rediscovery_rate": (
            len(hits) / len(in_scope_primary) if in_scope_primary else None
        ),
        "primary_top10": sum(
            1 for case in hits
            if isinstance(case.get("rank"), int) and case["rank"] <= 10
        ),
        "primary_strong_match": sum(
            1 for case in hits if case.get("strong_match") is True
        ),
        "status_counts": dict(status_counts),
    }

    problems = []
    if digest != EXPECTED_SHA256:
        problems.append("SHA-256 differs from the frozen result")
    if payload.get("freeze_tag") != FREEZE_TAG:
        problems.append("freeze tag differs from benchmark-freeze-v2")
    if len(cases) != EXPECTED_CASES:
        problems.append(f"expected {EXPECTED_CASES} case rows, found {len(cases)}")
    if subset_counts.get("primary", 0) != EXPECTED_PRIMARY:
        problems.append(
            f"expected {EXPECTED_PRIMARY} primary rows, "
            f"found {subset_counts.get('primary', 0)}"
        )
    if subset_counts.get("development", 0) != EXPECTED_DEVELOPMENT:
        problems.append(
            f"expected {EXPECTED_DEVELOPMENT} development rows, "
            f"found {subset_counts.get('development', 0)}"
        )

    if problems:
        return {
            **_base(
                "frozen_result_mismatch",
                "Frozen benchmark-v2 integrity check failed: " + "; ".join(problems),
            ),
            **summary,
        }

    return {
        **summary,
        "phase": "frozen_complete",
        "detail": (
            "Benchmark v2 completed as the single pre-registered run. "
            "The result is frozen and reruns are disabled."
        ),
        "complete": True,
        "frozen": True,
        "rerun_allowed": False,
        "completed_on": COMPLETED_ON,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--path", default=RESULTS_JSON)
    args = parser.parse_args()
    status = inspect_frozen_result(args.path)
    print(json.dumps(status, sort_keys=True))
    return 0 if status["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())