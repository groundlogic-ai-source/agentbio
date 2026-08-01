"""Unit tests for F2 precedent calibration (pre-registered, approved 2026-07-31).

Pins the constants and the two mechanisms from
validation/f2_precedent_calibration_justification.md:
  - direct precedent 0.90 / parent-umbrella precedent 0.70;
  - mechanistic-convergence cap: precedent-only targets may not outrank the
    best genetic target with OT association >= 0.50 (rank demotion only).

Run: python3 -m unittest validation.test_f2_precedent
"""
import sys
import unittest

sys.path.insert(0, ".")

from agents.target_selection import (  # noqa: E402
    GENETIC_CONVERGENCE_THRESHOLD,
    _apply_mechanistic_convergence_cap,
    _tag_umbrella_precedent,
)
from data_sources.chembl import (  # noqa: E402
    PHARM_PRECEDENT_ASSOC_SCORE,
    PHARM_PRECEDENT_UMBRELLA_ASSOC_SCORE,
)


def _row(sym, method, assoc, tract, unmet=0.0):
    return {
        "target_symbol": sym,
        "target_discovery_method": method,
        "ot_association_score": assoc,
        "tractability_score": tract,
        "unmet_need_score": unmet,
    }


class TestConstants(unittest.TestCase):
    def test_preregistered_values(self):
        self.assertEqual(PHARM_PRECEDENT_ASSOC_SCORE, 0.90)
        self.assertEqual(PHARM_PRECEDENT_UMBRELLA_ASSOC_SCORE, 0.70)
        self.assertEqual(GENETIC_CONVERGENCE_THRESHOLD, 0.50)


class TestUmbrellaTag(unittest.TestCase):
    def test_demotes_score_and_retags(self):
        t = {"target_symbol": "FKBP1A", "uniprot_id": "P62942",
             "association_score": 0.90,
             "target_discovery_method": "pharmacological_precedent"}
        tagged = _tag_umbrella_precedent(t)
        self.assertEqual(tagged["association_score"], 0.70)
        self.assertEqual(tagged["target_discovery_method"],
                         "pharmacological_precedent_via_parent_umbrella")
        self.assertEqual(tagged["target_symbol"], "FKBP1A")
        self.assertEqual(tagged["uniprot_id"], "P62942")


class TestConvergenceCap(unittest.TestCase):
    def test_demotes_precedent_below_best_genetic(self):
        genetic = _row("PDGFRA", "genetic_association", 0.77, 0.40, 0.15)
        precedent = _row("IL5", "pharmacological_precedent", 0.90, 0.80, 0.15)
        rows = [precedent, genetic]  # pre-sorted: precedent outranks
        out = _apply_mechanistic_convergence_cap(rows)
        self.assertEqual([r["target_symbol"] for r in out], ["PDGFRA", "IL5"])
        self.assertTrue(out[1]["precedent_capped"])
        self.assertNotIn("precedent_capped", out[0])
        # Scores unchanged — demotion is rank-only.
        self.assertEqual(out[1]["tractability_score"], 0.80)

    def test_umbrella_precedent_also_capped(self):
        genetic = _row("TSC1", "genetic_association", 0.62, 0.30, 0.10)
        umbrella = _row("FKBP1A", "pharmacological_precedent_via_parent_umbrella",
                        0.70, 0.60, 0.10)
        out = _apply_mechanistic_convergence_cap([umbrella, genetic])
        self.assertEqual([r["target_symbol"] for r in out], ["TSC1", "FKBP1A"])
        self.assertTrue(out[1]["precedent_capped"])

    def test_noop_when_no_strong_genetic(self):
        weak_genetic = _row("GENE1", "genetic_association", 0.30, 0.20, 0.10)
        precedent = _row("PDE5A", "pharmacological_precedent", 0.90, 0.70, 0.10)
        rows = [precedent, weak_genetic]
        out = _apply_mechanistic_convergence_cap(rows)
        self.assertEqual(out, rows)  # precedent still wins — sildenafil pattern
        self.assertNotIn("precedent_capped", out[0])

    def test_noop_when_precedent_already_below(self):
        genetic = _row("JAK2", "genetic_association", 0.85, 0.60, 0.20)
        precedent = _row("JAK1", "pharmacological_precedent", 0.90, 0.30, 0.10)
        rows = [genetic, precedent]
        out = _apply_mechanistic_convergence_cap(rows)
        self.assertEqual([r["target_symbol"] for r in out], ["JAK2", "JAK1"])
        self.assertNotIn("precedent_capped", out[1])

    def test_multiple_capped_preserve_relative_order_and_stay_in_list(self):
        g = _row("GENE", "genetic_association", 0.55, 0.30, 0.10)
        p1 = _row("P1", "pharmacological_precedent", 0.90, 0.90, 0.10)
        p2 = _row("P2", "pharmacological_precedent", 0.90, 0.80, 0.10)
        other = _row("OTHER", "genetic_association", 0.20, 0.10, 0.10)
        out = _apply_mechanistic_convergence_cap([p1, p2, g, other])
        self.assertEqual([r["target_symbol"] for r in out],
                         ["GENE", "P1", "P2", "OTHER"])
        # Capped rows are demoted, never excluded.
        self.assertEqual(len(out), 4)
        self.assertTrue(all(r.get("precedent_capped") for r in out[1:3]))


if __name__ == "__main__":
    unittest.main()
