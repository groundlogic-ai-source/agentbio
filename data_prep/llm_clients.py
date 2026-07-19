"""
Thin LLM client wrappers for the discovery pipeline.

Two providers via the Replit AI Integrations proxy (keys auto-provisioned):
  - Claude Opus 4.8   (claude-opus-4-8)  — LLM A and the Lead reviewer
  - GPT-5.6 Sol       (gpt-5.6-sol)      — LLM B, via the OpenAI Responses API

IMPORTANT — temperature: the MEGA PROMPT asks for temperature 0.7, but BOTH models
forbid it: claude-opus-4-8 returns a 400 if temperature/top_p/top_k are set at all,
and the gpt-5.6 fleet forces temperature=1. We therefore OMIT temperature on both
and rely on each model's default sampling. This is a forced API constraint, not a
design choice, and is surfaced in the run report.
"""
from __future__ import annotations

import json
import os

from anthropic import Anthropic
from openai import OpenAI

OPUS_MODEL = "claude-opus-4-8"
SOL_MODEL = "gpt-5.6-sol"

_anthropic: Anthropic | None = None
_openai: OpenAI | None = None


def _ac() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(
            api_key=os.environ["AI_INTEGRATIONS_ANTHROPIC_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"],
        )
    return _anthropic


def _oc() -> OpenAI:
    global _openai
    if _openai is None:
        _openai = OpenAI(
            api_key=os.environ["AI_INTEGRATIONS_OPENAI_API_KEY"],
            base_url=os.environ["AI_INTEGRATIONS_OPENAI_BASE_URL"],
        )
    return _openai


def opus(prompt: str, max_tokens: int = 8000) -> str:
    """Call Claude Opus 4.8 (temperature omitted — deprecated on this model)."""
    resp = _ac().messages.create(
        model=OPUS_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text


def sol(prompt: str, max_output_tokens: int = 8000) -> str:
    """Call GPT-5.6 Sol via the OpenAI Responses API (temperature not specifiable)."""
    resp = _oc().responses.create(
        model=SOL_MODEL,
        input=prompt,
        max_output_tokens=max_output_tokens,
    )
    return resp.output_text


def extract_json(text: str):
    """
    Pull the first top-level JSON array or object out of an LLM reply. Tolerates
    ```json fences and leading/trailing prose.

    Uses balanced bracket + in-string tracking to find the correct matching close
    bracket, so trailing prose (even prose containing { or } characters) does not
    corrupt the slice.
    """
    t = text.strip()
    if "```" in t:
        # take the content of the first fenced block
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                t = p
                break

    _openers = {"[", "{"}
    _closers = {"]": "[", "}": "{"}

    for i, start_ch in enumerate(t):
        if start_ch not in _openers:
            continue
        # Walk forward tracking string context and bracket depth.
        in_string = False
        escape = False
        stack: list[str] = []
        j = i
        while j < len(t):
            ch = t[j]
            if escape:
                escape = False
                j += 1
                continue
            if ch == "\\" and in_string:
                escape = True
                j += 1
                continue
            if ch == '"':
                in_string = not in_string
                j += 1
                continue
            if in_string:
                j += 1
                continue
            if ch in _openers:
                stack.append(ch)
            elif ch in _closers:
                if not stack or stack[-1] != _closers[ch]:
                    break  # mismatched bracket — not valid JSON from here
                stack.pop()
                if not stack:
                    # found the matching close — try to parse
                    candidate = t[i : j + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # try the next opener position
            j += 1

    raise ValueError(f"no valid JSON found in LLM reply: {text[:200]!r}")


def extract_json_list(text: str) -> list:
    """
    Like extract_json(), but guarantees a list of dict elements.

    Handles the common LLM deviation of wrapping the requested array in an object
    (e.g. {"hypotheses": [...]} or {"domains": [...]}) instead of returning a bare
    array. If the parsed result is such a dict, the first list-valued field is
    unwrapped. A single dict is wrapped into a one-element list. Non-dict elements
    are dropped, so downstream code can safely call .get() on every element.
    """
    parsed = extract_json(text)

    if isinstance(parsed, dict):
        # Unwrap the first list-valued field (the wrapped array); otherwise treat
        # the dict itself as a single element.
        wrapped = next((v for v in parsed.values() if isinstance(v, list)), None)
        parsed = wrapped if wrapped is not None else [parsed]

    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array in LLM reply: {text[:200]!r}")

    return [el for el in parsed if isinstance(el, dict)]
