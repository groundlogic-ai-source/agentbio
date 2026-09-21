"""Shared LLM resilience helpers.

Two failure modes motivated this module (observed during a 100-claim live
audit run, 2026-08-11):

  * Sustained HTTP 429 rate-limit blocks on BOTH AI-integration providers
    (openai/gpt-5.x and anthropic/claude-*) when the audit pipeline fans out
    ~100 claims, each making several LLM calls.  Clients were constructed
    with ``max_retries=0``/low, so a single 429 killed the call and the lane
    crashed (recorded as an abstention).

  * No cross-provider redundancy: a text-only classification call that
    failed on one provider had no way to use the other, even though both
    providers are configured in this environment.

Design:

  * :func:`chat_text` — for TEXT-ONLY calls (classification, YES/NO gates,
    extraction).  Round-robins the starting provider across calls so load
    is spread roughly evenly between Anthropic and OpenAI, and on a
    transient error (429 / 5xx / timeout / overload) backs off and fails
    over to the other provider.  Deterministic decoding (temperature=0
    where the provider supports it) is preserved.

  * :func:`call_with_backoff` — for PROVIDER-BOUND calls (web-search tool
    calls whose tool API exists on only one provider).  Retries with
    exponential backoff + jitter on transient errors; never switches
    providers, so tool semantics are unchanged.

Nothing here retries deterministic validation errors (4xx other than 429)
or changes prompt/parse logic — call-site behavior is unchanged except for
resilience.
"""
from __future__ import annotations

import os
import random
import threading
import time
import json
from typing import Any, Callable, Optional

# Model tiers are deliberately explicit.  Existing callers use STANDARD unless
# they opt in to CHEAP; do not silently downgrade a call that influences a gate,
# score, or report.
MODEL_TIER_CRITICAL = "critical"
MODEL_TIER_STANDARD = "standard"
MODEL_TIER_CHEAP = "cheap"
_MODEL_TIERS = frozenset({
    MODEL_TIER_CRITICAL, MODEL_TIER_STANDARD, MODEL_TIER_CHEAP,
})

ANTHROPIC_CRITICAL_TEXT_MODEL = "claude-sonnet-4-6"
OPENAI_CRITICAL_TEXT_MODEL = "gpt-5.4"
# Standard is currently identical to critical for capability preservation.
ANTHROPIC_STANDARD_TEXT_MODEL = ANTHROPIC_CRITICAL_TEXT_MODEL
OPENAI_STANDARD_TEXT_MODEL = OPENAI_CRITICAL_TEXT_MODEL
# These are opt-in routing targets for low-risk classification/narration only.
# Environment overrides make the tier deploy-configurable without changing code.
ANTHROPIC_CHEAP_TEXT_MODEL = os.environ.get(
    "AGENTBIO_ANTHROPIC_CHEAP_TEXT_MODEL", "claude-haiku-4-5")
OPENAI_CHEAP_TEXT_MODEL = os.environ.get(
    "AGENTBIO_OPENAI_CHEAP_TEXT_MODEL", "gpt-5-mini")

# Backward-compatible internal names retained for callers/tests that imported
# them before tiers were introduced.
_ANTHROPIC_TEXT_MODEL = ANTHROPIC_STANDARD_TEXT_MODEL
_OPENAI_TEXT_MODEL = OPENAI_STANDARD_TEXT_MODEL

_MAX_ATTEMPTS = 6
_BASE_DELAY_SECONDS = 2.0
_TIMEOUT_SECONDS = 90.0
_DEFAULT_MAX_CONCURRENT_PER_PROVIDER = max(
    1, int(os.environ.get("AGENTBIO_LLM_MAX_CONCURRENT_PER_PROVIDER", "2")))
_DEFAULT_MIN_INTERVAL_SECONDS = max(
    0.0, float(os.environ.get("AGENTBIO_LLM_MIN_INTERVAL_SECONDS", "0.25")))

_rr_lock = threading.Lock()
_rr_counter = [0]

# Cache clients: they are thread-safe for our usage and expensive to rebuild.
_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()


class _ProviderAdmissionScheduler:
    """Process-wide per-provider concurrency and start-rate admission control.

    ``Condition.wait`` always releases ``_condition`` while waiting, so a
    throttled caller never blocks releases or admissions for another provider.
    """

    def __init__(self, *, max_concurrent: int = _DEFAULT_MAX_CONCURRENT_PER_PROVIDER,
                 min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS) -> None:
        self._max_concurrent = max(1, max_concurrent)
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._condition = threading.Condition()
        self._active: dict[str, int] = {}
        self._next_start: dict[str, float] = {}

    def _try_acquire_locked(self, provider: str, now: float) -> Optional[float]:
        """Reserve a slot, or return seconds until the next possible admission."""
        active = self._active.get(provider, 0)
        next_start = self._next_start.get(provider, 0.0)
        if active < self._max_concurrent and now >= next_start:
            self._active[provider] = active + 1
            self._next_start[provider] = now + self._min_interval_seconds
            return None
        # A full concurrency limit has no clock-only deadline; a release will
        # notify waiters. A rate limit has a precise deadline.
        return max(0.0, next_start - now) if now < next_start else 0.0

    def acquire(self, provider: str) -> None:
        """Block until this provider has both a slot and a start-rate permit."""
        with self._condition:
            while True:
                wait_seconds = self._try_acquire_locked(provider, time.monotonic())
                if wait_seconds is None:
                    return
                # Condition.wait releases the global scheduler lock.
                self._condition.wait(timeout=wait_seconds or None)

    def release(self, provider: str) -> None:
        with self._condition:
            active = self._active.get(provider, 0)
            if active <= 0:
                raise RuntimeError(f"LLM scheduler release without admission: {provider}")
            self._active[provider] = active - 1
            self._condition.notify_all()


_provider_scheduler = _ProviderAdmissionScheduler()


def text_model_for(provider: str, tier: str = MODEL_TIER_STANDARD) -> str:
    """Return the configured text model for an explicit provider/tier pair.

    ``CHEAP`` is a routing hook, not a policy change: callers must request it
    explicitly and should use it only for low-risk classification or narration.
    """
    if tier not in _MODEL_TIERS:
        raise ValueError(f"unknown LLM model tier: {tier!r}")
    models = {
        "anthropic": {
            MODEL_TIER_CRITICAL: ANTHROPIC_CRITICAL_TEXT_MODEL,
            MODEL_TIER_STANDARD: ANTHROPIC_STANDARD_TEXT_MODEL,
            MODEL_TIER_CHEAP: ANTHROPIC_CHEAP_TEXT_MODEL,
        },
        "openai": {
            MODEL_TIER_CRITICAL: OPENAI_CRITICAL_TEXT_MODEL,
            MODEL_TIER_STANDARD: OPENAI_STANDARD_TEXT_MODEL,
            MODEL_TIER_CHEAP: OPENAI_CHEAP_TEXT_MODEL,
        },
    }
    try:
        return models[provider][tier]
    except KeyError as exc:
        raise ValueError(f"unknown LLM provider: {provider!r}") from exc


def _token_usage(response: Any) -> Optional[dict[str, int]]:
    """Normalize the subset of SDK usage metadata that is actually present."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    def read(*names: str) -> Optional[int]:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    normalized = {
        "input_tokens": read("input_tokens", "prompt_tokens"),
        "output_tokens": read("output_tokens", "completion_tokens"),
        "total_tokens": read("total_tokens"),
    }
    return {key: value for key, value in normalized.items() if value is not None} or None


def _emit_telemetry(*, provider: Optional[str], model: Optional[str],
                    operation: Optional[str], attempt: int,
                    latency_seconds: float, success: bool,
                    response: Any = None, error: Optional[Exception] = None) -> None:
    """Emit a safe JSON event; prompts, responses, keys, and error text stay out."""
    event: dict[str, Any] = {
        "event": "llm_call",
        "provider": provider,
        "model": model,
        "operation": operation,
        "attempt": attempt,
        "latency_ms": round(latency_seconds * 1000, 2),
        "success": success,
        # Exception class is actionable without risking prompt/provider payloads.
        "error": None if success else type(error).__name__,
    }
    usage = _token_usage(response) if success else None
    if usage:
        event["token_usage"] = usage
    print(f"[llm_telemetry] {json.dumps(event, sort_keys=True)}", flush=True)


def _available_providers() -> list[str]:
    # base_url is an optional override (e.g. for a proxy); its absence no longer
    # disqualifies a provider — the SDK falls back to the official endpoint.
    providers: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append("anthropic")
    if os.environ.get("OPENAI_API_KEY"):
        providers.append("openai")
    return providers


def _get_client(provider: str) -> Any:
    with _clients_lock:
        client = _clients.get(provider)
        if client is None:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(
                    base_url=os.environ.get("ANTHROPIC_BASE_URL"),  # None -> SDK default
                    api_key=os.environ["ANTHROPIC_API_KEY"],
                    timeout=_TIMEOUT_SECONDS,
                    max_retries=0,  # retries are orchestrated here, not per-call
                )
            else:
                from openai import OpenAI
                client = OpenAI(
                    base_url=os.environ.get("OPENAI_BASE_URL"),  # None -> SDK default
                    api_key=os.environ["OPENAI_API_KEY"],
                    timeout=_TIMEOUT_SECONDS,
                    max_retries=0,
                )
            _clients[provider] = client
        return client


def _is_transient(exc: Exception) -> bool:
    """429 / 5xx / overload / timeout — worth retrying; 4xx validation is not."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate limit" in msg or "429" in msg:
        return True
    if "overload" in msg or "529" in msg:
        return True
    if "timeout" in name or "timed out" in msg:
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return True
    for token in ("500", "502", "503", "504"):
        if f" {token}" in msg or f"{token} " in msg or msg.endswith(token):
            return True
    return False


def _backoff_sleep(attempt: int) -> None:
    delay = _BASE_DELAY_SECONDS * (2 ** attempt) + random.uniform(0, 1.5)
    time.sleep(min(delay, 60.0))


def _anthropic_text(prompt: str, system: Optional[str], max_tokens: int,
                    model: str) -> tuple[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        # temperature omitted — this SDK/model combination rejects the kwarg
        # outright, even at 0 (TypeError, not a 400). Discovered 2026-09-21
        # deploying off Replit; likely masked there by an older pinned SDK
        # version. Relying on default sampling instead.
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    msg = _get_client("anthropic").messages.create(**kwargs)
    parts = [b.text for b in msg.content
             if getattr(b, "type", None) == "text" and getattr(b, "text", None)]
    return "\n".join(parts).strip(), msg


def _openai_text(prompt: str, system: Optional[str], max_tokens: int,
                 model: str) -> tuple[str, Any]:
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    resp = _get_client("openai").chat.completions.create(
        model=model,
        max_completion_tokens=max_tokens,
        messages=messages,
    )
    if not resp.choices:
        return "", resp
    return (resp.choices[0].message.content or "").strip(), resp


def chat_text(prompt: str, *, system: Optional[str] = None,
              max_tokens: int = 512,
              model_tier: str = MODEL_TIER_STANDARD,
              operation_label: Optional[str] = None) -> tuple[str, str]:
    """Text-only LLM call with round-robin provider rotation + failover.

    Returns ``(text, provider_used)``.  Raises the last exception if every
    attempt fails.  The starting provider rotates across calls (thread-safe)
    so sustained load is split across both AI-integration providers; a
    transient error (429/5xx/timeout) backs off and tries the next provider.
    """
    # Validate before checking credentials, so an invalid routing request is
    # never hidden by a local configuration error.
    if model_tier not in _MODEL_TIERS:
        raise ValueError(f"unknown LLM model tier: {model_tier!r}")
    providers = _available_providers()
    if not providers:
        raise RuntimeError(
            "no AI-integration providers configured "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY)")
    with _rr_lock:
        start = _rr_counter[0] % len(providers)
        _rr_counter[0] += 1
    order = providers[start:] + providers[:start]

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        provider = order[attempt % len(order)]
        model = text_model_for(provider, model_tier)
        started = time.monotonic()
        try:
            _provider_scheduler.acquire(provider)
            try:
                if provider == "anthropic":
                    text, response = _anthropic_text(prompt, system, max_tokens, model)
                else:
                    text, response = _openai_text(prompt, system, max_tokens, model)
            finally:
                _provider_scheduler.release(provider)
            _emit_telemetry(
                provider=provider, model=model,
                operation=operation_label or "chat_text", attempt=attempt + 1,
                latency_seconds=time.monotonic() - started, success=True,
                response=response)
            return text, provider
        except Exception as exc:  # noqa: BLE001 — orchestrated retry
            last_exc = exc
            _emit_telemetry(
                provider=provider, model=model,
                operation=operation_label or "chat_text", attempt=attempt + 1,
                latency_seconds=time.monotonic() - started, success=False,
                error=exc)
            if not _is_transient(exc):
                raise
            print(f"[llm_failover] {provider} transient error "
                  f"(attempt {attempt + 1}/{_MAX_ATTEMPTS}): "
                   f"{type(exc).__name__}", flush=True)
            _backoff_sleep(attempt)
    raise RuntimeError(
        f"chat_text exhausted {_MAX_ATTEMPTS} attempts across providers "
        f"{providers}") from last_exc


def call_with_backoff(fn: Callable[[], Any], *, max_attempts: int = 5,
                      label: str = "llm", provider: Optional[str] = None,
                      model: Optional[str] = None) -> Any:
    """Retry ``fn`` on transient errors with exponential backoff + jitter.

    For provider-bound calls (e.g. web-search tool calls that only one
    provider supports).  Never switches providers; non-transient errors
    propagate immediately.
    """
    for attempt in range(max_attempts):
        started = time.monotonic()
        try:
            if provider:
                _provider_scheduler.acquire(provider)
            try:
                response = fn()
            finally:
                if provider:
                    _provider_scheduler.release(provider)
            _emit_telemetry(
                provider=provider, model=model, operation=label,
                attempt=attempt + 1, latency_seconds=time.monotonic() - started,
                success=True, response=response)
            return response
        except Exception as exc:  # noqa: BLE001 — orchestrated retry
            _emit_telemetry(
                provider=provider, model=model, operation=label,
                attempt=attempt + 1, latency_seconds=time.monotonic() - started,
                success=False, error=exc)
            if attempt == max_attempts - 1 or not _is_transient(exc):
                raise
            print(f"[llm_failover] {label} transient error "
                  f"(attempt {attempt + 1}/{max_attempts}): "
                   f"{type(exc).__name__}", flush=True)
            _backoff_sleep(attempt)
