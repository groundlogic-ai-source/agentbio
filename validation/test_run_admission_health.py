"""Mocked tests for required-source admission before graph execution."""

import unittest
from unittest import mock

from api import main
from data_sources import source_health


class RunAdmissionHealthTests(unittest.TestCase):
    def test_unhealthy_admission_persists_retryable_terminal_without_graph(self):
        health = {
            "healthy": False,
            "cached": False,
            "sources": {
                "chembl": {"available": False, "error": "HTTPError: 503"},
                "ncbi_pubmed": {"available": True, "error": None},
            },
        }
        with mock.patch.object(main, "probe_required_sources",
                               return_value=health), \
             mock.patch.object(main, "build_graph") as build_graph, \
             mock.patch.object(main.jobs_db, "update_job_status") as update:
            main._run_graph("job-1", "thread-1")

        build_graph.assert_not_called()
        terminal = update.call_args_list[-1]
        self.assertEqual(terminal.kwargs["status"], "source_unavailable")
        self.assertEqual(terminal.kwargs["current_stage"], "done")
        self.assertIn("retryable", terminal.kwargs["error_message"])
        self.assertIn("chembl", terminal.kwargs["error_message"])

    def test_healthy_admission_proceeds_to_graph(self):
        graph = mock.Mock()
        graph.stream.return_value = []
        health = {
            "healthy": True,
            "cached": False,
            "sources": {
                "chembl": {"available": True, "error": None},
                "ncbi_pubmed": {"available": True, "error": None},
            },
        }
        with mock.patch.object(main, "probe_required_sources",
                               return_value=health), \
             mock.patch.object(main, "build_graph", return_value=graph) as build, \
             mock.patch.object(main.jobs_db, "get_job",
                               return_value={"disease_name": "Disease"}), \
             mock.patch.object(main.jobs_db, "update_job_status") as update:
            main._run_graph("job-2", "thread-2")

        build.assert_called_once_with()
        graph.stream.assert_called_once()
        self.assertEqual(update.call_args_list[-1].kwargs["status"], "completed")


class RequiredSourceProbeTests(unittest.TestCase):
    def setUp(self):
        source_health._reset_for_tests()

    def test_probe_uses_both_policies_and_caches_only_success(self):
        chembl_response = mock.Mock()
        chembl_response.json.return_value = {"status": "UP"}
        ncbi_response = mock.Mock()
        ncbi_response.json.return_value = {"einforesult": {"dbinfo": [{}]}}
        with mock.patch.object(source_health, "provider_request",
                               side_effect=[chembl_response, ncbi_response]) as request:
            first = source_health.probe_required_sources()
            second = source_health.probe_required_sources()
        self.assertTrue(first["healthy"])
        self.assertTrue(second["cached"])
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            ["chembl", "ncbi"],
        )

    def test_failed_probe_is_not_cached_as_success(self):
        ncbi_response = mock.Mock()
        ncbi_response.json.return_value = {"einforesult": {"dbinfo": [{}]}}
        chembl_response = mock.Mock()
        chembl_response.json.return_value = {"status": "UP"}
        with mock.patch.object(
            source_health,
            "provider_request",
            side_effect=[
                RuntimeError("down"), ncbi_response,
                chembl_response, ncbi_response,
            ],
        ) as request:
            first = source_health.probe_required_sources()
            second = source_health.probe_required_sources()
        self.assertFalse(first["healthy"])
        self.assertTrue(second["healthy"])
        self.assertEqual(request.call_count, 4)


if __name__ == "__main__":
    unittest.main()