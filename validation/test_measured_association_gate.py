"""A placeholder association score is not evidence of disease relevance.

Post-benchmark correction of 2026-09-29. ``PROCESS_EVIDENCE_ASSOC_SCORE = 0.50``
is assigned to targets admitted by pharmacological precedent. The constant's own
comment in agents/target_selection.py says it is provisional and "must be
calibrated on a broader drug-grouped corpus before any benchmark freeze".

Downstream it was indistinguishable from a measured Open Targets association.
In a Niemann-Pick type C run:

    UGCG    genetic_association        ot_assoc 0.5006   (measured)
    SMPD1   genetic_association        ot_assoc 0.4436   (measured)
    DRD2    pharmacological_precedent  ot_assoc 0.5000   (placeholder)
    DRD3    pharmacological_precedent  ot_assoc 0.5000   (placeholder)
    GABRA1  pharmacological_precedent  ot_assoc 0.5000   (placeholder)

The flat placeholder outranked SMPD1's measured value and tied UGCG's. Combined
with the pagination fix making the full DRD2 ledger visible -- 14,702 activity
records of potent, well-characterised antipsychotics -- 37 of the top 40
candidates became dopamine-receptor drugs. Those treat the disease's
psychiatric SYMPTOMS; they reached the pool only because a symptomatic drug
binds that target, which is circular: the target is "relevant" because a
symptom drug hits it.

Such rows stay in the pool as precedent context and are disclosed. They are not
disease-biology hypotheses and must not be promoted as one.
"""

import unittest

from agents.reviewer import _MEASURED_ASSOCIATION_METHODS


class MeasuredMethodTest(unittest.TestCase):

    def test_genetic_association_is_measured(self):
        self.assertIn("genetic_association", _MEASURED_ASSOCIATION_METHODS)

    def test_precedent_and_neighbour_methods_are_not_measured(self):
        for method in (
            "pharmacological_precedent",
            "pharmacological_precedent_via_parent_umbrella",
            "literature_mechanism_class",
            "pathway_neighbor",
        ):
            self.assertNotIn(method, _MEASURED_ASSOCIATION_METHODS, method)

    def test_the_set_is_deliberately_narrow(self):
        """Widening this silently re-admits placeholder-scored targets."""
        self.assertEqual(_MEASURED_ASSOCIATION_METHODS, {"genetic_association"})


class PlaceholderIsMarkedTest(unittest.TestCase):
    """target_selection must label the placeholder at the point it invents it."""

    def test_process_targets_are_marked_unmeasured(self):
        import inspect
        from agents import target_selection
        source = inspect.getsource(target_selection)
        self.assertIn('"ot_association_measured": False', source)

    def test_scored_rows_carry_the_flag(self):
        import inspect
        from agents import target_selection
        source = inspect.getsource(target_selection)
        self.assertIn('"ot_association_measured": bool(', source)

    def test_the_placeholder_constant_still_exists_unchanged(self):
        """The fix marks the number; it does not guess a better one.

        Choosing a different constant without calibration would replace one
        uncalibrated value with another.
        """
        from agents.target_selection import PROCESS_EVIDENCE_ASSOC_SCORE
        self.assertEqual(PROCESS_EVIDENCE_ASSOC_SCORE, 0.50)


class ExclusionWiringTest(unittest.TestCase):

    def test_reviewer_excludes_on_unmeasured_association(self):
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertIn('reasons.append("target_association_not_measured")',
                      source)

    def test_the_check_resolves_the_target_not_the_candidate_lane(self):
        """A target's evidence is a property of the TARGET.

        target_discovery_method describes how a CANDIDATE reached the pool.
        Gating on it per row gave one target two verdicts: every ELANE row in
        an alpha-1-antitrypsin deficiency run carried the same measured
        association (0.5465), yet sivelestat -- an actual neutrophil elastase
        inhibitor and the most apt candidate present -- arrived through the
        precedent lane and was excluded, while bortezomib, a mechanistically
        irrelevant proteasome inhibitor, arrived through the genetic lane and
        was not.
        """
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertIn("_measured_targets: set[str] = {", source)
        self.assertIn(
            'if str(r.get("target_symbol") or "").upper() not in _measured_targets:',
            source)
        self.assertNotIn(
            'if str(r.get("target_discovery_method") or "").strip().lower() \\\n'
            '                not in _MEASURED_ASSOCIATION_METHODS:',
            source)

    def test_rows_are_excluded_not_deleted(self):
        """Precedent targets remain visible as context.

        Dropping them would hide real pharmacological precedent; the point is
        that they cannot be promoted, not that they cannot be seen.
        """
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertNotIn("del r[", source)


if __name__ == "__main__":
    unittest.main()
