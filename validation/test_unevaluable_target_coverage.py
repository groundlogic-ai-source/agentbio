"""A target that was never evaluable cannot invalidate other targets' candidates.

The coverage gate is deliberately fail-closed: an unavailable SOURCE fails every
candidate, because the missing data could have changed eligibility for any of
them. That reasoning does not transfer to a TARGET that carries no UniProt
accession. Such a target is never queried, contributes no candidates, and has
nothing it could have told us about a candidate sourced from a different
target. It is a target-SELECTION gap, not a candidate-evidence gap.

Conflating them killed the best-fitting disease we had screened. A Steinert
myotonic dystrophy run produced 219 candidates across SEVEN fully-evaluated
targets -- SCN5A (94), SCN4A (56), SCN8A (49), DMPK (10), ASPH (8), ABCC9 (1),
ATP1A1 (1), every one with a resolved accession -- and terminated
source_unavailable with coverage complete for 0 of 219, because an eighth
target had no accession and produced nothing at all.

The gap is still disclosed as a warning. What changes is that it no longer
marks candidates incomplete when those candidates' own sources were fully
evaluated.
"""

import unittest

from agents.reviewer import _candidate_source_coverage

_HEALTHY = {
    name: {"status": "ok"}
    for name in ("chembl", "gtopdb", "drugcentral", "bindingdb")
}


def _target_block(symbol, uniprot, status="ok", error=None):
    block = dict(_HEALTHY)
    block["_target"] = {
        "target_symbol": symbol,
        "uniprot_id": uniprot,
        "status": status,
        "error": error,
    }
    return block


class UnevaluableTargetTest(unittest.TestCase):
    """The Steinert myotonic dystrophy shape."""

    SOURCE_STATUS = {
        "t0": _target_block("SCN5A", "Q14524"),
        "t1": _target_block(
            "", "", status="failed",
            error="Target has no UniProt accession; candidate sources were "
                  "not evaluated."),
    }

    def test_candidate_from_an_evaluated_target_stays_complete(self):
        coverage = _candidate_source_coverage(
            self.SOURCE_STATUS,
            {"target_symbol": "SCN5A", "uniprot_id": "Q14524"})
        self.assertTrue(
            coverage["complete"],
            "a candidate whose own target evaluated fully must not be failed "
            "by an unrelated target that was never evaluable")

    def test_the_gap_is_still_disclosed(self):
        """Dropped from the failure list, not from the record."""
        coverage = _candidate_source_coverage(
            self.SOURCE_STATUS,
            {"target_symbol": "SCN5A", "uniprot_id": "Q14524"})
        disclosed = json_dumps(coverage)
        self.assertIn("no UniProt accession", disclosed)

    def test_a_real_source_outage_still_fails_the_candidate(self):
        """The fail-closed posture is unchanged for actual missing data."""
        status = {
            "t0": {
                "_target": {"target_symbol": "SCN5A", "uniprot_id": "Q14524",
                            "status": "ok"},
                "chembl": {"status": "ok"},
                "gtopdb": {"status": "ok"},
                "drugcentral": {"status": "ok"},
                "bindingdb": {"status": "unavailable",
                              "error": "returned non-JSON body"},
            },
        }
        coverage = _candidate_source_coverage(
            status, {"target_symbol": "SCN5A", "uniprot_id": "Q14524"})
        self.assertFalse(coverage["complete"])

    def test_an_evaluated_target_that_failed_still_fails_candidates(self):
        """Only the NO-ACCESSION case is exempt, not every target failure."""
        status = {
            "t0": _target_block("SCN5A", "Q14524"),
            "t1": _target_block("JAK1", "P23458", status="failed",
                                error="biologist evaluation failed"),
        }
        coverage = _candidate_source_coverage(
            status, {"target_symbol": "SCN5A", "uniprot_id": "Q14524"})
        self.assertFalse(
            coverage["complete"],
            "a target that HAS an accession could have contributed, so its "
            "failure is still a coverage gap")

    def test_a_candidate_on_the_unevaluable_target_is_still_failed(self):
        """If the candidate IS attributed to it, the gap is its own."""
        status = {
            "t1": _target_block(
                "ORPHANTARGET", "", status="failed",
                error="Target has no UniProt accession; candidate sources "
                      "were not evaluated."),
        }
        coverage = _candidate_source_coverage(
            status, {"target_symbol": "ORPHANTARGET", "uniprot_id": ""})
        self.assertFalse(coverage["complete"])


def json_dumps(value):
    import json
    return json.dumps(value, default=str)


class GraphGateAgreesWithReviewerTest(unittest.TestCase):
    """Both gates must exempt the same case, or the fix is invisible.

    The reviewer's per-candidate coverage and the graph's eligibility gate read
    DIFFERENT paths: candidate_source_coverage versus
    chemist_output.source_status. Fixing only the reviewer produced a run that
    reported coverage complete for 219 of 219 and still terminated
    source_unavailable, because the graph gate counted the same unevaluable
    target as a global provider failure.
    """

    def test_graph_gate_ignores_a_target_with_no_accession(self):
        from main_graph import _source_failure_details
        status = {
            "t0": {
                "_target": {"target_symbol": "SCN5A", "uniprot_id": "Q14524",
                            "status": "ok"},
                "chembl": {"status": "ok"},
                "bindingdb": {"status": "ok"},
            },
            "t1": {
                "_target": {"target_symbol": "", "uniprot_id": "",
                            "status": "failed"},
                "chembl": {"status": "unavailable"},
                "bindingdb": {"status": "unavailable"},
            },
        }
        self.assertEqual(_source_failure_details(status), [])

    def test_graph_gate_still_reports_a_real_outage(self):
        from main_graph import _source_failure_details
        status = {
            "t0": {
                "_target": {"target_symbol": "SCN5A", "uniprot_id": "Q14524",
                            "status": "ok"},
                "chembl": {"status": "unavailable",
                           "error": "ChEMBL returned HTTP 503"},
            },
        }
        self.assertTrue(_source_failure_details(status))


if __name__ == "__main__":
    unittest.main()
