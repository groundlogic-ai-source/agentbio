"""An empty trial registry is a coverage gap, not earned credit.

Regression cover for the post-benchmark correction of 2026-09-27. A successful
ClinicalTrials.gov query returning zero rows used to score the no-failed-trial
term a flat 1.0, so a never-attempted pair earned it for free. Combined with
dropping an unscorable Tanimoto, that renormalized still more weight onto the
free term: capivasertib/AKT2 reached 0.9539, above the frozen benchmark's best
real result, without ever having been given to a patient.
"""

import unittest

from agents.reviewer import _trial_evidence_term


class TrialEvidenceTermTest(unittest.TestCase):

    def test_no_trials_is_unobserved(self):
        """The AKT2 case: registry queried cleanly, nothing there."""
        self.assertIsNone(_trial_evidence_term({
            "trial_count": 0,
            "has_negative_repurposing_result": False,
            "query_failed": False,
        }))

    def test_trials_exist_and_none_failed_still_earns_credit(self):
        """Real credit survives: taken into humans, did not fail there."""
        self.assertIs(True, _trial_evidence_term({
            "trial_count": 4,
            "has_negative_repurposing_result": False,
            "query_failed": False,
        }))

    def test_a_negative_trial_is_still_penalised(self):
        self.assertIs(False, _trial_evidence_term({
            "trial_count": 4,
            "has_negative_repurposing_result": True,
            "query_failed": False,
        }))

    def test_query_failure_remains_unobserved(self):
        self.assertIsNone(_trial_evidence_term({
            "trial_count": 0, "query_failed": True,
        }))

    def test_holdout_redaction_remains_unobserved(self):
        self.assertIsNone(_trial_evidence_term({
            "trial_count": 0, "holdout_redacted": True,
        }))

    def test_a_negative_trial_outranks_a_missing_count(self):
        """An adverse result is never softened into a coverage gap."""
        self.assertIs(False, _trial_evidence_term({
            "trial_count": 1,
            "has_negative_repurposing_result": True,
        }))

    def test_missing_trial_count_key_is_unobserved(self):
        """Older persisted payloads carry no count; absence is not credit."""
        self.assertIsNone(_trial_evidence_term(
            {"has_negative_repurposing_result": False}))


class NoveltyIsNotPaidTwiceTest(unittest.TestCase):

    def test_an_untested_pair_does_not_outscore_a_trialled_one(self):
        """The face-validity check that prompted the fix.

        Two candidates identical but for trial history. The one nobody has
        tested must not rank above the one that reached humans and held up.
        """
        from agents.reviewer import _coverage_aware_composite

        untested, _ = _coverage_aware_composite(
            efficacy_evidence=0.922, ot_association=0.787,
            tanimoto=None, no_failed_trial=_trial_evidence_term(
                {"trial_count": 0, "has_negative_repurposing_result": False}),
        )
        trialled, _ = _coverage_aware_composite(
            efficacy_evidence=0.922, ot_association=0.787,
            tanimoto=None, no_failed_trial=_trial_evidence_term(
                {"trial_count": 6, "has_negative_repurposing_result": False}),
        )
        self.assertLess(untested, trialled)


if __name__ == "__main__":
    unittest.main()
