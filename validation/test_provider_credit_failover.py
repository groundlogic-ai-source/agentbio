"""An empty balance on one provider must not take down the call.

Credit exhaustion is neither transient nor fatal. Retrying the SAME provider
cannot help -- the balance will not refill between attempts -- but the OTHER
provider may be funded, which is exactly what failover exists for.

_is_transient() returned False for it, so the call raised immediately and never
asked the alternative. A Duchenne muscular dystrophy run lost its entire
literature gate that way: six unclaimed candidates on NR3C1 went unassessed
because the Anthropic balance was empty, without OpenAI being tried once.

The two providers report the condition incompatibly, which is why it is matched
on message rather than status:

    Anthropic   400 invalid_request_error  "Your credit balance is too low"
    OpenAI      429                        "You have no credits remaining"

A 400 is normally a permanent validation error; a 429 is normally a rate limit.
Neither status alone identifies this, and both normally route the opposite way
-- 400 raises, 429 retries the same provider.
"""

import unittest

from data_sources import llm_failover
from data_sources.llm_failover import _is_provider_exhausted, _is_transient


class AnthropicExhaustionTest(unittest.TestCase):

    MESSAGE = (
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'Your credit balance is too low "
        "to access the Anthropic API. Please go to Plans & Billing to upgrade "
        "or purchase credits.'}}"
    )

    def test_recognised_as_exhaustion(self):
        self.assertTrue(_is_provider_exhausted(Exception(self.MESSAGE)))

    def test_not_misread_as_transient(self):
        """It must not drive a same-provider retry; the balance will not refill."""
        self.assertFalse(_is_transient(Exception(self.MESSAGE)))


class OpenAIExhaustionTest(unittest.TestCase):

    MESSAGE = (
        "Error code: 429 - {'error': {'message': 'You have no credits "
        "remaining. Add credits to continue using the API'}}"
    )

    def test_recognised_as_exhaustion(self):
        self.assertTrue(_is_provider_exhausted(Exception(self.MESSAGE)))

    def test_the_429_would_otherwise_look_transient(self):
        """Why exhaustion is checked FIRST in the handler.

        A 429 reads as a rate limit, so without the exhaustion check this would
        back off and retry the same empty provider until attempts ran out.
        """
        self.assertTrue(_is_transient(Exception(self.MESSAGE)))

    def test_quota_phrasing_is_also_recognised(self):
        for msg in ("insufficient_quota",
                    "You exceeded your current quota, please check your plan"):
            self.assertTrue(_is_provider_exhausted(Exception(msg)), msg)


class NonExhaustionTest(unittest.TestCase):
    """Ordinary failures must keep their existing routing."""

    def test_rate_limit_is_not_exhaustion(self):
        self.assertFalse(_is_provider_exhausted(
            Exception("Error code: 429 - rate limit exceeded, retry shortly")))

    def test_validation_error_is_not_exhaustion(self):
        self.assertFalse(_is_provider_exhausted(
            Exception("Error code: 400 - max_tokens must be positive")))

    def test_server_error_is_not_exhaustion(self):
        self.assertFalse(_is_provider_exhausted(Exception("503 overloaded")))


class HandlerWiringTest(unittest.TestCase):

    def test_exhaustion_is_checked_before_transient(self):
        """Order matters: OpenAI reports exhaustion as a 429."""
        import inspect
        source = inspect.getsource(llm_failover.chat_text)
        self.assertLess(
            source.index("_is_provider_exhausted(exc)"),
            source.index("if not _is_transient(exc):"),
            "exhaustion must be classified before the transient check")

    def test_exhausted_provider_is_skipped_not_retried(self):
        import inspect
        source = inspect.getsource(llm_failover.chat_text)
        self.assertIn("exhausted.add(provider)", source)
        self.assertIn("if provider in exhausted:", source)

    def test_no_backoff_is_spent_on_an_empty_balance(self):
        """Waiting cannot refill an account."""
        import inspect
        source = inspect.getsource(llm_failover.chat_text)
        block = source.split("if _is_provider_exhausted(exc):")[1]
        block = block.split("if not _is_transient(exc):")[0]
        self.assertNotIn("_backoff_sleep", block)

    def test_all_providers_empty_says_so_plainly(self):
        """Billing is not an infrastructure problem, and read as one all day."""
        import inspect
        source = inspect.getsource(llm_failover.chat_text)
        self.assertIn("out of credit", source)
        self.assertIn("not a source outage", source)


if __name__ == "__main__":
    unittest.main()
