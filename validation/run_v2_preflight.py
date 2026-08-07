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
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FREEZE_TAG = "benchmark-freeze-v2"
ABLATION_RESULTS = "validation/v2_source_ablation_results.json"
SCREENED_LIST = "validation/benchmark_case_list_v2.json"
PIPELINE_DIRS = ["agents/", "data_sources/", "cache/"]
CONTROL_ROW_STATUSES = frozenset({"hit", "miss"})
# Providers under test in the ablation. A provider that is switched OFF for an
# arm MUST report "disabled" (that is the manipulation); a provider that is
# switched ON must have actually answered ("ok"/"empty").  Anything else
# ("unavailable", "parse_failed", missing) means the arm ran against a degraded
# source, so its miss/hit is not an observation about source coverage.
ABLATION_PROVIDERS = ("chembl", "gtopdb", "drugcentral")
HEALTHY_SOURCE_STATUSES = frozenset({"ok", "empty"})
# The control is only valid if every ablated source answered, so a degraded
# GtoPdb/DrugCentral would let an expensive control run finish and then be
# discarded. Probe them up front (cheap) and bound how many times we are
# willing to pay for that discovery (see ATTEMPTS_PATH).
ATTEMPTS_PATH = "validation/.v2_control_attempts"
MAX_CONTROL_DISCARDS = 3


def _log(msg: str) -> None:
    print(f"[preflight] {msg}", flush=True)


def _ablation_sources_healthy() -> bool:
    """Liveness probes for the two sources the ablation manipulates.

    Control validity requires GtoPdb and DrugCentral to have actually answered
    in the arms where they are enabled.  Probing them here turns a guaranteed
    late rejection (after a full, expensive control run) into a cheap retry.
    """
    from data_sources import drugcentral_v2, gtopdb
    probes = (
        (gtopdb, "/targets", {"accession": "P08183"}),
        (drugcentral_v2, "/act_table_full/accession/P08183", None),
    )
    for module, path, params in probes:
        try:
            data = module._get_json(path, params) if params is not None \
                else module._get_json(path)
        except Exception as exc:
            _log(f"{module.__name__.rsplit('.', 1)[-1]} probe failed: {exc}")
            return False
        if not isinstance(data, list):
            _log(f"{module.__name__.rsplit('.', 1)[-1]} probe returned no list")
            return False
    return True


def _healthy() -> bool:
    try:
        from validation.run_benchmark import _chembl_healthy
        from validation.screen_v2_cases import ot_healthy
    except ImportError:  # direct (non-package) execution
        from run_benchmark import _chembl_healthy
        from screen_v2_cases import ot_healthy
    return _chembl_healthy() and ot_healthy() and _ablation_sources_healthy()


def _discard_attempts() -> int:
    try:
        with open(ATTEMPTS_PATH, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _record_discard() -> int:
    attempts = _discard_attempts() + 1
    try:
        with open(ATTEMPTS_PATH, "w", encoding="utf-8") as f:
            f.write(str(attempts))
    except OSError:
        pass
    return attempts


def _clear_discards() -> None:
    try:
        os.remove(ATTEMPTS_PATH)
    except OSError:
        pass


# Stall watchdog for child modules.  A wedged child can stall indefinitely on
# an external call without a timeout in ROW FINALIZATION (observed 2026-08-07:
# the control froze at 38/52 for over an hour inside one arm with the
# supervisor still alive — the per-target process bound does not cover the
# post-target reviewer/matching phase).  Healthy modules emit output at least
# every per-target bound (~15 min), so 30 min of silence is definitively
# wedged: kill the whole process group and return 3 so the supervisor
# retries — the harness resumes from its last per-arm flush, losing at most
# the wedged in-flight arm.
_SILENCE_LIMIT_SECONDS = 30 * 60
_WATCHDOG_POLL_SECONDS = 30


def _run_module(module: str) -> int:
    return _run_argv([sys.executable, "-m", module])


def _forward(chunk: bytes) -> None:
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(chunk)
        buf.flush()
    else:  # exotic stdout wrappers
        sys.stdout.write(chunk.decode("utf-8", "replace"))
        sys.stdout.flush()


def _run_argv(argv) -> int:
    """Run a child with output tee'd to stdout under the stall watchdog."""
    tmp = tempfile.NamedTemporaryFile(mode="w+b", prefix="preflight_watchdog_",
                                      delete=False)
    try:
        proc = subprocess.Popen(argv, stdout=tmp, stderr=subprocess.STDOUT,
                                start_new_session=True)
        offset = 0
        last_change = time.monotonic()
        while proc.poll() is None:
            time.sleep(_WATCHDOG_POLL_SECONDS)
            size = os.fstat(tmp.fileno()).st_size
            if size > offset:
                tmp.seek(offset)
                _forward(tmp.read())
                offset = size
                last_change = time.monotonic()
            elif time.monotonic() - last_change > _SILENCE_LIMIT_SECONDS:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    proc.kill()
                proc.wait()
                _log(f"{' '.join(map(str, argv))} produced no output for "
                     f"{_SILENCE_LIMIT_SECONDS // 60} min — killed wedged "
                     "process group; exit 3 (resume replays from the last "
                     "checkpoint)")
                return 3
        tmp.seek(offset)
        _forward(tmp.read())
        return proc.returncode
    finally:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


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
        if row.get("error"):
            return False, (f"degraded control row: {key[0]} / {key[1]} "
                           f"({row.get('error')})")
        enabled = artifact_conditions.get(key[0])
        if not isinstance(enabled, list):
            return False, f"unknown control row condition: {key[0]}"
        # A terminal row is only meaningful if EVERY frozen target actually ran
        # to completion under the arm's intended source manipulation.  A row can
        # otherwise end as "miss" while individual targets errored out, which is
        # a source outage wearing the costume of a negative result.
        targets = row.get("per_target_results")
        if not isinstance(targets, list):
            return False, f"malformed per-target results: {key[0]} / {key[1]}"
        if len(targets) != len(snapshot.get("selected_rows") or []):
            return False, (f"incomplete per-target results: {key[0]} / {key[1]}")
        for rec in targets:
            if not isinstance(rec, dict):
                return False, f"malformed per-target result: {key[0]} / {key[1]}"
            symbol = rec.get("target_symbol")
            if rec.get("status") != "ok" or rec.get("error"):
                return False, (f"degraded target execution: {key[0]} / "
                               f"{key[1]} / {symbol}")
            statuses = rec.get("source_status")
            if not isinstance(statuses, dict):
                return False, (f"malformed source status: {key[0]} / "
                               f"{key[1]} / {symbol}")
            for provider in ABLATION_PROVIDERS:
                state = statuses.get(provider)
                status = (state or {}).get("status") if isinstance(state, dict) else None
                if provider in enabled:
                    if status not in HEALTHY_SOURCE_STATUSES:
                        return False, (f"degraded source {provider} "
                                       f"({status}): {key[0]} / {key[1]} / "
                                       f"{symbol}")
                elif status != "disabled":
                    # An ablated-away source that still answered would break the
                    # only manipulation this control exists to measure.
                    return False, (f"ablated source {provider} not disabled "
                                   f"({status}): {key[0]} / {key[1]} / {symbol}")
    if seen_rows != expected_rows:
        return False, "incomplete or unexpected control rows"
    return True, "complete and error-free"


def _row_defect(row, snapshot, enabled):
    """Row-level replica of the per-row checks in _valid_ablation_results().

    A defect means the row must be re-run — not that the whole control is
    unusable.  Kept deliberately separate from the validator so the strict
    first-failure reasons (pinned by tests) stay byte-identical.
    """
    if row.get("status") not in CONTROL_ROW_STATUSES:
        return "non-terminal status"
    if row.get("in_universe") is not True:
        return "out-of-universe"
    if snapshot is None or row.get("target_input_hash") != snapshot.get(
            "target_input_hash"):
        return "no matching frozen snapshot"
    if row.get("error"):
        return "row error"
    targets = row.get("per_target_results")
    if not isinstance(targets, list):
        return "malformed per-target results"
    if len(targets) != len(snapshot.get("selected_rows") or []):
        return "incomplete per-target results"
    for rec in targets:
        if not isinstance(rec, dict):
            return "malformed per-target result"
        if rec.get("status") != "ok" or rec.get("error"):
            return "degraded target execution"
        statuses = rec.get("source_status")
        if not isinstance(statuses, dict):
            return "malformed source status"
        for provider in ABLATION_PROVIDERS:
            state = statuses.get(provider)
            status = (state or {}).get("status") if isinstance(state, dict) else None
            if provider in enabled:
                if status not in HEALTHY_SOURCE_STATUSES:
                    return f"degraded source {provider}"
            elif status != "disabled":
                return f"ablated source {provider} not disabled"
    return None


def _defective_row_keys(payload, check_fingerprint=True):
    """Full scan: keys of every row that would fail _valid_ablation_results().

    Returns None when the artifact's failure is STRUCTURAL (label, fingerprint,
    condition mapping, snapshots) — then nothing row-level can be salvaged and
    the caller must discard the whole file.  Otherwise returns a (possibly
    empty) list of ((condition, case_key), defect) pairs.

    check_fingerprint=False is used ONLY by the Amendment-4 blessing path,
    which must row-verify a checkpoint whose fingerprint is the blessed prior
    one before rewriting it.
    """
    try:
        from validation import run_v2_source_ablations as ablation
    except ImportError:  # direct (non-package) execution
        import run_v2_source_ablations as ablation
    if payload.get("label") != ablation.LABEL:
        return None
    if payload.get("target_cap") != ablation.DEFAULT_TARGET_CAP:
        return None
    conditions = tuple(ablation.CONDITIONS)
    artifact_conditions = payload.get("conditions")
    if (not isinstance(artifact_conditions, dict)
            or tuple(artifact_conditions) != conditions):
        return None
    for condition in conditions:
        sources = artifact_conditions.get(condition)
        if (not isinstance(sources, list)
                or tuple(sources) != ablation.CONDITIONS[condition]):
            return None
    if check_fingerprint:
        expected_fingerprint = ablation.config_source_fingerprint(
            ablation.DEFAULT_TARGET_CAP, conditions)
        if payload.get("fingerprint") != expected_fingerprint:
            return None
    snapshots = payload.get("target_snapshots")
    rows = payload.get("rows")
    if not isinstance(snapshots, list) or not isinstance(rows, list):
        return None
    snapshot_by_key = {}
    for snapshot in snapshots:
        if (not isinstance(snapshot, dict)
                or not isinstance(snapshot.get("case_key"), str)
                or snapshot["case_key"] in snapshot_by_key):
            return None
        snapshot_by_key[snapshot["case_key"]] = snapshot
    defective = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            return None
        key = (row.get("condition"),
               ablation._case_key(row.get("drug_name", ""),
                                  row.get("disease_name", "")))
        if key in seen:
            defective.append((key, "duplicate row"))
            continue
        seen.add(key)
        enabled = artifact_conditions.get(key[0])
        if not isinstance(enabled, list):
            defective.append((key, "unknown condition"))
            continue
        defect = _row_defect(row, snapshot_by_key.get(key[1]), enabled)
        if defect:
            defective.append((key, defect))
    return defective


def _quarantine_defective_rows():
    """Strip defective rows from the control artifact, keeping healthy rows.

    Returns the number of rows removed, or 0 when the artifact is not
    quarantinable (structural failure, or nothing defective at row level).
    """
    try:
        from validation import run_v2_source_ablations as ablation
    except ImportError:  # direct (non-package) execution
        import run_v2_source_ablations as ablation
    try:
        with open(ABLATION_RESULTS, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return 0
    defective = _defective_row_keys(payload)
    if not defective:
        return 0
    bad = {key for key, _ in defective}
    kept, seen, removed = [], set(), 0
    for row in payload["rows"]:
        key = (row.get("condition"),
               ablation._case_key(row.get("drug_name", ""),
                                  row.get("disease_name", "")))
        if key in bad or key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(row)
    payload["rows"] = kept
    tmp_path = ABLATION_RESULTS + ".quarantine.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, ABLATION_RESULTS)
    except OSError:
        return 0
    return removed


# Amendment 4 (2026-08-07): data_sources/gtopdb.py now tolerates HTTP 204 on
# /ligands/{id}/structure (approved biologics — olaratumab, tositumomab,
# efgartigimod alfa — have no deposited small-molecule structure, so the 204
# is a data absence, not a source failure).  The code fix changes the control
# fingerprint, which the stale-resume guard would (correctly) treat as a new
# run, forcing a full 52-arm re-run.  Before blessing, every completed row of
# this checkpoint was re-verified defect-free under the row-level checks
# (zero degraded/error source stamps): the fix is behavior-identical for all
# completed rows, and only re-run arms observe the new behavior.  Blessing is
# a loud, one-time, row-verified fingerprint transition — NOT a weakening of
# the guard for unknown drift.
_BLESSED_FINGERPRINT_TRANSITIONS = {
    "e65a5374477e32c374381a47dfd562981b4733195c1b4c53d00e0b198d5a48b4",
}


def _bless_fingerprint_transition() -> None:
    """Apply a blessed fingerprint transition to the on-disk checkpoint.

    Verifies the checkpoint row-by-row first (any defect or structural
    problem → no blessing), then rewrites the fingerprint to the current one
    so the harness's stale-resume guard accepts the resume.  Unrecognized
    fingerprints keep the strict mismatch → discard path.
    """
    if not os.path.exists(ABLATION_RESULTS):
        return
    try:
        with open(ABLATION_RESULTS, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    stored = payload.get("fingerprint")
    if stored not in _BLESSED_FINGERPRINT_TRANSITIONS:
        return
    try:
        from validation import run_v2_source_ablations as ablation
    except ImportError:  # direct (non-package) execution
        import run_v2_source_ablations as ablation
    current = ablation.config_source_fingerprint(
        ablation.DEFAULT_TARGET_CAP, tuple(ablation.CONDITIONS))
    if stored == current:
        return
    defects = _defective_row_keys(payload, check_fingerprint=False)
    if defects is None or defects:
        _log("blessed-fingerprint candidate failed row-level verification — "
             "NOT blessing; strict discard path preserved")
        return
    payload["fingerprint"] = current
    tmp_path = ABLATION_RESULTS + ".bless.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, ABLATION_RESULTS)
    except OSError:
        return
    _log(f"Amendment 4: blessed control fingerprint transition "
         f"{stored[:12]}… → {current[:12]}… ({len(payload.get('rows') or [])} "
         "completed rows verified unaffected by the gtopdb structure-204 fix)")


def _discard_control(reason: str, context: str) -> int:
    """Quarantine degraded rows; delete only structurally unusable artifacts.

    A mid-run source flake poisons individual rows, not the whole control:
    stripping just those rows lets the resume re-run only the affected arms
    instead of repeating dozens of healthy ones (the 2026-08-06 GtoPdb /
    DrugCentral flake cost a complete 52-arm control to whole-file deletion).
    Structural failures (fingerprint drift, malformed snapshots) still discard
    everything because no row can be trusted.  Either way the attempt budget
    bounds repeated flakes and hands back to a human instead of burning the
    night on a source outage.
    """
    quarantined = _quarantine_defective_rows()
    if quarantined:
        attempts = _record_discard()
        if attempts >= MAX_CONTROL_DISCARDS:
            _log(f"source-ablation control invalid {context} ({reason}); "
                 f"{attempts} attempts — sources look persistently degraded. "
                 "Stopping instead of resuming the control; manual "
                 "intervention required")
            return 2
        _log(f"quarantined {quarantined} defective control row(s) {context} "
             f"({reason}) — attempt {attempts}/{MAX_CONTROL_DISCARDS}; healthy "
             "rows kept, exit 3 (resume re-runs only the removed arms)")
        return 3
    try:
        os.remove(ABLATION_RESULTS)
    except OSError as exc:
        _log(f"source-ablation control invalid {context} ({reason}) and could "
             f"not be discarded ({exc}) — exit 2")
        return 2
    attempts = _record_discard()
    if attempts >= MAX_CONTROL_DISCARDS:
        _log(f"source-ablation control invalid {context} ({reason}); "
             f"{attempts} discarded attempts — sources look persistently "
             "degraded. Stopping instead of re-running the control; manual "
             "intervention required")
        return 2
    _log(f"discarded invalid source-ablation control {context} ({reason}) — "
         f"attempt {attempts}/{MAX_CONTROL_DISCARDS}; exit 3 (retry cleanly "
         "when sources are healthy)")
    return 3


def main() -> int:
    # 1. Health gate — never compete cases against degraded sources.
    if not _healthy():
        _log("ChEMBL/OT/ablation sources unhealthy — exit 4 (workflow retries)")
        return 4

    # 2. Source-ablation control (pre-registered development control; the
    #    harness refuses to run post-tag, so it must finish first).
    control_ok = False
    if os.path.exists(ABLATION_RESULTS):
        _bless_fingerprint_transition()
        control_ok, reason = _valid_ablation_results()
        if not control_ok:
            # The harness deliberately flushes a resumable checkpoint after
            # every completed arm.  A structurally incomplete checkpoint is
            # normal while the same harness process is still working through
            # the 13-case suite; do not delete its completed rows merely
            # because final-only validation quite correctly refuses to freeze
            # from them.  The next preflight invocation (after the harness
            # exits) will either resume this checkpoint or reject a completed
            # but degraded artifact via the strict path below.
            if reason in {
                "incomplete or duplicate target snapshots",
                "incomplete or unexpected target snapshots",
                # Valid-but-row-incomplete is also a resume point: this is the
                # state a quarantine leaves behind after stripping degraded
                # rows — resume re-runs only the missing arms.
                "incomplete or unexpected control rows",
            }:
                _log("source-ablation checkpoint incomplete — resuming the "
                     "same control; final validation remains required before "
                     "screening or freeze")
                rc = _run_module("validation.run_v2_source_ablations")
                if rc == 2:
                    _log("ablation control refused (seal violation) — manual "
                         "intervention required")
                    return 2
                if rc != 0:
                    _log(f"ablation control rc={rc} — exit 3 (retry)")
                    return 3
                control_ok, reason = _valid_ablation_results()
                if not control_ok:
                    return _discard_control(reason, "after resumed run")
            else:
                # This file is a generated, uncommitted partial control artifact.
                # It has no analytical value once invalid, and retaining it would
                # force a stale-resume refusal. Remove it so the next healthy
                # preflight retries cleanly; never freeze from degraded control.
                return _discard_control(reason, "on disk")
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
            return _discard_control(reason, "after run")
    # A usable control exists; the outage budget is about consecutive failures.
    _clear_discards()

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
