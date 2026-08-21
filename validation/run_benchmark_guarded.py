"""Retired guarded runner for the completed, one-shot benchmark v2.

Benchmark v2 completed on 2026-08-09. This wrapper now refuses before starting
the child process. The historical watchdog implementation is described below
for provenance, but no second benchmark process may be launched.

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
from validation.benchmark_v2_completion import inspect_frozen_result


def main() -> int:
    frozen = inspect_frozen_result()
    print(
        "REFUSED: benchmark v2 is frozen and complete "
        f"({frozen['completed_on']}; phase={frozen['phase']}).",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
