"""Tests for the static "How to read this dossier" writer appendix.

The appendix is deliberately STATIC (format explanation only, no candidate
claims) so it can never introduce an unverifiable statement into an
otherwise claim-audited document. These tests pin that contract.
"""

import unittest

from agents.writer import (
    _READERS_GUIDE_VERSION,
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
        self.assertIn("Unknown must not be interpreted as compatible", md)

    def test_reader_guide_says_applicability_is_not_scored(self):
        text = _readers_guide_appendix()
        self.assertIn("Therapeutic applicability is not scored", text)
        self.assertIn("no tissue-specific score, cap, or", text)
        self.assertIn("gate", text)

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
