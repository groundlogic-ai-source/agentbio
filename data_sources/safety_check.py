"""
Layer 2 market-safety disclosure — web-search supplementary check.

Uses Anthropic claude-sonnet-4-6 with the built-in web_search tool to query
for drug market-withdrawal / black-box-warning history, then runs ONE
constrained classification call (temperature=0, no tools) answering TWO
separate questions: WITHDRAWAL and BLACK_BOX, each YES / NO / UNCLEAR.

Design constraints from spec:
  - Only WITHDRAWAL: YES may trigger the cap+badge gate.  A black-box warning
    alone NEVER caps — >30% of marketed drugs carry one; it feeds the
    disclosure-only advisory (mirroring Layer 1 ChEMBL semantics).  The v1
    classifier conflated the two in a single question, letting boxed-warning
    drugs (e.g. lamotrigine) inherit the hard cap; that regression is guarded
    by validation/test_safety_layer2_split.py — do not re-merge the questions.
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
from data_sources.llm_failover import call_with_backoff, chat_text

_NO_INFO_TEXT = (
    "No market-withdrawal information found in this search; "
    "this does not confirm the compound is safe."
)
_AI_TIMEOUT_SECONDS = 60.0
_AI_MAX_RETRIES = 0


def web_safety_check(drug_name: str) -> dict[str, Any]:
    """
    Layer 2: web-search safety check for a candidate compound.

    Step 1 — web search (claude-sonnet-4-6 + web_search_20250305 tool):
        "Has [drug name] ever been withdrawn from any market, received a
        black box warning, or been discontinued for safety reasons? Cite sources."

    Step 2 — constrained classification (temperature=0, no tools), TWO
        separate questions on the same search text:
        "1. WITHDRAWAL: withdrawn from any market or discontinued for safety
            reasons? (a black-box warning alone is NOT a withdrawal)
         2. BLACK_BOX: carries a regulatory black-box (boxed) warning?
         Answer each YES / NO / UNCLEAR. Cite the specific source."

    Only YES → confirmed=True.  NO or UNCLEAR → confirmed=False, and
    disclosure_text ALWAYS contains the mandatory "does not confirm safe" note.

    Cached 30 days on success; 1 day on error (so transient failures retry soon).

    Returns:
        {
          "confirmed"          : bool — True ONLY on WITHDRAWAL: YES (a boxed
                                        warning alone NEVER sets this)
          "black_box_advisory" : bool — True on BLACK_BOX: YES when no
                                        withdrawal is confirmed (disclosure only)
          "layer"              : "web_search"
          "verdict"            : withdrawal verdict — "YES" | "NO" | "UNCLEAR" |
                                        "SKIPPED" | "ERROR"
          "black_box_verdict"  : black-box verdict — same value set
          "citation"           : str | None   — source cited by the classifier
          "search_summary"     : str  — full text extracted from Step 1 response
          "disclosure_text"    : str  — what to surface in the report
        }
    """
    # v2 key: v1 asked ONE conflated question ("withdrawal OR black-box
    # warning?"), so a black-box-only drug (e.g. lamotrigine) rendered a YES
    # and inherited the hard safety cap — bypassing the Layer 1 separation of
    # boxed warnings from genuine withdrawals.  v2 asks the two questions
    # separately; only WITHDRAWAL: YES may set confirmed=True.
    cache_key = make_key("web_safety_check_v2", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")

    result: dict[str, Any] = {
        "confirmed": False,
        "black_box_advisory": False,
        "layer": "web_search",
        "verdict": "SKIPPED",
        "black_box_verdict": "SKIPPED",
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
        client = anthropic.Anthropic(
            base_url=base_url,
            api_key=api_key,
            timeout=_AI_TIMEOUT_SECONDS,
            max_retries=_AI_MAX_RETRIES,
        )

        # ── Step 1: web search ────────────────────────────────────────────────
        search_query = (
            f'Has the drug "{drug_name}" ever been withdrawn from any market, '
            f"received a black box warning, or been discontinued for safety "
            f"reasons? Cite sources with URLs."
        )
        # Provider-bound web-search tool: retry 429/5xx with backoff (no
        # cross-provider failover — the tool API is Anthropic-specific).
        search_response = call_with_backoff(
            lambda: client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2048,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{"role": "user", "content": search_query}],
            ),
            label="safety-web-search",
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
        # Two SEPARATE questions.  A black-box warning is NOT a withdrawal:
        # >30% of approved drugs carry one and remain fully marketed.  Only a
        # WITHDRAWAL: YES may trigger the hard cap; BLACK_BOX: YES feeds the
        # disclosure advisory instead.
        classification_prompt = (
            f"The following is a web search result about the drug {drug_name!r} "
            f"and any market withdrawal, black-box warning, or safety "
            f"discontinuation:\n\n"
            f"---\n{search_text}\n---\n\n"
            f"Based ONLY on the above text, answer BOTH questions:\n"
            f"1. WITHDRAWAL: Was the drug withdrawn from any market, or "
            f"discontinued FOR SAFETY REASONS? (A black-box warning alone is "
            f"NOT a withdrawal.)\n"
            f"2. BLACK_BOX: Does the drug carry a regulatory black-box "
            f"(boxed) warning?\n\n"
            f"Reply in this EXACT format (three lines only, nothing else):\n"
            f"WITHDRAWAL: YES | NO | UNCLEAR\n"
            f"BLACK_BOX: YES | NO | UNCLEAR\n"
            f"CITATION: <the specific source URL or citation, or 'none'>"
        )
        # Text-only classification: round-robin providers + 429 failover.
        classify_text, _provider = chat_text(classification_prompt,
                                             max_tokens=256)
        classify_text = classify_text.strip()

        # Parse the three-line classifier output.  Backward compatibility: if
        # the model ignores the two-question format and replies with a bare
        # "VERDICT: ..." line (old format), treat it as the WITHDRAWAL answer.
        withdrawal = "UNCLEAR"
        black_box = "UNCLEAR"
        citation = None

        def _parse_verdict(raw: str) -> str:
            raw = raw.strip().upper()
            if "YES" in raw:
                return "YES"
            if "NO" in raw and "UNCLEAR" not in raw:
                return "NO"
            return "UNCLEAR"

        for line in classify_text.splitlines():
            line = line.strip()
            upper = line.upper()
            if upper.startswith("WITHDRAWAL:"):
                withdrawal = _parse_verdict(line.split(":", 1)[1])
            elif upper.startswith("BLACK_BOX:") or upper.startswith("BLACK BOX:"):
                black_box = _parse_verdict(line.split(":", 1)[1])
            elif upper.startswith("VERDICT:"):
                withdrawal = _parse_verdict(line.split(":", 1)[1])
            elif upper.startswith("CITATION:"):
                raw_cite = line.split(":", 1)[1].strip()
                if raw_cite and raw_cite.lower() not in ("none", "n/a", ""):
                    citation = raw_cite

        result["verdict"] = withdrawal
        result["black_box_verdict"] = black_box
        result["citation"] = citation

        if withdrawal == "YES":
            result["confirmed"] = True
            result["disclosure_text"] = (
                f"MARKET WITHDRAWAL / SAFETY DISCONTINUATION CONFIRMED by web "
                f"search for {drug_name}. "
                f"Source: {citation or 'see search_summary field'}"
            )
        else:
            # NO or UNCLEAR — must never be treated as a positive signal.
            # Mandatory disclaimer is always present.
            result["confirmed"] = False
            result["disclosure_text"] = _NO_INFO_TEXT
            if black_box == "YES":
                # Disclosure-only advisory, mirroring the Layer 1 semantics:
                # boxed warning present, drug still marketed — never a cap.
                result["black_box_advisory"] = True

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
