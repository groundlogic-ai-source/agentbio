"""Small process-wide request policy for rate-limited external providers.

The policy deliberately has no dependency on a particular HTTP client.  Callers
pass their existing ``requests.get`` function, which keeps source modules easy
to mock while making throttling, retries, and source-health accounting uniform.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import threading
import time
from typing import Any, Callable

import requests


class ProviderCircuitOpen(RuntimeError):
    """Raised without making a request while a provider is cooling down."""


@dataclass(frozen=True)
class ProviderPolicy:
    concurrency: int
    min_interval_seconds: float
    max_attempts: int = 3
    circuit_failure_threshold: int = 3
    circuit_cooldown_seconds: float = 30.0


# NCBI permits three requests/s without an API key; the conservative process
# wide interval prevents concurrent jobs from accidentally exceeding that.
POLICIES = {
    "chembl": ProviderPolicy(concurrency=2, min_interval_seconds=0.20),
    "ncbi": ProviderPolicy(concurrency=1, min_interval_seconds=0.34),
    # GtoPdb is a small academic service and rate-limits harder than its size
    # suggests. A reviewer pass over a large candidate pool fans out to it once
    # per (candidate, target), which was enough to draw sustained HTTP 429s and
    # — because those land in candidate_source_coverage.failures — to block
    # report persistence for the whole run. One in-flight request at a time
    # with a half-second floor keeps a 200-candidate pass inside what the
    # service tolerates; the shared retry path honours Retry-After when it is
    # sent.
    #
    # max_attempts is raised above the default 3 because a single unrecovered
    # 429 is fatal to an entire run, not just to one lookup. A 429 is a real
    # coverage gap -- unlike a 404, we do not learn what the service would have
    # said -- so a failed target fails every candidate, by design. That makes
    # fragility scale with TOP_K_TARGETS: pursuing five targets is five chances
    # to hit a transient error, any one of which discards the run. An
    # Achondroplasia run died exactly this way with FGFR3, the causal gene,
    # fully resolved: one 429 on an FGFR1 structure lookup, 4 of 5 targets
    # fine, whole run lost. Retrying a cheap GET several more times costs
    # seconds; losing the run costs the LLM and structure spend already
    # incurred.
    "gtopdb": ProviderPolicy(
        concurrency=1, min_interval_seconds=0.75, max_attempts=6),
    # Ensembl publishes a ~15 requests/s ceiling and answers a breach with 429
    # plus Retry-After. The positional-association check looks up every target
    # in a disease's list back to back, and screening several diseases in a row
    # breached it: a Wilson disease screen came back with coordinates
    # "unavailable" for all five targets, which then read as no positional
    # risk. An unthrottled lookup turns a rate limit into a clean result, which
    # is the failure mode this codebase keeps removing.
    "ensembl": ProviderPolicy(
        concurrency=1, min_interval_seconds=0.15, max_attempts=5),
}

_lock = threading.Lock()
_semaphores = {name: threading.BoundedSemaphore(policy.concurrency)
               for name, policy in POLICIES.items()}
_next_request_at: dict[str, float] = defaultdict(float)
_consecutive_failures: dict[str, int] = defaultdict(int)
_circuit_open_until: dict[str, float] = defaultdict(float)
_metrics: dict[str, dict[str, int]] = defaultdict(
    lambda: {"attempts": 0, "successes": 0, "failures": 0,
             "retries": 0, "circuit_rejections": 0})


def source_metrics(provider: str | None = None) -> dict[str, dict[str, Any]]:
    """Return a copy of process-local source health counters."""
    with _lock:
        names = (provider,) if provider else tuple(POLICIES)
        now = time.monotonic()
        return {
            name: {
                **_metrics[name],
                "consecutive_failures": _consecutive_failures[name],
                "circuit_open": _circuit_open_until[name] > now,
            }
            for name in names
        }


def _retry_after_seconds(response: Any) -> float | None:
    value = (getattr(response, "headers", {}) or {}).get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, IndexError):
            return None


def _transient_response(response: Any) -> bool:
    return getattr(response, "status_code", 0) == 429 or 500 <= getattr(
        response, "status_code", 0) <= 599


def _reject_if_circuit_open(provider: str) -> None:
    """Atomically reject callers admitted before another caller opened circuit."""
    with _lock:
        if _circuit_open_until[provider] > time.monotonic():
            _metrics[provider]["circuit_rejections"] += 1
            raise ProviderCircuitOpen(f"{provider} circuit open")


def request(
    provider: str,
    request_fn: Callable[..., Any],
    url: str,
    pass_through_statuses: frozenset[int] = frozenset(),
    **kwargs: Any,
) -> Any:
    """Make one provider request under a shared throttle and bounded retries.

    429/5xx and requests timeout/connection errors are retried at most twice.
    A Retry-After header takes precedence over the deterministic exponential
    backoff.  Final transient failures contribute to a short circuit breaker;
    non-transient HTTP errors are raised immediately and do not open it.

    ``pass_through_statuses`` are returned to the caller unraised. Some
    non-2xx statuses are definitive answers rather than failures: a 404 from a
    per-accession endpoint means "this record is not in the database", which
    the caller records as an observed absence. Raising it instead turns a
    clean negative into an apparent outage. That is what happened when GtoPdb
    moved behind this throttle in 915001f: its own
    ``if resp.status_code == 404: return None`` became unreachable, five
    accessions absent from GtoPdb were reported as source unavailability, and
    an Acrodysostosis run failed every target on it.
    """
    if provider not in POLICIES:
        raise ValueError(f"unknown provider policy: {provider}")
    policy = POLICIES[provider]
    _reject_if_circuit_open(provider)

    with _semaphores[provider]:
        for attempt in range(policy.max_attempts):
            # A caller may have waited on the semaphore while a different
            # caller exhausted retries and opened the circuit.  Check both
            # after admission and at each retry boundary before issuing I/O.
            _reject_if_circuit_open(provider)
            with _lock:
                delay = _next_request_at[provider] - time.monotonic()
                # Holding this tiny scheduling lock through the wait makes the
                # interval global even when a provider allows several in-flight
                # requests.  The network call itself happens outside the lock.
                if delay > 0:
                    time.sleep(delay)
                _next_request_at[provider] = (
                    time.monotonic() + policy.min_interval_seconds)
                _metrics[provider]["attempts"] += 1
            try:
                response = request_fn(url, **kwargs)
                transient = _transient_response(response)
                if not transient:
                    if getattr(response, "status_code", None) not in (
                            pass_through_statuses):
                        response.raise_for_status()
                    with _lock:
                        _consecutive_failures[provider] = 0
                        _metrics[provider]["successes"] += 1
                    return response
            except (requests.Timeout, requests.ConnectionError) as exc:
                response = None
                transient = True
                last_error: Exception = exc
            else:
                last_error = requests.HTTPError(
                    f"transient HTTP {getattr(response, 'status_code', 'unknown')}")

            if attempt + 1 < policy.max_attempts:
                with _lock:
                    _metrics[provider]["retries"] += 1
                time.sleep(_retry_after_seconds(response) if response is not None
                           and _retry_after_seconds(response) is not None
                           else 0.25 * (2 ** attempt))
                continue
            with _lock:
                _metrics[provider]["failures"] += 1
                _consecutive_failures[provider] += 1
                if _consecutive_failures[provider] >= policy.circuit_failure_threshold:
                    _circuit_open_until[provider] = (
                        time.monotonic() + policy.circuit_cooldown_seconds)
            raise last_error


def _reset_for_tests() -> None:
    """Reset mutable process state; intentionally private test support."""
    with _lock:
        _next_request_at.clear()
        _consecutive_failures.clear()
        _circuit_open_until.clear()
        _metrics.clear()