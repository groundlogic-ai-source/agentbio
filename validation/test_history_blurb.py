"""Tests for the two-tier exclusion-history prompt in run_discovery.

The exclusion prompt is the ONLY prompt-level guard against re-proposing
already-explored domains. The old single-tier cap evicted older domains from
the prompt entirely once history grew past the cap — making known ground
re-proposable (wasted LLM calls, test compute, and FDR slots). The two-tier
blurb must therefore (a) bound total size, and (b) never drop a domain NAME
completely unless even the compact roster overflows.
"""

import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data_prep"))

import run_discovery as RD  # noqa: E402


def _fake_history(n_domains, rows_per_domain=1):
    """Build a fake bisociation_history DataFrame with n unique domains."""
    rows = []
    for i in range(n_domains):
        for j in range(rows_per_domain):
            rows.append({
                "domain_description": f"Domain number {i:03d} phenomenon",
                "outcome_note": "tested; p=0.42" if j == 0 else "SALVAGEABLE: retry",
                "discovery_pass": False,
            })
    return pd.DataFrame(rows)


class TestHistoryBlurb(unittest.TestCase):

    def test_empty_history_message(self):
        with mock.patch.object(RD.R, "load_history",
                               return_value=pd.DataFrame()):
            text = RD._history_blurb()
        self.assertIn("EMPTY", text)

    def test_small_history_shows_everything_full_detail(self):
        with mock.patch.object(RD.R, "load_history",
                               return_value=_fake_history(5)):
            text = RD._history_blurb()
        for i in range(5):
            self.assertIn(f"Domain number {i:03d}", text)
        self.assertNotIn("names only", text)
        self.assertIn("did not survive", text)

    def test_large_history_keeps_every_domain_name(self):
        # 100 unique domains > HISTORY_PROMPT_MAX_ITEMS (40): tier 2 must carry
        # the older 60 as names, so NO domain leaves the prompt.
        with mock.patch.object(RD.R, "load_history",
                               return_value=_fake_history(100)):
            text = RD._history_blurb()
        for i in range(100):
            self.assertIn(f"Domain number {i:03d}", text,
                          f"domain {i} dropped from prompt entirely")
        self.assertIn("names only", text)

    def test_total_size_bounded(self):
        with mock.patch.object(RD.R, "load_history",
                               return_value=_fake_history(500, rows_per_domain=3)):
            text = RD._history_blurb()
        # Budget: full-detail chars + names roster + structural overhead.
        hard_ceiling = (RD.HISTORY_PROMPT_MAX_CHARS
                        + RD.HISTORY_PROMPT_NAMES_MAX_CHARS + 2000)
        self.assertLessEqual(len(text), hard_ceiling)

    def test_newest_domains_get_full_detail(self):
        # Domain 099 is the newest; it must keep its full status line.
        with mock.patch.object(RD.R, "load_history",
                               return_value=_fake_history(100)):
            text = RD._history_blurb()
        self.assertIn("- Domain number 099 phenomenon [", text)

    def test_salvageable_status_rendered(self):
        df = pd.DataFrame([{
            "domain_description": "Salvage domain",
            "outcome_note": "SALVAGEABLE: needs binary op",
            "discovery_pass": False,
        }])
        with mock.patch.object(RD.R, "load_history", return_value=df):
            text = RD._history_blurb()
        self.assertIn("salvageable", text)

    def test_duplicate_rows_deduped_per_domain(self):
        df = _fake_history(3, rows_per_domain=5)
        with mock.patch.object(RD.R, "load_history", return_value=df):
            text = RD._history_blurb()
        # each domain appears exactly once in tier-1 full lines
        self.assertEqual(text.count("- Domain number 000 phenomenon ["), 1)

    def test_oversized_newest_note_does_not_starve_tier1(self):
        # One giant newest note must be SKIPPED, not stop packing: the five
        # older short entries still get full detail, and the giant domain's
        # NAME still appears (tier 2) so it never leaves the prompt.
        rows = [{
            "domain_description": f"Short domain {i}",
            "outcome_note": "tested; p=0.42",
            "discovery_pass": False,
        } for i in range(5)]
        rows.append({
            "domain_description": "Giant note domain",
            "outcome_note": "x" * (RD.HISTORY_PROMPT_MAX_CHARS + 500),
            "discovery_pass": False,
        })
        with mock.patch.object(RD.R, "load_history",
                               return_value=pd.DataFrame(rows)):
            text = RD._history_blurb()
        for i in range(5):
            self.assertIn(f"- Short domain {i} [", text)
        self.assertIn("Giant note domain", text)          # tier-2 roster
        self.assertNotIn("- Giant note domain [", text)   # no full line

    def test_names_roster_overflow_is_bounded_and_noted(self):
        # 105 long-named domains: 40 fill tier 1, the remaining 65 overflow the
        # names roster budget — the blurb must stay bounded and say so.
        rows = [{
            "domain_description": f"Domain {i:03d} " + ("n" * 60),
            "outcome_note": "tested; p=0.42",
            "discovery_pass": False,
        } for i in range(105)]
        with mock.patch.object(RD.R, "load_history",
                               return_value=pd.DataFrame(rows)):
            text = RD._history_blurb()
        self.assertIn("History bounded", text)
        hard_ceiling = (RD.HISTORY_PROMPT_MAX_CHARS
                        + RD.HISTORY_PROMPT_NAMES_MAX_CHARS + 2000)
        self.assertLessEqual(len(text), hard_ceiling)


class TestRejectKnownDomains(unittest.TestCase):

    def test_exact_reproposal_dropped(self):
        props = [{"domain": "Kinase inhibitor half-life"},
                 {"domain": "Novel ion channel gating"}]
        known = {RD._norm_domain("Kinase inhibitor half-life")}
        kept = RD._reject_known_domains(props, "A", known, set())
        self.assertEqual([p["domain"] for p in kept], ["Novel ion channel gating"])

    def test_normalization_case_and_whitespace(self):
        props = [{"domain": "  Kinase   Inhibitor  HALF-Life "}]
        known = {RD._norm_domain("kinase inhibitor half-life")}
        kept = RD._reject_known_domains(props, "A", known, set())
        self.assertEqual(kept, [])

    def test_salvageable_domain_stays_reproposable(self):
        props = [{"domain": "Salvage domain"}]
        key = RD._norm_domain("Salvage domain")
        kept = RD._reject_known_domains(props, "B", {key}, {key})
        self.assertEqual(len(kept), 1)

    def test_novel_and_empty_known_passthrough(self):
        props = [{"domain": "Brand new territory"}]
        self.assertEqual(RD._reject_known_domains(props, "A", set(), set()), props)
        self.assertEqual(
            RD._reject_known_domains(props, "A", {RD._norm_domain("other")}, set()),
            props)


if __name__ == "__main__":
    unittest.main()
