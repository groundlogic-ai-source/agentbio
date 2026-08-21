#!/usr/bin/env bash
# Terminal guard for the completed, one-shot benchmark v2.
#
# Benchmark v2 completed on 2026-08-09.  Its frozen result is committed and the
# preregistration permits no reroll.  This former supervisor now does exactly
# one thing in production: verify the frozen result and refuse to launch any
# preflight or benchmark process.
set -u
cd "$(dirname "$0")/.." || exit 1

DONE=validation/.prod_benchmark_done
LOG=validation/prod_benchmark.log

if python3 -m validation.benchmark_v2_completion --verify >> "$LOG" 2>&1; then
  echo "complete_frozen_one_shot" > "$DONE"
  echo "[benchmark-v2] frozen completion verified; rerun disabled" >> "$LOG"
  exit 0
fi

echo "frozen_result_missing_or_mismatch" > "$DONE"
echo "[benchmark-v2] FROZEN RESULT INTEGRITY FAILURE — rerun remains disabled; manual provenance review required" >> "$LOG"
exit 2
