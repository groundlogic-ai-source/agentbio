#!/usr/bin/env bash
# 24/7 Study B supervisor for the Reserved VM deployment.
#
# Mirrors scripts/prod_benchmark_supervisor.sh: the dev workflow only advances
# while the workspace is awake; this supervisor is started by the production
# run command so the triage-discrimination Study B pool rebuild progresses
# independently of the dev tab.
#
# Persistence rules (Reserved VM disk is wiped on restart/redeploy):
#   - Progress lives in validation/triage_discrimination_studyb_checkpoint.jsonl
#     and is pulled back to dev via GET /internal/studyb-checkpoint BEFORE any
#     republish; the next publish snapshot then resumes from it.
#   - validation/.prod_studyb_freeze_head pins the commit the run started on.
#     It is committed alongside the checkpoint on pull-back, so a redeploy
#     whose pipeline code moved on is REFUSED here instead of silently merging
#     new code into a supposedly frozen run.
set -u
cd "$(dirname "$0")/.." || exit 1

LOCK=/tmp/prod_studyb_supervisor.lock
DONE=validation/.prod_studyb_done
HEAD_FILE=validation/.prod_studyb_freeze_head
RESULTS=validation/triage_discrimination_studyb_results.json
LOG=validation/prod_studyb.log

# Terminal state from a previous boot: do not restart the chain.
if [ -f "$DONE" ]; then
  echo "[studyb-supervisor] terminal marker $DONE present ($(cat "$DONE" 2>/dev/null)) — nothing to do"
  exit 0
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[studyb-supervisor] another instance holds $LOCK — exiting"
  exit 0
fi

{
  echo "[studyb-supervisor] started $(date -u +%Y-%m-%dT%H:%M:%SZ) pid=$$"
  if git rev-parse HEAD >/dev/null 2>&1; then
    echo "[studyb-supervisor] git HEAD=$(git rev-parse --short HEAD)"
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
      echo "[studyb-supervisor] FREEZE VIOLATION: checkpoint pinned to $pinned but HEAD is $head_sha — refusing to resume; manual intervention required" >> "$LOG"
      echo "freeze_violation_head_mismatch" > "$DONE"
      exit 2
    fi
  else
    echo "$head_sha" > "$HEAD_FILE"
  fi
fi

while true; do
  # The runner is fail-closed: it refuses to start when results already exist
  # ("amend, never regenerate"), so a completed run exits 1 with that message.
  if [ -f "$RESULTS" ]; then
    echo "STUDY B COMPLETE" >> "$LOG"
    echo "complete" > "$DONE"
    break
  fi
  # Prefetch resilience: fewer concurrent lane workers (5 lanes x 8 workers
  # = 40 in-flight calls reproducibly wedges this VM's egress), and a
  # 15-min zero-progress stall budget — on stall the runner self-terminates
  # and this loop restarts it; completed lane calls are cached, so each
  # retry resumes where the last one froze instead of starting over.
  # nice -19: on the 1-vCPU reserved VM the prefetch/scoring phases saturate
  # every hardware thread and starve uvicorn, whose health-check failures make
  # the LB pull the backend (prod goes dark while the study is fine). Idle
  # priority keeps the API responsive at zero throughput cost when idle.
  AGENTBIO_PREFETCH_WORKERS=2 \
  AGENTBIO_PREFETCH_STALL_EXIT_SECONDS=900 \
    nice -n 19 python3 -m validation.run_triage_discrimination_studyb >> "$LOG" 2>&1
  rc=$?
  if [ -f "$RESULTS" ]; then
    echo "STUDY B COMPLETE" >> "$LOG"
    echo "complete" > "$DONE"
    break
  fi
  # Freeze violation is a hard stop — never auto-retry a frozen-study breach.
  if tail -n 50 "$LOG" | grep -q "FREEZE VIOLATION"; then
    echo "FREEZE VIOLATION — manual intervention required" >> "$LOG"
    echo "freeze_violation" > "$DONE"
    break
  fi
  # Anything else (health-gate refusal, transient API failure, crash) is
  # retried: the runner is checkpoint-resumable, so retries are cheap.
  echo "halted rc=$rc — health-gated retry in 5 min" >> "$LOG"
  sleep 300
done
