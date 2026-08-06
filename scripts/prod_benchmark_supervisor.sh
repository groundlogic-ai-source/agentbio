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
# NOTE: the Reserved VM filesystem is wiped on restart/redeploy. Progress lives
# in validation/v2_source_ablation_results.json (included in each publish
# snapshot) and can be pulled back to the dev workspace anytime via
# GET /internal/benchmark-results. Never republish without pulling it first.
set -u
cd "$(dirname "$0")/.." || exit 1

LOCK=/tmp/prod_benchmark_supervisor.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[supervisor] another instance holds $LOCK — exiting"
  exit 0
fi

LOG=validation/prod_benchmark.log

{
  echo "[supervisor] started $(date -u +%Y-%m-%dT%H:%M:%SZ) pid=$$"
  if git rev-parse HEAD >/dev/null 2>&1; then
    echo "[supervisor] git HEAD=$(git rev-parse --short HEAD)"
  else
    echo "[supervisor] WARNING: git unavailable — preflight freeze-tag step will fail when the control completes"
  fi
} >> "$LOG" 2>&1

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
    break
  fi
  if [ $rc -eq 2 ]; then
    echo "FREEZE VIOLATION — manual intervention required" >> "$LOG"
    break
  fi
  echo "halted rc=$rc — health-gated retry in 5 min" >> "$LOG"
  sleep 300
done
