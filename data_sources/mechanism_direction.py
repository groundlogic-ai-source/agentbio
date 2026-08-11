"""
Mechanism-direction compatibility check (Stage 2, post-Reviewer scoring).

Two-step check using OpenAI GPT with web search:
  Step 1 — gpt-5.4 + web_search_preview tool:
      Retrieve the core pathophysiological mechanism of the disease and reason
      about whether the drug's action ON THE SPECIFIC TARGET (not its systemic
      downstream class) is directionally compatible with the disease.
      Also retrieves any known clinical use of the drug for this disease.
  Step 2 — gpt-5.4 constrained classification:
      Given Step 1 text and the drug's action_type/target, classify:
        DIRECTIONALLY_COMPATIBLE / DIRECTIONALLY_INCOMPATIBLE / INSUFFICIENT_INFO
      with a one-sentence cited reason.  Must not introduce any claim not present
      in the Step 1 retrieved text.

Key design constraints:
  1. The drug appears in the candidate list because it has IC50/Ki BIOACTIVITY
     against target_symbol in ChEMBL.  The direction check asks "would
     [action_type] acting ON [target_symbol] correct or worsen [disease]?"
  2. HIGH-CONFIDENCE threshold for INCOMPATIBLE: only classify
     DIRECTIONALLY_INCOMPATIBLE when the incompatibility is unambiguous,
     directly stated by the retrieved text, and mechanistically straightforward.
     Complex multi-step or multi-protein mechanisms → INSUFFICIENT_INFO.
  3. Clinical-use anchor: if the drug is an approved/investigational treatment
     for this disease or a closely related condition through this target pathway,
     that is definitive evidence of DIRECTIONALLY_COMPATIBLE.

Only a DIRECTIONALLY_INCOMPATIBLE verdict triggers a composite_score cap.
DIRECTIONALLY_COMPATIBLE and INSUFFICIENT_INFO leave the score unchanged
(fail-open, same philosophy as safety Layer 2's NO/UNCLEAR outcomes).

Design mirrors data_sources/safety_check.py (Anthropic Layer 2).

GPT-5 note: temperature parameter is not supported for the gpt-5.x model
family.  The classification step uses a strict format-constrained prompt
(single-word verdict token as the first token of the line) to achieve
equivalent determinism.  Any response deviating from the
VERDICT:/REASON:/CITATIONS: format is parsed as INSUFFICIENT_INFO — fail-open,
never false-positive incompatible.
"""

import os
from typing import Any

from openai import OpenAI

from cache.cache import get, set as cache_set, make_key
from data_sources import holdout
from data_sources.llm_failover import call_with_backoff, chat_text

VERDICT_INCOMPATIBLE = "DIRECTIONALLY_INCOMPATIBLE"
VERDICT_COMPATIBLE   = "DIRECTIONALLY_COMPATIBLE"
VERDICT_INSUFFICIENT = "INSUFFICIENT_INFO"

_NO_INFO_TEXT = (
    "Mechanism-direction check found insufficient information to classify "
    "compatibility; this does not confirm the candidate is directionally compatible."
)

# Direction evidence is a bounded gate. If the AI service is unavailable, the
# existing exception path returns INSUFFICIENT_INFO and applies no score cap.
_AI_TIMEOUT_SECONDS = 60.0
_AI_MAX_RETRIES = 0

# Known pharmaceutical safety-screening targets.
# Companies routinely measure IC50/Ki of drug candidates against these proteins
# to detect DRUG-INDUCED LIVER INJURY (DILI) or cardiac liability BEFORE
# regulatory submission — NOT because the drug might treat a disease caused by
# these proteins.  Activity records in ChEMBL for these targets may therefore
# come from SAFETY PROFILING ASSAYS rather than therapeutic-intent binding studies.
# When the candidate target is in this set, the Step 1 search query is augmented
# to explicitly ask the LLM to consider the safety-screen context.
_DILI_SAFETY_SCREEN_TARGETS: frozenset[str] = frozenset({
    "ABCB11", "BSEP",        # Bile salt export pump — BSEP inhibition = cholestasis (DILI)
    "KCNH2", "HERG",         # hERG K⁺ channel — block = QT prolongation (cardiac safety)
    "ABCB1", "MDR1",         # P-glycoprotein / MDR1 — multidrug efflux, DDI screening
    "ABCC2", "MRP2",         # MRP2 — bile acid/drug exporter, DILI screening
    "CYP3A4", "CYP2D6",      # CYP enzymes — DDI/hepatotoxicity liability screening
    "CYP2C9", "CYP2C19", "CYP1A2",
    "SCN5A",                 # NaV1.5 cardiac sodium channel — cardiac safety
})


def _openai_client() -> OpenAI | None:
    base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    api_key  = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
    if not base_url or not api_key:
        return None
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=_AI_TIMEOUT_SECONDS,
        max_retries=_AI_MAX_RETRIES,
    )


def check_mechanism_direction(
    drug_name: str,
    target_symbol: str,
    action_type: str | None,
    mechanism_of_action: str | None,
    disease_name: str,
    candidate_chembl_ids: list[str] | None = None,
    candidate_inchikey: str | None = None,
) -> dict[str, Any]:
    """
    Two-step mechanism-direction compatibility check using OpenAI.

    The drug was identified as a repurposing candidate because it has IC50/Ki
    bioactivity against `target_symbol` in ChEMBL.  This check asks whether
    the drug's pharmacological action ON THAT SPECIFIC TARGET is directionally
    compatible with the disease.

    INCOMPATIBLE verdict requires HIGH CONFIDENCE — unambiguous mechanistic
    incompatibility directly stated by the retrieved text.  Complex or
    multi-protein mechanisms → INSUFFICIENT_INFO (fail-open).

    If the drug is a known approved/investigational treatment for this disease
    through this target pathway, that anchors COMPATIBLE.

    Args:
        drug_name:              drug being evaluated
        target_symbol:          HGNC gene symbol of the target (e.g. "GCH1")
        action_type:            ChEMBL action_type (e.g. "INHIBITOR"); may be None
        mechanism_of_action:    ChEMBL moa string (may be None)
        disease_name:           full disease name used for pathophysiology search

    Returns:
      {
        "verdict"                   : str   — one of the three labels
        "disease_mechanism_summary" : str   — full Step 1 web-search text (audit)
        "reason"                    : str   — one-sentence cited reason from Step 2
        "action_type_used"          : str | None
        "mechanism_of_action_used"  : str | None
        "target_symbol_used"        : str
        "model_used"                : str
        "search_citations"          : str   — URLs/citations from Step 2 classifier
        "step2_raw"                 : str   — raw Step 2 output (full audit trail)
        "compatible"                : bool  — True ONLY for COMPATIBLE verdict
        "incompatible"              : bool  — True ONLY for INCOMPATIBLE; cap trigger
      }
    Cached 30 days on success, 1 day on error/skip.
    """
    heldout_mode = holdout.is_active() and (
        holdout.matches_name(drug_name)
        or any(
            holdout.matches_molecule(molecule_id)
            for molecule_id in (candidate_chembl_ids or [])
            if molecule_id
        )
        or holdout.matches_inchikey(candidate_inchikey)
    )
    cache_key = make_key(
        "mechanism_direction_v5",
        "heldout_candidate" if heldout_mode else drug_name,
        target_symbol, disease_name,
        action_type or "", mechanism_of_action or "",
    )
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "verdict": VERDICT_INSUFFICIENT,
        "disease_mechanism_summary": "",
        "reason": _NO_INFO_TEXT,
        "action_type_used": action_type,
        "mechanism_of_action_used": mechanism_of_action,
        "target_symbol_used": target_symbol,
        "model_used": "gpt-5.4",
        "search_citations": "",
        "step2_raw": "",
        "compatible": False,
        "incompatible": False,
    }

    client = _openai_client()
    if not client:
        result["reason"] = (
            "Mechanism-direction check skipped — "
            "AI_INTEGRATIONS_OPENAI_BASE_URL or AI_INTEGRATIONS_OPENAI_API_KEY "
            "not configured. " + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)
        return result

    try:
        action_desc = action_type or "unknown"
        moa_desc    = mechanism_of_action or "not specified in ChEMBL mechanism records"

        # ── Step 1: web-search — disease mechanism + clinical-use anchor ─────
        # The prompt has three parts:
        #   (A) Disease pathophysiology anchored to target_symbol
        #   (B) Drug's action on target_symbol specifically
        #   (C) Clinical-use anchor — is the drug known to treat this disease?
        # Part (C) matters for drugs like sirolimus/FKBP12 where the therapeutic
        # mechanism is a multi-protein gain-of-function complex, making the naive
        # "inhibitor of FKBP1A" framing misleading.
        # For known DILI/safety-screening targets, add a fourth question that
        # asks the LLM to assess whether the ChEMBL activity record likely comes
        # from a pharmaceutical safety screen rather than a therapeutic-intent assay.
        candidate_label = (
            "the held-out candidate" if heldout_mode else f"the drug {drug_name!r}"
        )
        dili_question = ""
        if target_symbol.upper() in _DILI_SAFETY_SCREEN_TARGETS:
            dili_question = (
                f"\n\n4. ASSAY CONTEXT (REQUIRED for this target): "
                f"{target_symbol!r} is a well-known pharmaceutical DILI/safety "
                f"screening target. Drug companies routinely measure IC50/Ki of "
                f"candidate drugs against {target_symbol!r} to detect liver or "
                f"cardiac toxicity risk BEFORE regulatory submission — NOT to find "
                f"treatments for diseases caused by {target_symbol!r} dysfunction. "
                f"Does the literature indicate whether {candidate_label}'s activity "
                f"against {target_symbol!r} comes from a DILI/safety screening "
                f"context (recording a toxicity liability) or from a genuine "
                f"therapeutic-intent study for {disease_name!r}? "
                f"This distinction is critical: if the activity is safety-screen "
                f"origin, the binding data does NOT support a therapeutic hypothesis "
                f"and the direction check should reflect that this is an INHIBITOR "
                f"of a protein that the disease already lacks — i.e., "
                f"DIRECTIONALLY_INCOMPATIBLE."
            )

        clinical_use_question = "" if heldout_mode else (
            f"\n\n3. CLINICAL USE ANCHOR: Is {drug_name!r} (or a closely related "
            f"compound in the same class) known to be an approved, investigational, "
            f"or experimentally validated treatment for {disease_name!r} or a "
            f"disease caused by dysfunction of {target_symbol!r}? "
            f"If yes, what is the confirmed clinical mechanism through "
            f"{target_symbol!r}? Cite sources."
        )
        search_query = (
            f"Context: {candidate_label} has target-first pharmacology against "
            f"the protein target {target_symbol!r} (gene symbol) in ChEMBL assay "
            f"data and is being evaluated as a repurposing candidate for "
            f"{disease_name!r}.\n\n"
            f"Target-specific pharmacological action: "
            f"action_type={action_desc!r}, "
            f"mechanism_of_action={moa_desc!r}.\n\n"
            f"Please answer all questions with cited sources:\n\n"
            f"1. DISEASE MECHANISM: What is the core pathophysiological defect "
            f"in {disease_name!r}?  Is {target_symbol!r} DEFICIENT (lost/reduced "
            f"function) or OVERACTIVE (gained/elevated function) in this disease? "
            f"Cite the primary molecular mechanism.\n\n"
            f"2. TARGET-SPECIFIC DIRECTION: Given {candidate_label} has "
            f"{action_desc!r} activity specifically against {target_symbol!r}, "
            f"would this drug's direct action ON {target_symbol!r} correct, "
            f"compensate for, or WORSEN the disease defect described in (1)? "
            f"Focus only on the drug's effect on {target_symbol!r} itself — "
            f"not on any indirect downstream consequences through other proteins."
            f"{clinical_use_question}"
            f"{dili_question}"
        )
        # Provider-bound web-search tool: retry 429/5xx with backoff (no
        # cross-provider failover — the tool API is OpenAI-specific).
        search_response = call_with_backoff(
            lambda: client.responses.create(
                model="gpt-5.4",
                tools=[{"type": "web_search_preview"}],
                input=search_query,
            ),
            label="mechanism-direction-search",
        )
        search_text = (search_response.output_text or "").strip()
        result["disease_mechanism_summary"] = search_text

        if not search_text:
            result["verdict"] = VERDICT_INSUFFICIENT
            cache_set(cache_key, result, ttl_days=1)
            return result

        # ── Step 2: constrained classification ────────────────────────────────
        # HIGH-CONFIDENCE threshold: only INCOMPATIBLE when the retrieved text
        # gives direct, unambiguous evidence that the drug's action on
        # target_symbol worsens the disease.  Complex mechanisms, indirect
        # effects, or multi-protein complexes → INSUFFICIENT_INFO (fail-open).
        # Clinical use anchor: approved/investigational status → COMPATIBLE.
        clinical_compatibility_rule = "" if heldout_mode else (
            f"    (a) The retrieved text shows {drug_name!r} or a closely related "
            f"compound IS an approved or experimentally validated treatment for "
            f"{disease_name!r} or a disease caused by {target_symbol!r} dysfunction.\n"
        )
        classification_prompt = (
            f"CONTEXT:\n"
            f"  Candidate: {candidate_label}\n"
            f"  Bioactivity target: {target_symbol!r}\n"
            f"    (drug was identified as a candidate because it has IC50/Ki "
            f"activity against {target_symbol!r} in ChEMBL assays)\n"
            f"  ChEMBL action_type: {action_desc!r}\n"
            f"  ChEMBL mechanism_of_action: {moa_desc!r}\n"
            f"  Disease: {disease_name!r}\n\n"
            f"RETRIEVED EVIDENCE:\n"
            f"---\n{search_text}\n---\n\n"
            f"CLASSIFICATION TASK:\n"
            f"Classify whether {candidate_label}'s action ON {target_symbol!r} is "
            f"directionally compatible with {disease_name!r}.\n\n"
            f"Verdict rules (apply in priority order):\n\n"
            f"  PRIORITY 1 — DIRECTIONALLY_COMPATIBLE:\n"
            f"    Apply if ANY of the following is true:\n"
            f"{clinical_compatibility_rule}"
            f"    (b) The drug's action on {target_symbol!r} clearly corrects or "
            f"compensates for the primary disease defect in the retrieved text.\n\n"
            f"  PRIORITY 2 — DIRECTIONALLY_INCOMPATIBLE:\n"
            f"    Apply ONLY when ALL of the following are true:\n"
            f"    (a) Priority 1 does not apply.\n"
            f"    (b) The retrieved text gives direct, unambiguous evidence that "
            f"the drug's action ON {target_symbol!r} would worsen the core defect "
            f"(e.g. inhibiting an enzyme that is already lost/deficient).\n"
            f"    (c) The mechanism is simple and direct — no multi-protein "
            f"complexes, indirect downstream effects, or pharmacological nuance "
            f"that could alter the simple inhibitor/activator conclusion.\n\n"
            f"  PRIORITY 3 — INSUFFICIENT_INFO:\n"
            f"    Apply when neither Priority 1 nor Priority 2 is clearly met:\n"
            f"    complex mechanism, insufficient evidence, or any ambiguity.\n\n"
            f"CRITICAL CONSTRAINT: never introduce a claim not in the retrieved "
            f"text above.\n\n"
            f"Reply in this EXACT format — three lines, nothing else:\n"
            f"VERDICT: DIRECTIONALLY_INCOMPATIBLE | DIRECTIONALLY_COMPATIBLE | INSUFFICIENT_INFO\n"
            f"REASON: <one sentence citing a specific source from the retrieved text>\n"
            f"CITATIONS: <comma-separated URLs or citation identifiers, or 'none'>"
        )
        # Text-only classification: round-robin providers + 429 failover.
        classify_text, _provider = chat_text(classification_prompt,
                                             max_tokens=512)
        classify_text = classify_text.strip()
        result["step2_raw"] = classify_text

        verdict = VERDICT_INSUFFICIENT
        reason  = _NO_INFO_TEXT
        cite    = ""
        for line in classify_text.splitlines():
            line  = line.strip()
            upper = line.upper()
            if upper.startswith("VERDICT:"):
                raw = line.split(":", 1)[1].strip().upper()
                if "INCOMPATIBLE" in raw:
                    verdict = VERDICT_INCOMPATIBLE
                elif "COMPATIBLE" in raw:
                    verdict = VERDICT_COMPATIBLE
                else:
                    verdict = VERDICT_INSUFFICIENT
            elif upper.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
            elif upper.startswith("CITATIONS:"):
                raw_cite = line.split(":", 1)[1].strip()
                if raw_cite and raw_cite.lower() not in ("none", "n/a", ""):
                    cite = raw_cite

        result["verdict"]          = verdict
        result["reason"]           = reason
        result["search_citations"] = cite
        result["compatible"]       = (verdict == VERDICT_COMPATIBLE)
        result["incompatible"]     = (verdict == VERDICT_INCOMPATIBLE)

        cache_set(cache_key, result, ttl_days=30)

    except Exception as e:
        print(f"[mechanism_direction] WARNING: check failed for "
              f"'{drug_name}'/'{target_symbol}'/'{disease_name}': {e}")
        result["verdict"]    = VERDICT_INSUFFICIENT
        result["reason"]     = (
            f"Mechanism-direction check encountered an error for "
            f"'{drug_name}'/'{target_symbol}'/'{disease_name}': {e}. "
            f"Treating as INSUFFICIENT_INFO (no cap applied). " + _NO_INFO_TEXT
        )
        cache_set(cache_key, result, ttl_days=1)

    return result
