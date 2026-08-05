"""
Benchmark v2 preflight — everything that must be true BEFORE the one v2 run
starts (Amendment 3, item 20). Idempotent; safe to invoke on every retry.

Chain, in order:
  1. Source health probes (ChEMBL + Open Targets) — exit 4 so the workflow
     retries instead of competing cases against a degraded API.
  2. Source-ablation control results exist. The ablation harness HARD-REFUSES
     to run once `benchmark-freeze-v2` exists, so this MUST complete before
     step 4. Runs the harness (one-time) if results are missing.
  3. Amendment-1 screened case list exists (runs screen_v2_cases if missing;
     its exit 3/2 propagate).
  4. `benchmark-freeze-v2` tag exists — created at HEAD only after steps 1-3
     pass and the pipeline dirs are clean.

Usage:
    python3 -m validation.run_v2_preflight

Exit codes: 0 = ready; 2 = manual intervention; 3 = data unavailable (retry);
            4 = source unhealthy (retry).
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FREEZE_TAG = "benchmark-freeze-v2"
ABLATION_RESULTS = "validation/v2_source_ablation_results.json"
SCREENED_LIST = "validation/benchmark_case_list_v2.json"
PIPELINE_DIRS = ["agents/", "data_sources/", "cache/"]


def _log(msg: str) -> None:
    print(f"[preflight] {msg}", flush=True)


def _healthy() -> bool:
    try:
        from validation.run_benchmark import _chembl_healthy
        from validation.screen_v2_cases import ot_healthy
    except ImportError:  # direct (non-package) execution
        from run_benchmark import _chembl_healthy
        from screen_v2_cases import ot_healthy
    return _chembl_healthy() and ot_healthy()


def _run_module(module: str) -> int:
    return subprocess.call([sys.executable, "-m", module])


def main() -> int:
    # 1. Health gate — never compete cases against degraded sources.
    if not _healthy():
        _log("ChEMBL/OT unhealthy — exit 4 (workflow retries)")
        return 4

    # 2. Source-ablation control (pre-registered development control; the
    #    harness refuses to run post-tag, so it must finish first).
    if not os.path.exists(ABLATION_RESULTS):
        _log("source-ablation control results missing — running one-time "
             "(label source_ablation_control, NOT benchmark v2)")
        rc = _run_module("validation.run_v2_source_ablations")
        if rc == 2:
            _log("ablation control refused (seal violation) — manual "
                 "intervention required")
            return 2
        if rc != 0 or not os.path.exists(ABLATION_RESULTS):
            _log(f"ablation control rc={rc} — exit 3 (retry)")
            return 3

    # 3. Amendment-1 screened case list.
    if not os.path.exists(SCREENED_LIST):
        _log("screened case list missing — running Amendment-1 screen")
        rc = _run_module("validation.screen_v2_cases")
        if rc != 0:
            return 2 if rc == 2 else 3

    # 4. Freeze tag — only now, at a clean HEAD.
    have_tag = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{FREEZE_TAG}"],
        capture_output=True).returncode == 0
    if have_tag:
        head = subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        tagged = subprocess.run(
            ["git", "rev-parse", f"{FREEZE_TAG}^{{commit}}"],
            capture_output=True, text=True).stdout.strip()
        if head != tagged:
            _log(f"{FREEZE_TAG} points at {tagged[:8]} but HEAD is "
                 f"{head[:8]} — the frozen run must execute at the tagged "
                 "commit; manual intervention required")
            return 2
    if not have_tag:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--"] + PIPELINE_DIRS,
            capture_output=True, text=True).stdout.strip()
        if dirty:
            _log(f"pipeline dirs dirty — refusing to tag:\n{dirty}")
            return 2
        subprocess.check_call(["git", "tag", FREEZE_TAG, "HEAD"])
        _log(f"created freeze tag {FREEZE_TAG} at HEAD")

    _log("READY — benchmark v2 may start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
