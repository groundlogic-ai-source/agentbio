"""Target engagement is not tissue exposure.

Built 2026-09-29 after a Niemann-Pick type C run ranked ELIGLUSTAT first out of
397 candidates: approved drug, pChEMBL 7.8 against UGCG (the validated
non-causal target), literature gate clear, no prior art, zero exclusions.

Eliglustat is actively effluxed from the brain by P-glycoprotein. That is why
it is approved for Gaucher disease type 1 and not for neuronopathic type 3.
NPC is neurodegenerative -- the neurological course is what kills children at a
median age of 13. Miglustat, a far weaker inhibitor of the same enzyme, is the
one used in NPC precisely because it crosses the blood-brain barrier.

The pipeline scored potency, assay confidence and genetic association, and
nothing about whether the molecule arrives.

THE TRAP THIS MODULE HAD TO AVOID
---------------------------------
The obvious gate is a descriptor rule -- MW < 450, TPSA < 90, HBD <= 3,
logP 2-5. Eliglustat's measured descriptors are MW 404.55, TPSA 71.03, HBD 2,
logP 3.43: it passes every threshold. Descriptors model PASSIVE permeability
and eliglustat's barrier is ACTIVE efflux. The obvious implementation would
have cleared the exact candidate that motivated the work, which is why the
descriptor block is disclosure-only and can never produce the verdict.
"""

import unittest

from data_sources import tissue_exposure
from data_sources.tissue_exposure import (
    VERDICT_INSUFFICIENT,
    VERDICT_NOT_REQUIRED,
    VERDICT_PLAUSIBLE,
    VERDICT_UNLIKELY,
    descriptor_disclosure,
    parse_verdict,
    requires_cns_exposure,
)

# Measured for eliglustat in the run that motivated this module.
ELIGLUSTAT_DESCRIPTORS = {
    "molecular_weight": 404.55,
    "logp": 3.43,
    "h_bond_donors": 2,
    "h_bond_acceptors": 5,
    "tpsa": 71.03,
}


class CompartmentRequirementTest(unittest.TestCase):

    def test_niemann_pick_type_c_requires_cns(self):
        """The disease name carries no neurological word at all.

        A name-marker test alone would skip the disease this gate exists for,
        which is why lysosomal/metabolic families are matched separately.
        """
        self.assertTrue(requires_cns_exposure("Niemann-Pick disease type C"))

    def test_the_name_alone_contains_no_neuro_marker(self):
        """Documents why the second matcher is needed rather than assuming it."""
        from data_sources.tissue_exposure import _CNS_DISEASE_MARKERS
        self.assertIsNone(
            _CNS_DISEASE_MARKERS.search("Niemann-Pick disease type C"))

    def test_explicit_neurological_names_are_caught(self):
        for name in (
            "Metachromatic leukodystrophy",
            "Friedreich ataxia",
            "Infantile neuroaxonal dystrophy",
            "Progressive myoclonic epilepsy",
            "Autosomal recessive spastic paraplegia",
        ):
            self.assertTrue(requires_cns_exposure(name), name)

    def test_other_lysosomal_families_are_caught(self):
        for name in (
            "Gaucher disease type 3",
            "Krabbe disease",
            "Tay-Sachs disease",
            "Sanfilippo syndrome",
            "Neuronal ceroid lipofuscinosis",
        ):
            self.assertTrue(requires_cns_exposure(name), name)

    def test_non_cns_disease_does_not_assert_a_requirement(self):
        for name in ("Cystinosis", "Wilson disease", "Essential thrombocythemia"):
            self.assertFalse(requires_cns_exposure(name), name)

    def test_empty_name_asserts_nothing(self):
        self.assertFalse(requires_cns_exposure(""))


class DescriptorDisclosureTest(unittest.TestCase):
    """Descriptors are context. They must never decide."""

    def test_eliglustat_passes_every_passive_threshold(self):
        """The finding that shaped the design."""
        disclosure = descriptor_disclosure(ELIGLUSTAT_DESCRIPTORS)
        self.assertTrue(
            disclosure["passive_cns_ruleset_passed"],
            "eliglustat satisfies the naive CNS descriptor rules -- which is "
            "exactly why they cannot be the verdict")

    def test_disclosure_states_its_own_limitation(self):
        disclosure = descriptor_disclosure(ELIGLUSTAT_DESCRIPTORS)
        self.assertIn("efflux", disclosure["note"].lower())

    def test_absent_descriptors_are_reported_not_guessed(self):
        self.assertFalse(descriptor_disclosure({})["assessed"])
        self.assertFalse(descriptor_disclosure(None)["assessed"])

    def test_a_large_polar_molecule_fails_the_passive_ruleset(self):
        disclosure = descriptor_disclosure(
            {"molecular_weight": 780, "tpsa": 210, "h_bond_donors": 9,
             "logp": -1.2})
        self.assertFalse(disclosure["passive_cns_ruleset_passed"])

    def test_the_verdict_never_comes_from_descriptors(self):
        """A descriptor pass must not appear anywhere in verdict selection."""
        import inspect
        source = inspect.getsource(tissue_exposure.check_tissue_exposure)
        self.assertNotIn("passive_cns_ruleset_passed", source)


class VerdictParsingTest(unittest.TestCase):

    def test_documented_efflux_parses_as_unlikely(self):
        verdict, reason = parse_verdict(
            "VERDICT: COMPARTMENT_EXPOSURE_UNLIKELY\n"
            "REASON: Eliglustat is a documented P-glycoprotein substrate and "
            "is approved only for non-neuronopathic Gaucher disease type 1.\n"
            "CITATIONS: https://example.org")
        self.assertEqual(verdict, VERDICT_UNLIKELY)
        self.assertIn("P-glycoprotein", reason)

    def test_positive_cns_evidence_parses_as_plausible(self):
        verdict, _ = parse_verdict(
            "VERDICT: COMPARTMENT_EXPOSURE_PLAUSIBLE\nREASON: crosses.\n")
        self.assertEqual(verdict, VERDICT_PLAUSIBLE)

    def test_unparseable_output_is_unknown(self):
        for text in ("", "the drug probably gets in", None,
                     "VERDICT: MAYBE_SOMETIMES"):
            verdict, _ = parse_verdict(text)
            self.assertEqual(verdict, VERDICT_INSUFFICIENT)

    def test_reason_defaults_to_the_unknown_notice(self):
        _, reason = parse_verdict("VERDICT: INSUFFICIENT_INFO")
        self.assertIn("unknown", reason.lower())


class FailOpenTest(unittest.TestCase):
    """Only a documented failure may exclude. Unknown must not."""

    def test_only_unlikely_sets_the_blocking_flag(self):
        for verdict in (VERDICT_PLAUSIBLE, VERDICT_INSUFFICIENT,
                        VERDICT_NOT_REQUIRED,
                        tissue_exposure.VERDICT_NOT_ASSESSED):
            envelope = tissue_exposure._envelope(verdict, "r")
            self.assertFalse(envelope["exposure_unlikely"], verdict)

    def test_unlikely_sets_the_blocking_flag(self):
        self.assertTrue(
            tissue_exposure._envelope(VERDICT_UNLIKELY, "r")["exposure_unlikely"])

    def test_non_cns_disease_short_circuits_without_a_network_call(self):
        """Cheap path: no retrieval for a disease with no CNS requirement."""
        result = tissue_exposure.check_tissue_exposure(
            "Cloxacillin", "Cystinosis", descriptors=ELIGLUSTAT_DESCRIPTORS)
        self.assertEqual(result["verdict"], VERDICT_NOT_REQUIRED)
        self.assertFalse(result["exposure_unlikely"])

    def test_the_non_required_verdict_does_not_overclaim(self):
        result = tissue_exposure.check_tissue_exposure(
            "Cloxacillin", "Cystinosis")
        self.assertIn("not assessed", result["reason"].lower())


class DisclosureScopeTest(unittest.TestCase):
    """This narrows one clause of the applicability disclosure, not all of it.

    The dossier states that ranking does not assess tissue reach, exposure,
    route, dose, PK, disease stage or therapeutic window. This gate addresses a
    bounded part of the first item only. Claiming it retires the rest would be
    precisely the overclaiming the project removes elsewhere.
    """

    def test_module_disclaims_dose_route_and_pk(self):
        doc = tissue_exposure.__doc__ or ""
        for term in ("dose", "route", "pharmacokinetic", "therapeutic window"):
            self.assertIn(term, doc.lower())

    def test_no_verdict_claims_therapeutic_adequacy(self):
        for verdict in (VERDICT_UNLIKELY, VERDICT_PLAUSIBLE,
                        VERDICT_INSUFFICIENT, VERDICT_NOT_REQUIRED):
            self.assertNotIn("EFFECTIVE", verdict.upper())


if __name__ == "__main__":
    unittest.main()
