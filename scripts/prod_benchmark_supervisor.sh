#!/usr/bin/env bash
# 24/7 v2 benchmark supervisor for the Reserved VM deployment.
#
# Mirrors the dev `benchmark-run` workflow's retry semantics:
#   preflight -> (only when ready) benchmark -> retry on health gates,
#   stop cleanly on completion (0) or freeze violation (2).
#
# The dev workflow only advances while the workspace is awake; this supervisor
# is started by the production run command so the benchmark progresses
# independently of the dev tab.
#
# Persistence rules (Reserved VM disk is wiped on restart/redeploy):
#   - Progress lives in validation/v2_source_ablation_results.json and is
#     pulled back to dev via GET /internal/benchmark-results BEFORE any
#     republish; the next publish snapshot then resumes from it.
#   - validation/.prod_freeze_head pins the commit the run started on. It is
#     committed alongside the checkpoint on pull-back, so a redeploy whose
#     pipeline code moved on is REFUSED here instead of silently merging new
#     code into a supposedly frozen run.
set -u
cd "$(dirname "$0")/.." || exit 1

LOCK=/tmp/prod_benchmark_supervisor.lock
DONE=validation/.prod_benchmark_done
HEAD_FILE=validation/.prod_freeze_head
LOG=validation/prod_benchmark.log

# Terminal state from a previous boot: do not restart the chain.
if [ -f "$DONE" ]; then
  echo "[supervisor] terminal marker $DONE present ($(cat "$DONE" 2>/dev/null)) — nothing to do"
  exit 0
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[supervisor] another instance holds $LOCK — exiting"
  exit 0
fi

{
  echo "[supervisor] started $(date -u +%Y-%m-%dT%H:%M:%SZ) pid=$$"
  if git rev-parse HEAD >/dev/null 2>&1; then
    echo "[supervisor] git HEAD=$(git rev-parse --short HEAD)"
  else
    echo "[supervisor] WARNING: git unavailable — preflight freeze-tag step will fail when the control completes"
  fi
} >> "$LOG" 2>&1

# Freeze-integrity guard: the checkpoint must continue on the SAME commit it
# started on. The head file rides with the checkpoint through pull-back and
# publish snapshots, so this catches "redeployed newer code onto an old run".
if git rev-parse HEAD >/dev/null 2>&1; then
  head_sha=$(git rev-parse HEAD)
  if [ -f "$HEAD_FILE" ]; then
    pinned=$(cat "$HEAD_FILE" 2>/dev/null || true)
    if [ -n "$pinned" ] && [ "$pinned" != "$head_sha" ]; then
      echo "[supervisor] FREEZE VIOLATION: checkpoint pinned to $pinned but HEAD is $head_sha — refusing to resume; manual intervention required" >> "$LOG"
      echo "freeze_violation_head_mismatch" > "$DONE"
      exit 2
    fi
  else
    echo "$head_sha" > "$HEAD_FILE"
  fi
fi

while true; do
  python3 -m validation.run_v2_preflight >> "$LOG" 2>&1
  pf=$?
  if [ $pf -eq 0 ]; then
    python3 -m validation.run_benchmark >> "$LOG" 2>&1
    rc=$?
  else
    rc=$pf
  fi
  if [ $rc -eq 0 ]; then
    echo "BENCHMARK COMPLETE" >> "$LOG"
    echo "complete" > "$DONE"
    break
  fi
  if [ $rc -eq 2 ]; then
    echo "FREEZE VIOLATION — manual intervention required" >> "$LOG"
    echo "freeze_violation" > "$DONE"
    break
  fi
  echo "halted rc=$rc — health-gated retry in 5 min" >> "$LOG"
  sleep 300
done
