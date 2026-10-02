"""The scarcest budget in the pipeline must not be spent on the ineligible.

MAX_LITERATURE_LIMITATION_CANDIDATES is 3. A candidate outside that shortlist
is recorded NOT_ASSESSED, never clears literature_limitation_gate_cleared, and
is excluded with literature_gate_not_cleared -- without its evidence ever being
looked at. The shortlist is therefore not a ranking convenience; it decides
which candidates are evaluated at all.

It was taken as the plain top 3 by rank, which spent the whole budget on
candidates already destined for exclusion. In an alpha-1-antitrypsin deficiency
run the top ten by composite were SGLT2 inhibitors on SLC5A2 and complement
drugs on C5 -- both admitted by pharmacological precedent, both carrying the
provisional 0.50 placeholder rather than a measured association, and both
therefore unpromotable no matter what the literature gate returned. All three
shortlist slots went to SGLT2 inhibitors.

Meanwhile ELANE, the target the disease screen predicted and the one carrying a
real measured association (0.5465), held six candidates including sivelestat,
an approved neutrophil elastase inhibitor at composite 0.8725. Every one came
back NOT_ASSESSED. The pipeline never looked at the only mechanistically apt
candidate it had found.
"""

import inspect
import unittest

from agents import reviewer


class ShortlistAllocationTest(unittest.TestCase):

    def test_shortlist_prefers_candidates_on_measured_targets(self):
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertIn("_promotable = [", source)
        self.assertIn(
            'if str(r.get("target_symbol") or "").upper() in _measured_targets',
            source)

    def test_shortlist_is_no_longer_the_plain_top_n(self):
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertNotIn(
            "shortlist = reviewed[:MAX_LITERATURE_LIMITATION_CANDIDATES]",
            source)

    def test_it_falls_back_rather_than_assessing_nothing(self):
        """A pool with no measured target must still be assessed.

        Filtering to an empty shortlist would silently skip the gate for every
        candidate, turning a narrowing optimisation into a blanket skip.
        """
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertIn("(_unclaimed or _promotable or reviewed)[", source)

    def test_prior_art_is_screened_before_the_paid_budget(self):
        """The free gate must run first.

        Prior art is a Europe PMC search with no LLM call; the
        literature-limitation gate is the paid one and is capped. Running the
        free filter after the paid one spends the whole budget on candidates a
        free search would have removed -- on Duchenne muscular dystrophy the
        top three by rank were the corticosteroids already used to treat it,
        and prednisolone took a slot before prior art ever ran.
        """
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertLess(
            source.index("for r in _promotable[:MAX_PRIOR_ART_PRESCREEN]"),
            source.index("shortlist = ("),
            "prior art must be screened before the shortlist is drawn")

    def test_claimed_pairs_do_not_consume_the_budget(self):
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertIn("_unclaimed = [", source)
        self.assertIn(
            'if not (r.get("prior_art_found") and prior_art_gate_is_enabled())',
            source)

    def test_the_prescreen_is_bounded(self):
        """One HTTP call per candidate, so the pass cannot run unbounded."""
        self.assertIsInstance(reviewer.MAX_PRIOR_ART_PRESCREEN, int)
        self.assertGreaterEqual(
            reviewer.MAX_PRIOR_ART_PRESCREEN,
            reviewer.MAX_LITERATURE_LIMITATION_CANDIDATES,
            "the prescreen must be able to supply a full shortlist")

    def test_measured_targets_is_resolved_before_the_shortlist(self):
        """Order matters: the filter cannot use a set built after it."""
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertLess(
            source.index("_measured_targets: set[str] = {"),
            source.index("_promotable = ["),
            "the measured-target set must be built before the shortlist")

    def test_the_budget_matches_the_benchmark_hit_definition(self):
        """10 mirrors how a benchmark hit is defined: rediscovery within top-10.

        Raised from 3 once prior art was screened first: on a well-drugged
        target the top 3 by rank are the drugs already used for the disease.
        """
        self.assertEqual(reviewer.MAX_LITERATURE_LIMITATION_CANDIDATES, 10)

    def test_holdout_path_is_untouched(self):
        """Frozen studies predate this gate and must keep their semantics."""
        source = inspect.getsource(reviewer.run_reviewer)
        holdout_block = source.split("if _holdout.is_active():")[1]
        holdout_block = holdout_block.split("else:")[0]
        self.assertNotIn("_promotable", holdout_block)


if __name__ == "__main__":
    unittest.main()
