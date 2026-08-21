"""Regression tests for the frozen benchmark-v2 completion guard."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from validation import benchmark_v2_completion as completion


class BenchmarkV2CompletionTest(unittest.TestCase):
    def test_committed_result_is_recognized_as_frozen_complete(self):
        status = completion.inspect_frozen_result()
        self.assertTrue(status["complete"])
        self.assertTrue(status["frozen"])
        self.assertFalse(status["rerun_allowed"])
        self.assertEqual(status["phase"], "frozen_complete")
        self.assertEqual(status["cases_completed"], 47)
        self.assertEqual(status["selected_primary"], 50)
        self.assertEqual(status["screened_primary"], 32)
        self.assertEqual(status["funnel_feasibility_rate"], 0.64)
        self.assertEqual(status["primary_executed"], 32)
        self.assertEqual(status["development_executed"], 15)
        self.assertEqual(status["primary_in_scope"], 22)
        self.assertEqual(status["primary_rediscovered"], 6)
        self.assertEqual(status["primary_top10"], 6)
        self.assertEqual(status["primary_strong_match"], 2)

    def test_modified_result_fails_closed(self):
        payload = json.loads(pathlib.Path(completion.RESULTS_JSON).read_text())
        payload["cases"][0]["status"] = "tampered"
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "result.json"
            path.write_text(json.dumps(payload))
            status = completion.inspect_frozen_result(str(path))
        self.assertFalse(status["complete"])
        self.assertFalse(status["rerun_allowed"])
        self.assertEqual(status["phase"], "frozen_result_mismatch")

    def test_missing_result_fails_closed(self):
        status = completion.inspect_frozen_result("/definitely/missing/result.json")
        self.assertFalse(status["complete"])
        self.assertFalse(status["rerun_allowed"])
        self.assertEqual(status["phase"], "frozen_result_missing")

    def test_direct_runner_refuses_before_pipeline_or_source_work(self):
        from validation import run_benchmark

        with mock.patch.object(run_benchmark, "_check_freeze_integrity") as freeze, \
                mock.patch.object(run_benchmark, "_health_gate") as health:
            with self.assertRaises(SystemExit) as ctx:
                run_benchmark.main()
        self.assertEqual(ctx.exception.code, 2)
        freeze.assert_not_called()
        health.assert_not_called()

    def test_preflight_refuses_before_health_or_control_work(self):
        from validation import run_v2_preflight

        with mock.patch.object(run_v2_preflight, "_healthy") as healthy, \
                mock.patch.object(run_v2_preflight, "_run_module") as run_module:
            self.assertEqual(run_v2_preflight.main(), 2)
        healthy.assert_not_called()
        run_module.assert_not_called()

    def test_api_status_and_greenlight_are_terminal(self):
        from fastapi import HTTPException
        from api import main as api_main

        status = api_main.benchmark_status()
        self.assertEqual(status["phase"], "frozen_complete")
        self.assertFalse(status["rerun_allowed"])
        self.assertEqual(
            status["frozen_completion"]["funnel_feasibility_rate"], 0.64
        )
        with self.assertRaises(HTTPException) as ctx:
            api_main.benchmark_greenlight()
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()