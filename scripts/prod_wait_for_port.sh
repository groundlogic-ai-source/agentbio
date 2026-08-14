#!/usr/bin/env bash
# Block until the API server is accepting connections before starting the
# heavy study supervisors.
#
# Why: the api-server production run command launches the benchmark and
# Study B supervisor loops in the background and then execs uvicorn. On a
# fresh Reserved VM disk (cold cache) the Study B runner immediately starts
# prefetch work that competes with uvicorn's ~15-40s import; on the small VM
# that pushed boot past the orchestrator's ~70s "port must open" budget, so
# the artifact was SIGKILLed and boot-looped forever ("a port configuration
# was specified but the required port was never opened").
#
# Gating the supervisors on the port guarantees uvicorn gets the whole
# machine until it is serving. The 10-minute cap is a deliberate escape
# hatch: even if the API never comes up, the study still runs (checkpoint-
# resumable), because the study is the reason this VM exists.
set -u

port="${1:-${PORT:-8000}}"

for _ in $(seq 1 200); do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    exec 3>&- 3<&-
    exit 0
  fi
  sleep 3
done

echo "[wait-for-port] 127.0.0.1:$port never opened after 600s — starting supervisors anyway" >&2
exit 0
