"""A disease nobody is working on is the only place the conditional holds.

Six flagship hunts cleared every drug-level gate and died anyway. Myotonic
dystrophy type 1 is the clean case: lacosamide had zero registered trials in
myotonia and no prior-art hit, so the pair looked unclaimed -- while the
DISEASE carried 136 registered interventional trials, 44 open, and a
RECRUITING phase 3 of mexiletine PR sponsored by Lupin Ltd. (NCT06523400).

AgentBio's reason to exist is the conditional "if AgentBio did not exist,
those patients would have suffered". A funded clinical team standing on the
same ground falsifies it regardless of how good the candidate looks. That is a
disease-level fact, costs a handful of count queries, and was knowable before
any run was spent.

These tests pin the two ways the check can lie:

  1. A zero obtained from the wrong disease name. "Steinert myotonic
     dystrophy" really returns 69 trials where "Myotonic dystrophy type 1"
     returns 136 -- a 2x undercount from the Orphanet label alone. The same
     string difference already produced this repo's worst false negative, a
     NO_PRIOR_ART_FOUND on a drug with a published RCT.
  2. A zero obtained from a provider that did not answer. This codebase has
     removed that error from the trial registry, Tanimoto, GtoPdb, ChEMBL, the
     literature gate, Ensembl, the circuit breaker and the prior-art gate. A
     module whose entire output is "nobody is here" must not reintroduce it.
"""

import unittest
from unittest import mock

from data_sources import research_attention as ra


class DormancyRequiresAnAnswerTest(unittest.TestCase):
    """Unknown is not dormant. This is the whole discipline."""

    def test_registry_outage_is_not_zero_trials(self):
        with mock.patch.object(ra, "_ctgov_count", return_value=None), \
             mock.patch.object(ra, "search_names", return_value=["X disease"]):
            result = ra.trial_attention("X disease")
        self.assertFalse(result["assessed"])
        self.assertNotIn("n_interventional", result)
        self.assertIn("not a trial count of zero", result["reason"])

    def test_unanswerable_disease_gets_its_own_verdict(self):
        with mock.patch.object(ra, "_ctgov_count", return_value=None), \
             mock.patch.object(ra, "_epmc_count", return_value=None), \
             mock.patch.object(ra, "search_names", return_value=["X disease"]):
            envelope = ra.attention_verdict("X disease")
        self.assertEqual(envelope["verdict"], ra.VERDICT_UNKNOWN)
        self.assertNotEqual(envelope["verdict"], ra.VERDICT_DORMANT)

    def test_an_outage_is_not_cached(self):
        """A cached outage would freeze a transient 429 into a verdict."""
        with mock.patch.object(ra, "_ctgov_count", return_value=None), \
             mock.patch.object(ra, "_epmc_count", return_value=None), \
             mock.patch.object(ra, "search_names", return_value=["X disease"]), \
             mock.patch.object(ra, "cache_set") as store:
            ra.attention_verdict("X disease")
        store.assert_not_called()

    def test_literature_outage_does_not_block_a_trial_verdict(self):
        """Europe PMC is the secondary axis; losing it must not lose the primary."""
        with mock.patch.object(ra, "_ctgov_count", return_value=0), \
             mock.patch.object(ra, "_epmc_count", return_value=None), \
             mock.patch.object(ra, "search_names", return_value=["X disease"]), \
             mock.patch.object(ra, "get", return_value=None), \
             mock.patch.object(ra, "cache_set"):
            envelope = ra.attention_verdict("X disease")
        self.assertEqual(envelope["verdict"], ra.VERDICT_DORMANT)


class SynonymsDecideTheAnswerTest(unittest.TestCase):

    def test_the_largest_count_across_names_wins(self):
        """69 on the Orphanet label, 136 on the canonical name -> 136."""
        counts = {"Steinert myotonic dystrophy": 69,
                  "myotonic dystrophy type 1": 136}
        with mock.patch.object(
            ra, "search_names",
            return_value=list(counts)), \
             mock.patch.object(
                 ra, "_ctgov_count",
                 side_effect=lambda params: counts[params["query.cond"]]):
            result = ra.trial_attention("Steinert myotonic dystrophy")
        self.assertEqual(result["n_interventional"], 136)

    def test_one_dead_name_does_not_drag_the_count_down(self):
        """A name the registry cannot resolve must not read as dormancy."""
        counts = {"bad name": None, "good name": 42}
        with mock.patch.object(ra, "search_names", return_value=list(counts)), \
             mock.patch.object(
                 ra, "_ctgov_count",
                 side_effect=lambda params: counts[params["query.cond"]]):
            result = ra.trial_attention("bad name")
        self.assertTrue(result["assessed"])
        self.assertEqual(result["n_interventional"], 42)

    def test_search_names_includes_the_original_label(self):
        with mock.patch.object(ra, "disease_synonyms",
                               return_value=["myotonic dystrophy type 1"]):
            names = ra.search_names("Steinert myotonic dystrophy")
        self.assertEqual(names[0], "Steinert myotonic dystrophy")
        self.assertIn("myotonic dystrophy type 1", names)

    def test_a_synonym_duplicate_is_not_searched_twice(self):
        with mock.patch.object(ra, "disease_synonyms",
                               return_value=["Alkaptonuria", "alkaptonuria"]):
            names = ra.search_names("Alkaptonuria")
        self.assertEqual(len(names), 1)


class VerdictTest(unittest.TestCase):

    def _verdict(self, trials, pubs_total, pubs_recent):
        with mock.patch.object(ra, "search_names", return_value=["D"]), \
             mock.patch.object(ra, "_ctgov_count", return_value=trials), \
             mock.patch.object(
                 ra, "_epmc_count",
                 side_effect=lambda q: pubs_recent if "FIRST_PDATE" in q
                 else pubs_total), \
             mock.patch.object(ra, "get", return_value=None), \
             mock.patch.object(ra, "cache_set"):
            return ra.attention_verdict("D")

    def test_dm1_scale_attention_is_rejected(self):
        envelope = self._verdict(136, 9000, 900)
        self.assertEqual(envelope["verdict"], ra.VERDICT_ACTIVE_TRIALS)

    def test_a_single_trial_is_enough_to_reject(self):
        """The bar is zero. One protocol means somebody got there first."""
        envelope = self._verdict(1, 300, 20)
        self.assertEqual(envelope["verdict"], ra.VERDICT_ACTIVE_TRIALS)

    def test_no_trials_and_a_flat_literature_is_dormant(self):
        """Hallermann-Streiff syndrome: 0 trials, 41/327 = 12.5% recent."""
        envelope = self._verdict(0, 327, 41)
        self.assertEqual(envelope["verdict"], ra.VERDICT_DORMANT)

    def test_no_trials_but_a_climbing_literature_is_renewed_interest(self):
        """An empty registry can mean the work has not reached the clinic yet."""
        envelope = self._verdict(0, 120, 60)
        self.assertEqual(envelope["verdict"], ra.VERDICT_RENEWED_INTEREST)

    def test_a_tiny_literature_does_not_fire_renewed_interest_on_share_alone(self):
        """4 of 6 papers is 67% recent and still nobody working on it."""
        envelope = self._verdict(0, 6, 4)
        self.assertEqual(envelope["verdict"], ra.VERDICT_DORMANT)

    def test_a_large_old_literature_does_not_fire_on_count_alone(self):
        """500 recent of 20000 is a field ticking over, not reviving."""
        envelope = self._verdict(0, 20000, 500)
        self.assertEqual(envelope["verdict"], ra.VERDICT_DORMANT)

    def test_the_reason_names_the_sponsor_pressure(self):
        with mock.patch.object(ra, "search_names", return_value=["D"]), \
             mock.patch.object(ra, "_ctgov_count", return_value=136), \
             mock.patch.object(ra, "_epmc_count", return_value=10), \
             mock.patch.object(ra, "get", return_value=None), \
             mock.patch.object(ra, "cache_set"):
            envelope = ra.attention_verdict("D")
        self.assertIn("industry lead sponsor", envelope["reason"])


class ThrottleTest(unittest.TestCase):
    """A 429 read as zero would manufacture the dormancy being certified."""

    def test_both_providers_have_a_request_policy(self):
        from data_sources.provider_request_policy import POLICIES
        self.assertIn("clinicaltrials", POLICIES)
        self.assertIn("europepmc", POLICIES)

    def test_counts_go_through_the_shared_policy_not_bare_requests(self):
        for fn, provider in ((ra._ctgov_count, "clinicaltrials"),
                             (ra._epmc_count, "europepmc")):
            with mock.patch.object(
                    ra.provider_request_policy, "request") as request:
                request.return_value = mock.Mock(
                    status_code=200,
                    json=mock.Mock(return_value={"totalCount": 3,
                                                 "hitCount": 3}))
                fn({"query.cond": "D"} if provider == "clinicaltrials" else "D")
            self.assertEqual(request.call_args.args[0], provider)

    def test_a_throttled_provider_yields_none_not_zero(self):
        with mock.patch.object(
                ra.provider_request_policy, "request",
                side_effect=RuntimeError("circuit open")):
            self.assertIsNone(ra._ctgov_count({"query.cond": "D"}))
            self.assertIsNone(ra._epmc_count("D"))


class ScreenWiringTest(unittest.TestCase):

    def test_attention_runs_before_the_expensive_preflight(self):
        """Paying for Open Targets and Ensembl on an occupied disease is waste."""
        import disease_screen
        with mock.patch.object(
            disease_screen, "attention_verdict",
            return_value={"verdict": "ACTIVE_CLINICAL_PROGRAM",
                          "reason": "136 trials", "trials": {}, "literature": {}},
        ), mock.patch.object(disease_screen, "preflight_for_disease") as pre:
            result = disease_screen.screen_disease("D")
        pre.assert_not_called()
        self.assertEqual(result["verdict"], disease_screen.VERDICT_ATTENTION)

    def test_unmeasurable_attention_is_excluded_not_promoted(self):
        import disease_screen
        with mock.patch.object(
            disease_screen, "attention_verdict",
            return_value={"verdict": ra.VERDICT_UNKNOWN, "reason": "outage",
                          "trials": {}, "literature": {}},
        ), mock.patch.object(disease_screen, "preflight_for_disease") as pre:
            result = disease_screen.screen_disease("D")
        pre.assert_not_called()
        self.assertNotEqual(result["verdict"], disease_screen.VERDICT_VIABLE)

    def test_dormant_diseases_outrank_everything_else(self):
        import disease_screen
        occupied = {"attention_verdict": "ACTIVE_CLINICAL_PROGRAM",
                    "has_approved_treatment": False,
                    "richest_alternative_systemic": 200, "n_usable": 9,
                    "disease_name": "occupied"}
        dormant = {"attention_verdict": ra.VERDICT_DORMANT,
                   "has_approved_treatment": True,
                   "richest_alternative_systemic": 1, "n_usable": 1,
                   "disease_name": "dormant"}
        ranked = sorted([occupied, dormant], key=disease_screen.rank_key)
        self.assertEqual(ranked[0]["disease_name"], "dormant")


if __name__ == "__main__":
    unittest.main()
