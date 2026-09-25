"""The promotable leader is never promoted without a direction check.

Regression cover for the post-benchmark correction of 2026-09-24. The two
bounded direction passes spend their budget on the candidates they cap, and
each cap re-sorts the pool, so a pool whose whole head is directionally
incompatible hands the lead to a candidate no pass reached. The NR3C2 run
capped six steroid agonists and then promoted an unchecked seventh compound.
"""

import unittest

from agents.reviewer import (
    MAX_DIRECTION_LEADER_PASSES,
    _unchecked_direction_leader,
)


def _row(name: str, *, strong: bool, checked: bool) -> dict:
    return {
        "drug_name": name,
        "strong_match": strong,
        "mechanism_direction": {"verdict": "DIRECTIONALLY_COMPATIBLE"}
        if checked else None,
    }


class UncheckedDirectionLeaderTest(unittest.TestCase):

    def test_unchecked_leader_is_returned(self):
        """The NR3C2 shape: everything ahead was capped out of strong_match."""
        reviewed = [
            _row("Capped agonist", strong=False, checked=True),
            _row("Drospirenone", strong=True, checked=False),
            _row("Finerenone", strong=True, checked=False),
        ]
        leader = _unchecked_direction_leader(reviewed)
        self.assertIsNotNone(leader)
        self.assertEqual(leader["drug_name"], "Drospirenone")

    def test_checked_leader_returns_none(self):
        reviewed = [
            _row("Checked lead", strong=True, checked=True),
            _row("Runner up", strong=True, checked=False),
        ]
        self.assertIsNone(_unchecked_direction_leader(reviewed))

    def test_only_the_leader_matters_not_the_rest_of_the_pool(self):
        """Lower unchecked rows are not promoted, so they are not the gap."""
        reviewed = [_row("Checked lead", strong=True, checked=True)] + [
            _row(f"Unchecked {i}", strong=True, checked=False) for i in range(5)
        ]
        self.assertIsNone(_unchecked_direction_leader(reviewed))

    def test_non_strong_rows_are_skipped_not_treated_as_a_sentinel(self):
        """Tier demotion can place a weak row first; it cannot be promoted."""
        reviewed = [
            _row("Weak anchor", strong=False, checked=False),
            _row("Real leader", strong=True, checked=False),
        ]
        leader = _unchecked_direction_leader(reviewed)
        self.assertEqual(leader["drug_name"], "Real leader")

    def test_pool_with_no_strong_candidate_has_no_leader(self):
        reviewed = [_row("Weak", strong=False, checked=False)]
        self.assertIsNone(_unchecked_direction_leader(reviewed))

    def test_empty_pool_has_no_leader(self):
        self.assertIsNone(_unchecked_direction_leader([]))

    def test_convergence_is_bounded(self):
        """Each pass caps at most one leader, so the budget must be finite."""
        self.assertGreaterEqual(MAX_DIRECTION_LEADER_PASSES, 1)
        self.assertLessEqual(MAX_DIRECTION_LEADER_PASSES, 5)

    def test_loop_terminates_when_every_leader_is_incompatible(self):
        """Simulate the loop's contract: capping demotes, so it converges."""
        reviewed = [
            _row(f"Incompatible {i}", strong=True, checked=False)
            for i in range(MAX_DIRECTION_LEADER_PASSES)
        ]
        seen = []
        for _ in range(MAX_DIRECTION_LEADER_PASSES):
            leader = _unchecked_direction_leader(reviewed)
            if leader is None:
                break
            seen.append(leader["drug_name"])
            # Stand in for a DIRECTIONALLY_INCOMPATIBLE cap.
            leader["mechanism_direction"] = {
                "verdict": "DIRECTIONALLY_INCOMPATIBLE"}
            leader["strong_match"] = False
        self.assertEqual(len(seen), MAX_DIRECTION_LEADER_PASSES)
        self.assertEqual(len(set(seen)), len(seen), "a leader was checked twice")
        self.assertIsNone(_unchecked_direction_leader(reviewed))


if __name__ == "__main__":
    unittest.main()
