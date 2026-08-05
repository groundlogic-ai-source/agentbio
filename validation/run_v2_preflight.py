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
import json
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FREEZE_TAG = "benchmark-freeze-v2"
ABLATION_RESULTS = "validation/v2_source_ablation_results.json"
SCREENED_LIST = "validation/benchmark_case_list_v2.json"
PIPELINE_DIRS = ["agents/", "data_sources/", "cache/"]
CONTROL_ROW_STATUSES = frozenset({"hit", "miss"})


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


def _valid_ablation_results(path: str = ABLATION_RESULTS) -> tuple[bool, str]:
    """Require a complete, error-free default control before freeze.

    The control harness flushes incrementally so a file existing is not proof
    that it is usable.  In particular, source outages can leave a full set of
    persisted error rows that must never be mistaken for a completed control.
    """
    try:
        from validation import run_v2_source_ablations as ablation
    except ImportError:  # direct (non-package) execution
        import run_v2_source_ablations as ablation
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"unreadable control artifact: {exc}"

    conditions = tuple(ablation.CONDITIONS)
    expected_cases = [
        (drug, disease) for _, drug, disease, _ in ablation.TARGET_CASES
    ]
    expected_snapshots = {
        ablation._case_key(drug, disease) for drug, disease in expected_cases
    }
    expected_rows = {
        (condition, ablation._case_key(drug, disease))
        for condition in conditions for drug, disease in expected_cases
    }
    if payload.get("label") != ablation.LABEL:
        return False, "wrong control label"
    if payload.get("target_cap") != ablation.DEFAULT_TARGET_CAP:
        return False, "unexpected target cap"
    artifact_conditions = payload.get("conditions")
    if not isinstance(artifact_conditions, dict):
        return False, "malformed condition mapping"
    if tuple(artifact_conditions) != conditions:
        return False, "unexpected condition set"
    for condition in conditions:
        sources = artifact_conditions.get(condition)
        if not isinstance(sources, list) or tuple(sources) != ablation.CONDITIONS[condition]:
            return False, f"unexpected sources for condition: {condition}"
    expected_fingerprint = ablation.config_source_fingerprint(
        ablation.DEFAULT_TARGET_CAP, conditions)
    if payload.get("fingerprint") != expected_fingerprint:
        return False, "stale control fingerprint"

    snapshots = payload.get("target_snapshots")
    rows = payload.get("rows")
    if not isinstance(snapshots, list) or not isinstance(rows, list):
        return False, "missing snapshots or rows"
    if len(snapshots) != len(expected_snapshots):
        return False, "incomplete or duplicate target snapshots"
    snapshot_by_key = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            return False, "malformed target-selection snapshot"
        key = snapshot.get("case_key")
        if not isinstance(key, str) or not key:
            return False, "target-selection snapshot missing case key"
        if key in snapshot_by_key:
            return False, f"duplicate target-selection snapshot: {key}"
        snapshot_by_key[key] = snapshot
    if set(snapshot_by_key) != expected_snapshots:
        return False, "incomplete or unexpected target snapshots"
    for key, snapshot in snapshot_by_key.items():
        # The pre-registered control suite is deliberately made of fixed,
        # in-universe small-molecule cases.  Any other selection result is
        # degraded/incomplete control output, not a scored observation.
        if snapshot.get("status") != "ok" or snapshot.get("in_universe") is not True:
            return False, f"failed target-selection snapshot: {key}"
        try:
            ablation.validate_snapshot(snapshot, ablation.DEFAULT_TARGET_CAP)
        except RuntimeError as exc:
            return False, f"invalid target-selection snapshot: {exc}"

    seen_rows = set()
    for row in rows:
        if not isinstance(row, dict):
            return False, "malformed control row"
        key = (row.get("condition"),
               ablation._case_key(row.get("drug_name", ""),
                                  row.get("disease_name", "")))
        if key in seen_rows:
            return False, "duplicate control row"
        seen_rows.add(key)
        if row.get("status") not in CONTROL_ROW_STATUSES:
            return False, f"non-terminal control row: {key[0]} / {key[1]}"
        if row.get("in_universe") is not True:
            return False, f"out-of-universe control row: {key[0]} / {key[1]}"
        snapshot = snapshot_by_key.get(key[1])
        if snapshot is None or row.get("target_input_hash") != snapshot.get("target_input_hash"):
            return False, "row does not match frozen target-selection snapshot"
    if seen_rows != expected_rows:
        return False, "incomplete or unexpected control rows"
    return True, "complete and error-free"


def main() -> int:
    # 1. Health gate — never compete cases against degraded sources.
    if not _healthy():
        _log("ChEMBL/OT unhealthy — exit 4 (workflow retries)")
        return 4

    # 2. Source-ablation control (pre-registered development control; the
    #    harness refuses to run post-tag, so it must finish first).
    control_ok = False
    if os.path.exists(ABLATION_RESULTS):
        control_ok, reason = _valid_ablation_results()
        if not control_ok:
            # This file is a generated, uncommitted partial control artifact.
            # It has no analytical value once invalid, and retaining it would
            # force a stale-resume refusal. Remove it so the next healthy
            # preflight retries cleanly; never freeze from degraded control.
            try:
                os.remove(ABLATION_RESULTS)
            except OSError as exc:
                _log(f"source-ablation control invalid ({reason}) and could "
                     f"not be discarded ({exc}) — exit 2")
                return 2
            _log(f"discarded invalid source-ablation control ({reason}) — "
                 "exit 3 (retry cleanly when sources are healthy)")
            return 3
    if not control_ok:
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
        control_ok, reason = _valid_ablation_results()
        if not control_ok:
            try:
                os.remove(ABLATION_RESULTS)
            except OSError as exc:
                _log(f"source-ablation control invalid after run ({reason}) "
                     f"and could not be discarded ({exc}) — exit 2")
                return 2
            _log(f"source-ablation control invalid after run ({reason}) — "
                 "discarded; exit 3 (retry cleanly)")
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
