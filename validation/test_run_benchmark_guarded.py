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
    def test_refuses_before_delegating_to_watchdog(self):
        with mock.patch.object(run_benchmark_guarded, "_run_argv",
                               return_value=7) as m:
            rc = run_benchmark_guarded.main()
        self.assertEqual(rc, 2)
        m.assert_not_called()

    def test_completed_v2_supervisor_is_terminal_guard_only(self):
        script = (pathlib.Path(__file__).resolve().parent.parent
                  / "scripts" / "prod_benchmark_supervisor.sh").read_text()
        self.assertIn("validation.benchmark_v2_completion --verify", script)
        self.assertNotIn("validation.run_v2_preflight", script)
        self.assertNotIn("validation.run_benchmark_guarded", script)
        self.assertNotIn("validation.run_benchmark ", script)


if __name__ == "__main__":
    unittest.main()
