"""Study B trial-evidence coverage gate.

A ClinicalTrials.gov 429 storm does not fail a pool build — it silently
drops the trial term from individual candidates' composites as a coverage
gap. The resulting pool looks complete but its ranks are not comparable,
either across diseases or within the pool itself. These tests pin the
coverage accounting that gates pool finalization.
"""
import unittest

from validation.run_triage_discrimination_studyb import _trial_coverage


def _cand(basis):
    return {"score_components": {"trial_evidence_basis": basis,
                                 "trial_evidence_observed": basis == "observed"}}


class TrialCoverageTest(unittest.TestCase):

    def test_all_observed_is_full_coverage(self):
        coverage, breakdown = _trial_coverage([_cand("observed")] * 10)
        self.assertEqual(coverage, 1.0)
        self.assertIn("observed=10", breakdown)

    def test_query_failed_lowers_coverage(self):
        pool = [_cand("observed")] * 3 + [_cand("query_failed")] * 7
        coverage, breakdown = _trial_coverage(pool)
        self.assertAlmostEqual(coverage, 0.3)
        self.assertIn("query_failed=7", breakdown)

    def test_holdout_redacted_excluded_from_denominator(self):
        # Redaction is a deliberate study control, not a source failure: a
        # fully-healthy pool with redacted rows must still read as 100%.
        pool = [_cand("observed")] * 8 + [_cand("holdout_redacted")] * 2
        coverage, _ = _trial_coverage(pool)
        self.assertEqual(coverage, 1.0)

    def test_redacted_does_not_mask_real_gaps(self):
        pool = ([_cand("observed")] * 5 + [_cand("query_failed")] * 5
                + [_cand("holdout_redacted")] * 2)
        coverage, _ = _trial_coverage(pool)
        self.assertAlmostEqual(coverage, 0.5)

    def test_empty_pool_does_not_divide_by_zero(self):
        coverage, _ = _trial_coverage([])
        self.assertEqual(coverage, 1.0)

    def test_observed_flag_absent_still_counted_by_basis(self):
        # Accounting keys off trial_evidence_basis, the field the reviewer
        # always stamps; a missing boolean must not inflate coverage.
        pool = [{"score_components": {"trial_evidence_basis": "query_failed"}}]
        coverage, _ = _trial_coverage(pool)
        self.assertEqual(coverage, 0.0)

    def test_missing_score_components_counts_as_gap(self):
        coverage, _ = _trial_coverage([{}, _cand("observed")])
        self.assertAlmostEqual(coverage, 0.5)


if __name__ == "__main__":
    unittest.main()
