"""Tests for the static "How to read this dossier" writer appendix.

The appendix is deliberately STATIC (format explanation only, no candidate
claims) so it can never introduce an unverifiable statement into an
otherwise claim-audited document. These tests pin that contract.
"""

import unittest
import unittest.mock

from agents.writer import (
    _READERS_GUIDE_VERSION,
    _citations,
    _confidence_band,
    _readers_guide_appendix,
    build_report_markdown,
)


def _minimal_candidate():
    return {
        "drug_name": "TestDrug",
        "target_symbol": "TEST1",
        "disease_name": "Test Disease",
        "strong_match": True,
        "composite_score": 0.75,
        "is_approved_drug": True,
        "score_components": {
            "efficacy_evidence": 0.8,
            "normalized_ot_association": 0.5,
            "normalized_tanimoto": 0.3,
            "no_failed_trial": 1,
            "evidence_weight_coverage": 1.0,
        },
        "provenance": {"counted_once": [], "collapsed_as_duplicate": []},
    }


class TestReadersGuideAppendix(unittest.TestCase):

    def test_appendix_present_in_report(self):
        md = build_report_markdown(
            _minimal_candidate(), {},
            {"composite_weights": {"efficacy_evidence": 0.5, "ot_association": 0.2,
                                   "tanimoto": 0.15, "no_failed_trial": 0.15},
             "strong_match_threshold": 0.70},
            None,
        )
        self.assertIn("## 6. How to read this dossier", md)
        self.assertIn(f"Reader's guide v{_READERS_GUIDE_VERSION}", md)

    def test_appendix_is_fully_static(self):
        # Two calls with nothing changing must be byte-identical, and the text
        # must not embed candidate-specific values.
        a = _readers_guide_appendix()
        b = _readers_guide_appendix()
        self.assertEqual(a, b)
        self.assertNotIn("TestDrug", a)
        self.assertNotIn("Test Disease", a)

    def test_appendix_explains_key_terms(self):
        text = _readers_guide_appendix()
        for term in ("STRONG_MATCH", "pre_cap_score", "Tanimoto", "pChEMBL",
                     "renormaliz", "hard cap", "human review", "ADME"):
            self.assertIn(term.lower(), text.lower(), f"missing term: {term}")

    def test_confidence_bands_cover_low_middle_and_high(self):
        self.assertEqual(_confidence_band(0.161), "low")
        self.assertEqual(_confidence_band(0.50), "moderate")
        self.assertEqual(_confidence_band(0.85), "high")

    def test_low_structure_values_are_never_called_high(self):
        md = build_report_markdown(
            _minimal_candidate(),
            {"complex": {"binding_pose_confidence": 0.161,
                         "predicted_affinity": 0.190}},
            {}, None,
        )
        self.assertIn("binding-pose confidence is **low** (0.161)", md)
        self.assertIn("predicted affinity is **low** (0.190)", md)
        self.assertNotIn("high binding-pose confidence", md.lower())

    def test_no_direct_activity_explanation_and_modality_provenance(self):
        candidate = _minimal_candidate()
        candidate["_evidence_ledger"] = {"records": [{
            "provider": "openfda",
            "source_type": "drug_label",
            "evidence_role": "efficacy",
            "qualification_status": "qualified",
            "label_id": "abc-label",
        }]}
        candidate["score_components"]["efficacy_evidence_source"] = "multisource_ledger"
        md = build_report_markdown(candidate, {}, {}, None)
        self.assertIn("No qualified ChEMBL human bioactivity ledger row", md)
        self.assertIn("does not claim direct target-assay support", md)
        self.assertIn("not direct-assay-backed", md)
        self.assertIn("not a measured probability of efficacy", md)

    def test_zero_like_provider_ids_are_not_cited(self):
        candidate = _minimal_candidate()
        candidate["_evidence_ledger"] = {"records": [{
            "provider": "openfda",
            "source_id": "openfda-label-mechanism:0",
            "label_id": 0,
        }]}
        with unittest.mock.patch("agents.writer.check_prior_trials",
                                 return_value={"trials": []}):
            cites = _citations(candidate, None)
        self.assertNotIn("0", cites["source_records"]["openfda"])

    def test_appendix_states_checkpoint_ordering(self):
        # Regression guard for the ChatGPT-review misreading: the human
        # checkpoint gates COMPLETION, not the Boltz spend (Boltz ran earlier).
        text = _readers_guide_appendix().lower()
        self.assertIn("already run", text)

    def test_every_report_discloses_therapeutic_applicability(self):
        md = build_report_markdown(_minimal_candidate(), {}, {}, None)
        self.assertIn("Therapeutic applicability not assessed", md)
        self.assertIn("relevant tissue, cell, or compartment", md)
        self.assertIn("effective, tolerable human exposure", md)
        self.assertIn("Route, dose, pharmacokinetics (PK)", md)
        self.assertIn("outside what this pipeline measures", md)

    def test_reader_guide_says_applicability_is_not_scored(self):
        """Applicability is still not a SCORE term — but it is now gated.

        The guide used to claim the limitation "introduces no tissue-specific
        score, cap, or gate". That became false when the compartment-exposure
        gate shipped: a documented failure to reach the compartment excludes
        the candidate. A dossier that understates its own method is as wrong as
        one that overstates it.
        """
        text = _readers_guide_appendix()
        self.assertIn("Therapeutic applicability is not scored", text)
        self.assertNotIn("no tissue-specific score, cap, or gate", text)
        # It must say what IS done, and not overclaim what clearing it means.
        self.assertIn("DOCUMENTED failure to reach that", text)
        self.assertIn("not evidence of adequate exposure", text)
        # And must keep disclaiming everything still unassessed.
        for term in ("Route, dose, pharmacokinetics", "therapeutic window"):
            self.assertIn(term, text)

    def test_appendix_appears_after_limitations(self):
        md = build_report_markdown(_minimal_candidate(), {}, {}, None)
        self.assertLess(md.index("## 5. Limitations"),
                        md.index("## 6. How to read this dossier"))

    def test_capped_report_shows_pre_cap_score_as_guide_claims(self):
        # The guide tells readers that Section 4 shows the uncapped score
        # (pre_cap_score) when a cap fired — so the breakdown must render it.
        cand = _minimal_candidate()
        cand["composite_score"] = 0.40
        cand["pre_cap_score"] = 0.75
        cand["safety_cap_applied"] = True
        md = build_report_markdown(cand, {}, {}, None)
        self.assertIn("Pre-cap score (before hard caps)", md)
        self.assertIn("0.7500", md)

    def test_uncapped_report_omits_pre_cap_row(self):
        md = build_report_markdown(_minimal_candidate(), {}, {}, None)
        self.assertNotIn("Pre-cap score", md)


if __name__ == "__main__":
    unittest.main()
