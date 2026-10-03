"""A drug formulated for local action cannot treat a systemic disease.

The compartment check asks whether a drug crosses the blood-brain barrier. For
a disease outside the CNS it answers nothing, and that gap produced a false
headline.

A Duchenne muscular dystrophy run promoted FLUTICASONE PROPIONATE: pChEMBL
10.3979 (~40 pM against NR3C1), assay confidence 9, measured genetic
association, direction DIRECTIONALLY_COMPATIBLE, no black-box warning,
NO_PRIOR_ART_FOUND, zero exclusions. Every gate cleared it.

Fluticasone propionate has ~1% oral bioavailability. Near-complete hepatic
first-pass metabolism is the POINT of the molecule -- it exists for local
airway action with minimal systemic exposure. DMD requires chronic systemic
glucocorticoid exposure to skeletal and cardiac muscle. All four promoted
candidates were inhaled or topical steroids.

This is the eliglustat failure again in a different compartment: right target,
exceptional potency, cannot reach the tissue. And as with eliglustat, the
prior-art gate was correct that nobody had published it -- because it obviously
would not work. Absence of prior art is not evidence of opportunity.

The fix is deterministic and free. ChEMBL's route flags separate the false
positives from the real drugs cleanly, with no LLM call:

    fluticasone propionate  oral=False topical=False parenteral=False
    halcinonide             oral=False topical=False parenteral=False
    prednisolone            oral=TRUE      <- DMD standard of care
    deflazacort             oral=TRUE      <- FDA-approved for DMD
"""

import unittest

from data_sources.tissue_exposure import systemic_route_check


class FalsePositiveRegressionTest(unittest.TestCase):
    """The four candidates that were wrongly promoted."""

    def test_fluticasone_propionate_has_no_systemic_route(self):
        result = systemic_route_check({
            "route_oral": False, "route_topical": False,
            "route_parenteral": False})
        self.assertTrue(result["assessed"])
        self.assertFalse(result["systemic_route"])

    def test_halcinonide_has_no_systemic_route(self):
        result = systemic_route_check({
            "route_oral": False, "route_topical": False,
            "route_parenteral": False})
        self.assertFalse(result["systemic_route"])

    def test_the_reason_names_the_actual_problem(self):
        result = systemic_route_check({
            "route_oral": False, "route_topical": False,
            "route_parenteral": False})
        self.assertIn("cannot deliver systemic exposure", result["reason"])
        self.assertIn("Target potency does not substitute", result["reason"])


class RealDrugsSurviveTest(unittest.TestCase):
    """The gate must not exclude the drugs actually used for the disease."""

    def test_prednisolone_passes(self):
        result = systemic_route_check({
            "route_oral": True, "route_topical": True,
            "route_parenteral": True})
        self.assertTrue(result["systemic_route"])

    def test_deflazacort_passes_on_oral_alone(self):
        """FDA-approved for DMD; oral only."""
        result = systemic_route_check({
            "route_oral": True, "route_topical": False,
            "route_parenteral": False})
        self.assertTrue(result["systemic_route"])

    def test_parenteral_only_drug_passes(self):
        """An injectable is systemic even with no oral form."""
        result = systemic_route_check({
            "route_oral": False, "route_topical": False,
            "route_parenteral": True})
        self.assertTrue(result["systemic_route"])

    def test_passing_is_not_a_claim_of_adequate_exposure(self):
        result = systemic_route_check({"route_oral": True})
        self.assertIn("not that it", result["reason"])


class UnknownIsNotCleanTest(unittest.TestCase):
    """Absent flags clear nothing -- the same discipline as everywhere else."""

    def test_absent_flags_are_unassessed(self):
        result = systemic_route_check({})
        self.assertFalse(result["assessed"])
        self.assertIsNone(result["systemic_route"])
        self.assertIn("not a clear result", result["reason"])

    def test_all_none_is_unassessed(self):
        result = systemic_route_check({
            "route_oral": None, "route_topical": None,
            "route_parenteral": None})
        self.assertFalse(result["assessed"])

    def test_a_single_known_flag_is_enough_to_assess(self):
        result = systemic_route_check({"route_oral": False})
        self.assertTrue(result["assessed"])
        self.assertFalse(result["systemic_route"])


class WiringTest(unittest.TestCase):

    def test_reviewer_excludes_on_no_systemic_route(self):
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertIn(
            'reasons.append("no_systemic_route_of_administration")', source)

    def test_only_an_assessed_absence_excludes(self):
        """Unknown route must not exclude, only a known local-only one."""
        import inspect
        from agents import reviewer
        source = inspect.getsource(reviewer)
        self.assertIn(
            'if route.get("assessed") and not route.get("systemic_route")',
            source)

    def test_route_flags_are_carried_from_chembl(self):
        import inspect
        from data_sources import chembl
        source = inspect.getsource(chembl)
        for field in ("route_oral", "route_topical", "route_parenteral"):
            self.assertIn(f'"{field}"', source)


if __name__ == "__main__":
    unittest.main()
