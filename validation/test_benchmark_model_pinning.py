"""Benchmark-era components must keep the models the benchmark ran on.

STANDARD inherits from CRITICAL and is the DEFAULT tier, so changing either
silently swaps the model under every caller that does not name a tier. Three of
those are benchmark-era:

    data_sources/pubmed.py          the biologist's PubMed gating
    data_sources/safety_check.py    safety classification
    data_sources/clinicaltrials.py  trial relevance

Updating CRITICAL to a newer Sonnet looks harmless and is not: the frozen
benchmark results would stop describing the system that produced them, and the
numbers are quoted externally. Newer models exist and are deliberately not
adopted here.

Post-benchmark gates opt into CHEAP, which is free to move. That is where the
literature-limitation gate runs, and it is where cost should be taken out --
the gate verifies every classifier label against the retrieved text in
deterministic Python, so the model proposes and the code checks.
"""

import unittest

from data_sources import llm_failover as lf


class BenchmarkPinningTest(unittest.TestCase):

    #: The models the frozen benchmark ran on. Changing either invalidates the
    #: comparability of every frozen result.
    PINNED_ANTHROPIC = "claude-sonnet-4-6"
    PINNED_OPENAI = "gpt-5.4"

    def test_critical_anthropic_model_is_pinned(self):
        self.assertEqual(lf.ANTHROPIC_CRITICAL_TEXT_MODEL,
                         self.PINNED_ANTHROPIC)

    def test_critical_openai_model_is_pinned(self):
        self.assertEqual(lf.OPENAI_CRITICAL_TEXT_MODEL, self.PINNED_OPENAI)

    def test_standard_still_inherits_from_critical(self):
        """If this ever diverges, the pinning above stops protecting callers."""
        self.assertEqual(lf.ANTHROPIC_STANDARD_TEXT_MODEL,
                         lf.ANTHROPIC_CRITICAL_TEXT_MODEL)
        self.assertEqual(lf.OPENAI_STANDARD_TEXT_MODEL,
                         lf.OPENAI_CRITICAL_TEXT_MODEL)

    def test_newer_models_are_not_adopted_in_the_pinned_tiers(self):
        for newer in ("claude-sonnet-5", "claude-sonnet-5-5", "claude-opus-5-5"):
            self.assertNotEqual(lf.ANTHROPIC_CRITICAL_TEXT_MODEL, newer)
            self.assertNotEqual(lf.ANTHROPIC_STANDARD_TEXT_MODEL, newer)

    def test_untiered_callers_resolve_to_the_pinned_model(self):
        """The default path is what benchmark-era components actually take."""
        self.assertEqual(lf.text_model_for("anthropic"), self.PINNED_ANTHROPIC)
        self.assertEqual(lf.text_model_for("openai"), self.PINNED_OPENAI)


class CheapTierIsFreeToMoveTest(unittest.TestCase):

    def test_cheap_tier_is_distinct_from_the_pinned_models(self):
        self.assertNotEqual(lf.ANTHROPIC_CHEAP_TEXT_MODEL,
                            lf.ANTHROPIC_CRITICAL_TEXT_MODEL)
        self.assertNotEqual(lf.OPENAI_CHEAP_TEXT_MODEL,
                            lf.OPENAI_CRITICAL_TEXT_MODEL)

    def test_cheap_tier_is_environment_overridable(self):
        """So a model can be swapped at deploy time without touching code."""
        import inspect
        source = inspect.getsource(lf)
        self.assertIn("AGENTBIO_ANTHROPIC_CHEAP_TEXT_MODEL", source)
        self.assertIn("AGENTBIO_OPENAI_CHEAP_TEXT_MODEL", source)

    def test_anthropic_cheap_slot_is_not_a_sonnet(self):
        """Sonnet 5.5 is cheaper per task than Sonnet 5, and still far above
        Haiku. It does not belong in the cost tier."""
        self.assertNotIn("sonnet", lf.ANTHROPIC_CHEAP_TEXT_MODEL.lower())


class LiteratureGateUsesCheapTest(unittest.TestCase):
    """The heaviest LLM consumer, and the safest place to economise."""

    def test_gate_requests_the_cheap_tier(self):
        import inspect
        from data_sources import literature_limitation
        source = inspect.getsource(literature_limitation)
        self.assertIn("model_tier=MODEL_TIER_CHEAP", source)
        self.assertNotIn("model_tier=MODEL_TIER_CRITICAL", source)

    def test_its_output_is_still_verified_in_code(self):
        """What makes a cheap model acceptable here at all."""
        from data_sources.literature_limitation import classify_record_validity
        self.assertTrue(callable(classify_record_validity))


if __name__ == "__main__":
    unittest.main()
