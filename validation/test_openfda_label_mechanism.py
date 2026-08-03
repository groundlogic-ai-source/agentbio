"""Contract tests for deterministic FDA-label mechanism extraction."""

import unittest
from unittest.mock import Mock, patch

from data_sources import openfda


class LabelMechanismTest(unittest.TestCase):
    @patch.object(openfda, "get", return_value=None)
    @patch.object(openfda, "cache_set")
    @patch.object(openfda.requests, "get")
    def test_prefers_mechanism_of_action_and_keeps_spl_provenance(
        self, request, cache_set, cache_get
    ):
        response = Mock(status_code=200)
        response.json.return_value = {
            "results": [{
                "set_id": "synthetic-spl",
                "mechanism_of_action": ["Inhibits de novo purine synthesis."],
            }]
        }
        request.return_value = response

        result = openfda.get_label_mechanism("SyntheticDrug")

        self.assertEqual(result["mechanism_text"], "Inhibits de novo purine synthesis.")
        self.assertEqual(result["label_id"], "synthetic-spl")
        self.assertEqual(result["source"], "openfda_label")
        self.assertIsNone(result["error"])
        cache_set.assert_called_once()

    @patch.object(openfda, "get", return_value=None)
    @patch.object(openfda, "cache_set")
    @patch.object(openfda.requests, "get")
    def test_missing_label_is_a_clean_empty_result(self, request, cache_set, cache_get):
        request.return_value = Mock(status_code=404)
        result = openfda.get_label_mechanism("NoLabelControl")
        self.assertEqual(result["mechanism_text"], "")
        self.assertIsNone(result["error"])


if __name__ == "__main__":
    unittest.main()