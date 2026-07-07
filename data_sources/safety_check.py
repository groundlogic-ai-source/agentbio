"""
Layer 2 market-safety disclosure — web-search supplementary check.

Uses Anthropic claude-sonnet-4-6 with the built-in web_search tool to query
for drug market-withdrawal / black-box-warning history, then runs ONE
constrained classification call (temperature=0, no tools) to render a
YES / NO / UNCLEAR verdict.

Design constraints from spec:
  - Only a YES may trigger the cap+badge gate.
  - NO or UNCLEAR must NEVER be treated as a positive safety signal.
  - The report ALWAYS includes the explicit disclaimer:
      "No market-withdrawal information found in this search;
       this does not confirm the compound is safe."
    for any non-YES verdict — not just when nothing is found.
  - This function never positively clears a compound; it can only flag or stay silent.
"""

import os
from typing import Any

import anthropic

from cache.cache import get, set as cache_set, make_key

_NO_INFO_TEXT = (
    "No market-withdrawal information found in this search; "
    "this does not confirm the compound is safe."
)


def web_safety_check(drug_name: str) -> dict[str, Any]:
    """
    Layer 2: web-search safety check for a candidate compound.

    Step 1 — web search (claude-sonnet-4-6 + web_search_20250305 tool):
        "Has [drug name] ever been withdrawn from any market, received a
        black box warning, or been discontinued for safety reasons? Cite sources."

    Step 2 — constrained classification (temperature=0, no tools):
        "Does this result confirm a market withdrawal or black-box warning for
        safety reasons: YES / NO / UNCLEAR. Cite the specific source."

    Only YES → confirmed=True.  NO or UNCLEAR → confirmed=False, and
    disclosure_text ALWAYS contains the mandatory "does not confirm safe" note.

    Cached 30 days on success; 1 day on error (so transient failures retry soon).

    Returns:
        {
          "confirmed"      : bool   — True ONLY on explicit YES verdict
          "layer"          : "web_search"
          "verdict"        : "YES" | "NO" | "UNCLEAR" | "SKIPPED" | "ERROR"
          "citation"       : str | None   — source cited by the classifier
          "search_summary" : str          — full text extracted from Step 1 response
          "disclosure_text": str          — what to surface in the report
        }
    """
    cache_key = make_key("web_safety_check_v1", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")

    result: dict[str, Any] = {
        "confirmed": False,
        "layer": "web_search",
        "verdict": "SKIPPED",
        "citation": None,
        "search_summary": "",
        "disclosure_text": _NO_INFO_TEXT,
    }

    if not base_url or not api_key:
        result["disclosure_text"] = (
            "Layer 2 web-search check skipped — "
            "AI_INTEGRATIONS_ANTHROPIC_BASE_URL or AI_INTEGRATIONS_ANTHROPIC_API_KEY "
            "not configured. " + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)
        return result

    try:
        client = anthropic.Anthropic(base_url=base_url, api_key=api_key)

        # ── Step 1: web search ────────────────────────────────────────────────
        search_query = (
            f'Has the drug "{drug_name}" ever been withdrawn from any market, '
            f"received a black box warning, or been discontinued for safety "
            f"reasons? Cite sources with URLs."
        )
        search_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": search_query}],
        )

        # Extract all text from the response (model answer incorporating search results)
        search_text_parts = []
        for block in search_response.content:
            if hasattr(block, "text") and block.text:
                search_text_parts.append(block.text)
        search_text = "\n".join(search_text_parts).strip()
        result["search_summary"] = search_text

        if not search_text:
            result["verdict"] = "UNCLEAR"
            result["disclosure_text"] = _NO_INFO_TEXT
            cache_set(cache_key, result, ttl_days=1)
            return result

        # ── Step 2: constrained classification (temperature=0, no tools) ─────
        classification_prompt = (
            f"The following is a web search result about the drug {drug_name!r} "
            f"and any market withdrawal, black-box warning, or safety "
            f"discontinuation:\n\n"
            f"---\n{search_text}\n---\n\n"
            f"Based ONLY on the above text, answer: does this result confirm a "
            f"market withdrawal or black-box warning for safety reasons?\n\n"
            f"Reply in this EXACT format (two lines only, nothing else):\n"
            f"VERDICT: YES | NO | UNCLEAR\n"
            f"CITATION: <the specific source URL or citation, or 'none'>"
        )
        classify_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": classification_prompt}],
        )

        classify_text = ""
        for block in classify_response.content:
            if hasattr(block, "text") and block.text:
                classify_text += block.text
        classify_text = classify_text.strip()

        # Parse verdict and citation from the two-line classifier output
        verdict = "UNCLEAR"
        citation = None
        for line in classify_text.splitlines():
            line = line.strip()
            upper = line.upper()
            if upper.startswith("VERDICT:"):
                raw = line.split(":", 1)[1].strip().upper()
                if "YES" in raw:
                    verdict = "YES"
                elif "NO" in raw and "UNCLEAR" not in raw:
                    verdict = "NO"
                else:
                    verdict = "UNCLEAR"
            elif upper.startswith("CITATION:"):
                raw_cite = line.split(":", 1)[1].strip()
                if raw_cite and raw_cite.lower() not in ("none", "n/a", ""):
                    citation = raw_cite

        result["verdict"] = verdict
        result["citation"] = citation

        if verdict == "YES":
            result["confirmed"] = True
            result["disclosure_text"] = (
                f"MARKET WITHDRAWAL / BLACK-BOX WARNING CONFIRMED by web search "
                f"for {drug_name}. "
                f"Source: {citation or 'see search_summary field'}"
            )
        else:
            # NO or UNCLEAR — must never be treated as a positive signal.
            # Mandatory disclaimer is always present.
            result["confirmed"] = False
            result["disclosure_text"] = _NO_INFO_TEXT

        cache_set(cache_key, result, ttl_days=30)

    except Exception as e:
        print(f"[safety_check] WARNING: web safety check failed for "
              f"'{drug_name}': {e}")
        result["verdict"] = "ERROR"
        result["disclosure_text"] = (
            f"Layer 2 web-search check encountered an error for '{drug_name}': "
            f"{e}. Treating as unconfirmed (no cap applied from Layer 2). "
            + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)

    return result
