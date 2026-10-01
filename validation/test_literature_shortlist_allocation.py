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
        self.assertIn("(_promotable or reviewed)[", source)

    def test_measured_targets_is_resolved_before_the_shortlist(self):
        """Order matters: the filter cannot use a set built after it."""
        source = inspect.getsource(reviewer.run_reviewer)
        self.assertLess(
            source.index("_measured_targets: set[str] = {"),
            source.index("_promotable = ["),
            "the measured-target set must be built before the shortlist")

    def test_the_budget_constant_is_unchanged(self):
        """The fix reallocates the budget; it does not quietly enlarge it.

        Raising the cap would spend more on every run, including the ones this
        change already makes cheaper.
        """
        self.assertEqual(reviewer.MAX_LITERATURE_LIMITATION_CANDIDATES, 3)

    def test_holdout_path_is_untouched(self):
        """Frozen studies predate this gate and must keep their semantics."""
        source = inspect.getsource(reviewer.run_reviewer)
        holdout_block = source.split("if _holdout.is_active():")[1]
        holdout_block = holdout_block.split("else:")[0]
        self.assertNotIn("_promotable", holdout_block)


if __name__ == "__main__":
    unittest.main()
