"""
Guarded v2 benchmark runner — runs the benchmark under the same 30-minute
output-silence watchdog that already protects the preflight children
(control + screen).

Why this exists: the production supervisor invoked ``run_benchmark``
directly, so unlike the preflight-run control/screen children the benchmark
process had NO stall protection.  Observed 2026-08-07: the benchmark wedged
at case 1, target 2/3, with 11+ hours of log silence while the supervisor
stayed alive — the same row-finalization wedge class that froze the control
at 38/52, one link down the chain.

With this wrapper the wedged process group is SIGKILLed after 30 min of
output silence and the wrapper exits 3, so the supervisor's retry loop
relaunches the benchmark — ``run_benchmark`` resumes the single run from its
last per-case flush and completed cases are never re-executed.

Usage:
    python3 -m validation.run_benchmark_guarded
"""
import sys

from validation.run_v2_preflight import _run_argv


def main() -> int:
    return _run_argv([sys.executable, "-m", "validation.run_benchmark"])


if __name__ == "__main__":
    sys.exit(main())
