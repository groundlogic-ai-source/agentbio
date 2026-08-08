"""Regression tests for the guarded benchmark runner wiring.

The 2026-08-07 prod wedge (benchmark silent 11+ h at case 1 target 2/3 with
the supervisor alive) happened because the supervisor invoked run_benchmark
directly, outside the preflight stall watchdog.  These tests pin the wiring
so the guard cannot be silently lost again.

Convention: unittest-only (no pytest in this environment).
"""
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from validation import run_benchmark_guarded


class RunBenchmarkGuardedTest(unittest.TestCase):
    def test_delegates_to_watchdog_with_benchmark_module(self):
        with mock.patch.object(run_benchmark_guarded, "_run_argv",
                               return_value=7) as m:
            rc = run_benchmark_guarded.main()
        self.assertEqual(rc, 7)
        argv = m.call_args.args[0]
        self.assertEqual(argv[-2:], ["-m", "validation.run_benchmark"])

    def test_supervisor_invokes_guarded_runner(self):
        script = (pathlib.Path(__file__).resolve().parent.parent
                  / "scripts" / "prod_benchmark_supervisor.sh").read_text()
        self.assertIn("python3 -m validation.run_benchmark_guarded", script)
        self.assertNotIn("python3 -m validation.run_benchmark >>", script)


if __name__ == "__main__":
    unittest.main()
