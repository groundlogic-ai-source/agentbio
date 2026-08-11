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
from typing import Any, Callable, Optional

_ANTHROPIC_TEXT_MODEL = "claude-sonnet-4-6"
_OPENAI_TEXT_MODEL = "gpt-5.4"

_MAX_ATTEMPTS = 6
_BASE_DELAY_SECONDS = 2.0
_TIMEOUT_SECONDS = 90.0

_rr_lock = threading.Lock()
_rr_counter = [0]

# Cache clients: they are thread-safe for our usage and expensive to rebuild.
_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()


def _available_providers() -> list[str]:
    providers: list[str] = []
    if (os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
            and os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")):
        providers.append("anthropic")
    if (os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
            and os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")):
        providers.append("openai")
    return providers


def _get_client(provider: str) -> Any:
    with _clients_lock:
        client = _clients.get(provider)
        if client is None:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(
                    base_url=os.environ["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"],
                    api_key=os.environ["AI_INTEGRATIONS_ANTHROPIC_API_KEY"],
                    timeout=_TIMEOUT_SECONDS,
                    max_retries=0,  # retries are orchestrated here, not per-call
                )
            else:
                from openai import OpenAI
                client = OpenAI(
                    base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
                    api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
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


def _anthropic_text(prompt: str, system: Optional[str], max_tokens: int) -> str:
    kwargs: dict[str, Any] = {
        "model": _ANTHROPIC_TEXT_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    msg = _get_client("anthropic").messages.create(**kwargs)
    parts = [b.text for b in msg.content
             if getattr(b, "type", None) == "text" and getattr(b, "text", None)]
    return "\n".join(parts).strip()


def _openai_text(prompt: str, system: Optional[str], max_tokens: int) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}]
    resp = _get_client("openai").chat.completions.create(
        model=_OPENAI_TEXT_MODEL,
        max_completion_tokens=max_tokens,
        messages=messages,
    )
    if not resp.choices:
        return ""
    return (resp.choices[0].message.content or "").strip()


def chat_text(prompt: str, *, system: Optional[str] = None,
              max_tokens: int = 512) -> tuple[str, str]:
    """Text-only LLM call with round-robin provider rotation + failover.

    Returns ``(text, provider_used)``.  Raises the last exception if every
    attempt fails.  The starting provider rotates across calls (thread-safe)
    so sustained load is split across both AI-integration providers; a
    transient error (429/5xx/timeout) backs off and tries the next provider.
    """
    providers = _available_providers()
    if not providers:
        raise RuntimeError(
            "no AI-integration providers configured "
            "(AI_INTEGRATIONS_ANTHROPIC_* / AI_INTEGRATIONS_OPENAI_*)")
    with _rr_lock:
        start = _rr_counter[0] % len(providers)
        _rr_counter[0] += 1
    order = providers[start:] + providers[:start]

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_ATTEMPTS):
        provider = order[attempt % len(order)]
        try:
            if provider == "anthropic":
                return _anthropic_text(prompt, system, max_tokens), provider
            return _openai_text(prompt, system, max_tokens), provider
        except Exception as exc:  # noqa: BLE001 — orchestrated retry
            last_exc = exc
            if not _is_transient(exc):
                raise
            print(f"[llm_failover] {provider} transient error "
                  f"(attempt {attempt + 1}/{_MAX_ATTEMPTS}): "
                  f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
            _backoff_sleep(attempt)
    raise RuntimeError(
        f"chat_text exhausted {_MAX_ATTEMPTS} attempts across providers "
        f"{providers}") from last_exc


def call_with_backoff(fn: Callable[[], Any], *, max_attempts: int = 5,
                      label: str = "llm") -> Any:
    """Retry ``fn`` on transient errors with exponential backoff + jitter.

    For provider-bound calls (e.g. web-search tool calls that only one
    provider supports).  Never switches providers; non-transient errors
    propagate immediately.
    """
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — orchestrated retry
            if attempt == max_attempts - 1 or not _is_transient(exc):
                raise
            print(f"[llm_failover] {label} transient error "
                  f"(attempt {attempt + 1}/{max_attempts}): "
                  f"{type(exc).__name__}: {str(exc)[:160]}", flush=True)
            _backoff_sleep(attempt)
