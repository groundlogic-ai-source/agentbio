#!/usr/bin/env bash
# 24/7 Study C (triage discrimination: confirmed positives vs genuine
# negatives) supervisor for the RESERVED-VM deployment.
#
# Mirrors scripts/prod_studyb_supervisor.sh conventions:
#   - Progress lives in validation/triage_discrimination_studyc_checkpoint.jsonl
#     and is pulled back to dev via GET /internal/studyc-checkpoint BEFORE any
#     republish; the next publish snapshot then resumes from it.
#   - The freeze manifest (validation/triage_discrimination_studyc_freeze_manifest.json)
#     is committed BEFORE publish. On prod, git is unavailable in the
#     deployment snapshot, so the runner's commit-pin check fails open there
#     by design; the cases/rule-fingerprint hash checks still run.
set -u
cd "$(dirname "$0")/.." || exit 1

LOCK=/tmp/prod_studyc_supervisor.lock
DONE=validation/.prod_studyc_done
RESULTS=validation/triage_discrimination_studyc_results.json
LOG=validation/prod_studyc.log

# Terminal state from a previous boot: do not restart the chain.
if [ -f "$DONE" ]; then
  echo "[studyc-supervisor] terminal marker $DONE present ($(cat "$DONE" 2>/dev/null)) — nothing to do"
  exit 0
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[studyc-supervisor] another instance holds $LOCK — exiting"
  exit 0
fi

{
  echo "[studyc-supervisor] started $(date -u +%Y-%m-%dT%H:%M:%SZ) pid=$$"
  if git rev-parse HEAD >/dev/null 2>&1; then
    echo "[studyc-supervisor] git HEAD=$(git rev-parse --short HEAD)"
  fi
} >> "$LOG" 2>&1

while true; do
  # The runner is fail-closed: it refuses to start when results already exist
  # ("amend, never regenerate"), so a completed run exits 1 with that message.
  if [ -f "$RESULTS" ]; then
    echo "STUDY C COMPLETE" >> "$LOG"
    echo "complete" > "$DONE"
    break
  fi
  # Same resource discipline as Study B on the 1-vCPU VM: nice -19 so uvicorn
  # always wins CPU contention (health-check starvation pulls the backend),
  # reduced prefetch workers to keep egress unwedged, and a 15-min
  # zero-progress stall budget so the runner self-terminates and this loop
  # resumes it from the checkpoint.
  AGENTBIO_PREFETCH_WORKERS=2 \
  AGENTBIO_PREFETCH_STALL_EXIT_SECONDS=900 \
    nice -n 19 python3 -m validation.run_triage_discrimination_studyc >> "$LOG" 2>&1
  rc=$?
  if [ -f "$RESULTS" ]; then
    echo "STUDY C COMPLETE" >> "$LOG"
    echo "complete" > "$DONE"
    break
  fi
  # Freeze violation is a hard stop — never auto-retry a frozen-study breach.
  if tail -n 50 "$LOG" | grep -q "FREEZE VIOLATION"; then
    echo "FREEZE VIOLATION — manual intervention required" >> "$LOG"
    echo "freeze_violation" > "$DONE"
    break
  fi
  # Study B results gate: they are committed in the repo, so on prod this
  # should never fire; if it does, retrying is pointless until a republish.
  if tail -n 50 "$LOG" | grep -q "REFUSED: Study B results"; then
    echo "Study B results gate fired — waiting for a publish that carries them" >> "$LOG"
    sleep 3600
    continue
  fi
  # Anything else (health-gate refusal, transient API failure, crash) is
  # retried: the runner is checkpoint-resumable, so retries are cheap.
  echo "halted rc=$rc — health-gated retry in 5 min" >> "$LOG"
  sleep 300
done
