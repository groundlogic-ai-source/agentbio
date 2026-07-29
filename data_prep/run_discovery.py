"""
MEGA PROMPT A — bisociative generation + discovery-phase testing (steps 3-6).

Pipeline
  1. Load the labeled dataset (discovery half only for testing) and the full
     bisociation_history (so the LLMs see real prior outcomes, not just names).
  2. GENERATION
     - LLM A (Claude Opus 4.8): propose 4-5 narrow phenomena NOT already in history
       + 3-4 computable predictor features each (feature DSL), or NEEDS_ENRICHMENT.
     - LLM B (GPT-5.6 Sol): identical instructions, same history.
     - Code-level overlap check on domain names; if A and B overlap, reprompt B ONLY
       with A's picks added to its exclusion list.
  3. LEAD REVIEW (Claude Opus 4.8): require a one-sentence mechanistic justification
     per hypothesis; discard trivial / pipeline-redundant (Tanimoto, OT association)
     or label-confounded ones; tag READY / NEEDS_ENRICHMENT.
  4. TEST each READY hypothesis on the DISCOVERY half, both outcome framings:
     binary -> Fisher's exact; continuous -> logistic. Append to the cumulative log.
  5. FDR: recompute Benjamini-Hochberg over the ENTIRE cumulative log; report raw p,
     cumulative-adjusted q, pass/fail at q<0.05 for every test this run. List
     NEEDS_ENRICHMENT hypotheses as a future-work appendix.
  6. Persist every proposed domain (used or not, passed or not) to bisociation_history.

Outcome coding (fixed): positive = repurposed-success; negatives = genuine-failure
(narrow) or genuine-failure + administrative-exclude (broad). original-approval is
excluded from both (it is a first approval, not a repurposing outcome).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import uuid

import pandas as pd

import features as F
import hypothesis_registry as R
import llm_clients as L
import stats_tests as S
import confound_check as C

HERE = os.path.dirname(os.path.abspath(__file__))
_BASE_CSV = os.path.join(HERE, "output", "labeled_dataset.csv")
_ENRICHED_CSV = os.path.join(HERE, "output", "enriched_dataset.csv")
DATA_CSV = _ENRICHED_CSV if os.path.exists(_ENRICHED_CSV) else _BASE_CSV
FDR_Q = 0.05

_ENRICHED_AVAILABLE = os.path.exists(_ENRICHED_CSV)

DSL_DOC = """
You may only propose features that reduce to ONE of these computable ops over the
real dataset columns. No other data exists — if a good idea needs anything else,
do NOT invent a proxy: set "feature_spec": null and "tag": "NEEDS_ENRICHMENT"
with a "needs" string naming the exact missing data.

=== BASE DATASET OPS (always available) ===
  {"op": "prc_raw"}
      continuous = prior_repurposing_count (how many OTHER approved uses this drug has)
  {"op": "prc_threshold", "params": {"k": <int>}}
      binary = prior_repurposing_count >= k
  {"op": "established"}
      binary = drug is an established product (DrugCentral-confirmed)
  {"op": "ind_keyword", "params": {"keywords": [<str>, ...]}}
      binary = ind_name (lowercased) contains ANY of the keywords
  {"op": "drug_keyword", "params": {"keywords": [<str>, ...]}}
      binary = drug_name (lowercased) contains ANY of the keywords

=== ENRICHED OPS (chemical/biological data from PubChem + ChEMBL) ==={enriched_status}
  {"op": "mw_raw"}
      continuous = molecular weight in Da (from PubChem)
  {"op": "mw_threshold", "params": {"k": <float>}}
      binary = molecular_weight >= k  (e.g. k=500 for Lipinski Rule-of-Five)
  {"op": "xlogp_raw"}
      continuous = XLogP lipophilicity (from PubChem)
  {"op": "xlogp_threshold", "params": {"k": <float>}}
      binary = XLogP >= k  (e.g. k=5 for Lipinski, k=0 to split hydrophilic vs lipophilic)
  {"op": "is_small_molecule"}
      binary = ChEMBL molecule_type == "Small molecule" (vs. Antibody/Protein/Enzyme/etc.)
  {"op": "is_oral"}
      binary = ChEMBL oral flag == True
  {"op": "global_max_phase_raw"}
      continuous = highest global clinical phase (0–4) this drug has reached in ChEMBL
                   across ALL indications (not just this one)
  {"op": "global_max_phase_threshold", "params": {"k": <float>}}
      binary = global_max_phase >= k  (e.g. k=4 = ever-approved, k=3 = Phase 3+)

=== INTERACTION OP ===
  {"op": "interaction", "params": {"base": <any op above>, "moderator": <BINARY op above>}}
      Tests whether the `base` predictor's effect on repurposing success DIFFERS across
      the two levels of the binary `moderator`. Fits
      y ~ base + moderator + base:moderator and reports the interaction term's OR/CI/p.
      The moderator MUST be a binary op; the base may be any op. Use for
      "the effect of X is stronger/weaker among Y" hypotheses.

WARNING: prior_repurposing_count is definitionally tied to the outcome label
(a repurposing success requires prior_repurposing_count >= 1). Features built on
prc_raw / prc_threshold are label-confounded and will likely be discarded by review.
Prefer enriched chemical/biological features (mw, xlogp, molecule type, max phase,
oral flag) or indication-text features.
""".strip().replace(
    "{enriched_status}",
    " [AVAILABLE — enriched_dataset.csv loaded]"
    if _ENRICHED_AVAILABLE
    else " [NOT YET AVAILABLE — run enrich_dataset.py; use NEEDS_ENRICHMENT tag for now]",
)

GEN_INSTRUCTIONS = """
You are proposing BISOCIATIVE hypotheses: narrow scientific / systems phenomena from
OTHER fields that could plausibly share STRUCTURE with drug-repurposing success
dynamics, then reducing each to a testable predictor over an existing dataset.

BOLDNESS REQUIREMENT: The strongest hypothesis is one that is surprising,
counter-intuitive, or high-risk — not a safe, expected finding. Bold, unconventional
hypotheses are EXPLICITLY DESIRED. The deterministic statistical pipeline (Fisher's
exact or logistic regression, Benjamini-Hochberg FDR correction, independent holdout
confirmation, confound adjustment) is solely responsible for determining validity.
Generation should NEVER self-censor a hypothesis simply because it sounds unlikely,
strange, or goes against received wisdom. The only legitimate reasons to skip a
hypothesis: (1) it cannot be reduced to a computable statistic from this dataset,
or (2) it has no real one-sentence mechanistic justification (a genuine causal or
structural argument, not "this sounds plausible").

Task:
1. Propose 4-5 SPECIFIC, NARROW phenomena (not broad category names like "ecology"
   or "economics" — name a concrete mechanism, e.g. "preferential attachment in
   citation networks"). Do NOT repeat any domain already in the history below. Where
   a domain appears in the history with a "did not survive" note, either propose a
   genuinely distinct phenomenon OR a clearly different ANGLE on it, and say so.
2. For each phenomenon, propose 3-4 candidate predictor features for repurposing
   success, each reduced to the feature DSL below (or NEEDS_ENRICHMENT).

%s

PARSIMONY NOTE (technical, not a creativity constraint): every hypothesis tested
adds one count to the cumulative Benjamini-Hochberg FDR denominator. Prefer a
smaller number of genuinely distinct, sharp ideas per domain over many near-duplicate
variations of the same underlying concept — five well-separated hypotheses outperform
fifteen minor reframings of the same claim.

Return ONLY a JSON array. Each element:
{
  "domain": "<narrow phenomenon name>",
  "domain_rationale": "<one sentence: what structure it shares with repurposing>",
  "hypotheses": [
    {
      "hypothesis_text": "<a testable claim: feature X predicts repurposing success>",
      "mechanistic_justification": "<one sentence: the causal/structural argument>",
      "feature_spec": {"op": "...", "params": {...}} | null,
      "tag": "READY" | "NEEDS_ENRICHMENT",
      "needs": "<if NEEDS_ENRICHMENT: exact missing data, else empty>"
    }
  ]
}
No prose outside the JSON.
""".strip() % DSL_DOC


def _history_blurb() -> str:
    hist = R.load_history()
    if hist.empty:
        return "(bisociation_history is EMPTY — this is the first run; no domains explored yet.)"
    lines = []
    for _, r in hist.iterrows():
        note = str(r.get("outcome_note") or "").strip()
        dom = str(r.get("domain_description") or "").strip()
        passed = r.get("discovery_pass")
        if passed is True or str(passed) == "True":
            status = "passed discovery"
        elif note.startswith("SALVAGEABLE"):
            # SALVAGEABLE means real mechanistic justification but needs a different
            # feature_spec — generators may re-propose this domain with a corrected spec.
            status = "salvageable (good rationale, needs different feature_spec)"
        else:
            status = "did not survive"
        lines.append(f"- {dom} [{status}] {note}")
    # de-dup identical lines, keep order
    seen, out = set(), []
    for ln in lines:
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
    return "\n".join(out)


_STOP = {"in", "of", "and", "the", "a", "an", "on", "under", "into", "to", "for", "with", "as"}


def _domain_names(proposals: list[dict]) -> list[str]:
    return [str(p.get("domain", "")).strip().lower() for p in proposals]


def _tokens(name: str) -> set[str]:
    return {w for w in "".join(c if c.isalnum() else " " for c in name.lower()).split() if w not in _STOP}


def _overlap(a: list[dict], b: list[dict]) -> list[str]:
    """
    Near-duplicate detection: a B domain overlaps an A domain if the names are
    identical OR share a Jaccard token similarity >= 0.5 (catches "exaptation in
    evolutionary biology" vs "evolutionary exaptation of duplicated genes").
    """
    a_names = _domain_names(a)
    a_tok = [_tokens(n) for n in a_names]
    hits = []
    for nb in _domain_names(b):
        tb = _tokens(nb)
        for na, ta in zip(a_names, a_tok):
            if nb == na:
                hits.append(nb)
                break
            union = ta | tb
            if union and len(ta & tb) / len(union) >= 0.5:
                hits.append(nb)
                break
    return hits


def generate() -> tuple[list[dict], list[dict]]:
    hist = _history_blurb()
    base = f"{GEN_INSTRUCTIONS}\n\n=== bisociation_history (real prior outcomes) ===\n{hist}\n"

    print("[gen] LLM A (Opus 4.8) proposing domains...", flush=True)
    a_raw = L.opus(base)
    # Log raw text BEFORE extraction so the actual model output is visible in the
    # run log even if JSON extraction succeeds or fails. Critical for diagnosing
    # empty responses (content filter, truncation, refusal, wrong format, etc.).
    print(
        f"[gen] Opus raw response (chars={len(a_raw) if a_raw else 0}, "
        f"first 500 chars):\n{(a_raw or '')[:500]!r}",
        flush=True,
    )
    a = L.extract_json_list(a_raw)
    print(f"[gen]   A proposed {len(a)} domains: {_domain_names(a)}", flush=True)

    print("[gen] LLM B (GPT-5.6 Sol) proposing domains...", flush=True)
    try:
        b_raw = L.sol(base)
        # Log raw text BEFORE extraction — the primary diagnostic tool for
        # zero-output Sol runs. Shows the literal bytes returned by the API,
        # including any refusal text, malformed JSON, or truncation artifact.
        print(
            f"[gen] Sol raw response (chars={len(b_raw) if b_raw else 0}, "
            f"first 3000 chars):\n{(b_raw or '')[:3000]!r}",
            flush=True,
        )
        b = L.extract_json_list(b_raw)
        if not b:
            # Parseable JSON but empty — Sol returned [] or all elements were filtered.
            # This is a genuine zero-domain response, NOT an API error. Log it clearly
            # so it is distinguishable from a crash in the run log.
            print(
                "[gen]   WARNING: Sol returned 0 domains — empty JSON array or all "
                "parsed elements were non-dict. Check whether Sol's response exceeded "
                "context or hit a content filter. Continuing with Opus proposals only.",
                flush=True,
            )
        else:
            print(f"[gen]   B proposed {len(b)} domains: {_domain_names(b)}", flush=True)
    except Exception as _sol_err:
        # API failure (timeout, auth error, non-JSON output, etc.). This is a SILENT
        # ERROR at the API level — not a zero-domain response. Without this catch the
        # ValueError from extract_json_list() propagates through run_batch() and aborts
        # the entire run before any DB writes occur, producing a run_id with zero rows.
        print(
            f"[gen]   ERROR: Sol call failed with {type(_sol_err).__name__}: {_sol_err!r}. "
            "This is a silent API error — NOT a zero-domain deliberate choice. "
            "Continuing with Opus proposals only.",
            flush=True,
        )
        b = []

    dupes = _overlap(a, b)
    if dupes:
        print(f"[gen] overlap detected {dupes} -> reprompting B with exclusions", flush=True)
        excl = ", ".join(sorted(set(_domain_names(a)) | set(dupes)))
        reprompt = (
            base
            + f"\n\nADDITIONAL EXCLUSION: do NOT propose any of these domains (already "
            f"taken by the other model): {excl}. Propose genuinely different phenomena."
        )
        try:
            b_raw = L.sol(reprompt)
            print(
                f"[gen] Sol reprompt raw response (chars={len(b_raw) if b_raw else 0}, "
                f"first 3000 chars):\n{(b_raw or '')[:3000]!r}",
                flush=True,
            )
            b = L.extract_json_list(b_raw)
            print(f"[gen]   B re-proposed: {_domain_names(b)}", flush=True)
        except Exception as _sol_err2:
            print(
                f"[gen]   ERROR on Sol reprompt ({type(_sol_err2).__name__}: {_sol_err2!r}). "
                "Keeping first Sol pass.",
                flush=True,
            )
    return a, b


def lead_review(a: list[dict], b: list[dict]) -> list[dict]:
    payload = {"LLM_A_Opus": a, "LLM_B_Sol": b}
    prompt = f"""
You are the LEAD reviewer (Claude Opus 4.8). Below are bisociative hypotheses from two
models (A = Opus, B = Sol). Review ALL of them and produce a single output list.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANTI-CONSOLIDATION RULE (read this first):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Do NOT merge or consolidate a Sol domain with an Opus domain, even if they sound similar.
Each hypothesis from each LLM is a SEPARATE review item and must appear as a SEPARATE
entry in your output — never fold them together under a merged domain name. The
"proposing_llm" field must accurately reflect which model proposed the hypothesis
(keep the original value — never change it). Both LLMs' perspectives are needed for
diversity; merging destroys the signal about which framing is predictive.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REVIEW RULES (applied per-hypothesis):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. MECHANISTIC JUSTIFICATION: Keep the one-sentence justification if it names a
   plausible structural analogy between the domain phenomenon and drug-repurposing
   dynamics (it does NOT need to prove causality — a structural parallel is sufficient).
   Only DISCARD if the justification is entirely circular ("X predicts success because
   successful drugs do X") or has no real argument at all.

2. REDUNDANCY CHECK: DISCARD anything whose computable feature is identical to an
   already-retained entry (same op + same params). A Sol hypothesis sharing the SAME
   DOMAIN TOPIC as an Opus hypothesis is NOT redundant if its feature_spec is distinct
   — review it independently on its own merits.

3. SALVAGEABLE TAG: When a hypothesis has a real mechanistic justification but its
   proposed feature_spec is entangled, missing, or non-computable — yet a DIFFERENT
   feature from the DSL could validly test the same structural claim — tag it
   SALVAGEABLE and provide a concrete alternative feature_spec in needs_or_discard_reason.
   The bar for SALVAGEABLE vs DISCARDED: if you can write a specific corrected
   feature_spec right now, it's SALVAGEABLE; otherwise DISCARDED.

4. DSL RESCUE for NEEDS_ENRICHMENT: Before tagging NEEDS_ENRICHMENT, actively try to
   reduce the hypothesis to the available DSL ops. Many "network" or "trajectory"
   hypotheses can be approximated: polypharmacology → is_oral + mw_threshold; clinical
   maturity → global_max_phase_threshold; platform molecules → drug_keyword with stems.
   Only tag NEEDS_ENRICHMENT if NO available op can even approximately test the claim.

5. LABEL-ENTANGLEMENT STRUCTURAL CHECK (mandatory for EVERY candidate): Reason
   explicitly about whether the proposed feature's defining terms could be DEFINITIONALLY
   entangled with how the outcome label was constructed — not merely correlated, but
   structurally co-determined. The core test: "If I know this feature value, does that
   already logically determine whether the label was assigned — not by biology, but by
   the algorithm that built the label?" DISCARD only where the answer is clearly yes.
   Record one sentence of structural reasoning (not just the conclusion) in
   needs_or_discard_reason for EVERY candidate, including ones you keep ("not entangled:
   [why]" for passing items). The canonical illustration: indication-stage keywords
   ("refractory", "relapsed", "salvage") presuppose an existing approved first-line
   therapy, so they co-vary with the administrative-exclude label by construction.
   But this is an ILLUSTRATION — evaluate each feature independently; do not pattern-
   match on surface similarity to the illustration.

6. HARD EXCLUSIONS: DISCARD (a) features built on prior_repurposing_count (that count
   defines the outcome label); (b) Tanimoto structural similarity or Open Targets
   association score (already in the pipeline baseline).

7. INTERACTION HYPOTHESES: Actively propose at least one interaction-effect hypothesis
   if a plausible moderator exists — "the effect of X on repurposing success differs
   across levels of binary Y". Encode with the "interaction" op in the DSL.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GOAL: Maximize READY hypotheses across BOTH LLMs. Prefer SALVAGEABLE over DISCARDED
whenever a concrete DSL fix exists. The downstream statistical pipeline (Fisher's exact,
logistic regression, BH-FDR) handles false positives — the reviewer's role is NOT to
pre-filter speculative ideas, but to ensure every surviving hypothesis is testable and
non-tautological.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Feature DSL (only these ops are computable):
{DSL_DOC}

Return ONLY a JSON array; each element:
{{
  "domain": "...",
  "proposing_llm": "Opus" | "Sol",
  "hypothesis_text": "...",
  "mechanistic_justification": "...",
  "feature_spec": {{"op": "...", "params": {{...}}}} | null,
  "predictor_kind": "binary" | "continuous" | null,
  "tag": "READY" | "NEEDS_ENRICHMENT" | "SALVAGEABLE" | "DISCARDED",
  "needs_or_discard_reason": "..."
}}

Input:
{json.dumps(payload, indent=2)}
""".strip()
    print("[review] Lead (Opus 4.8) consolidating...", flush=True)
    out = L.extract_json_list(L.opus(prompt, max_tokens=8000))
    tags: dict[str, int] = {}
    for h in out:
        tags[h.get("tag", "?")] = tags.get(h.get("tag", "?"), 0) + 1
    print(f"[review]   {len(out)} hypotheses: {tags}", flush=True)
    # Log any Sol hypotheses present so we can track diversity
    sol_count = sum(1 for h in out if h.get("proposing_llm") == "Sol")
    opus_count = sum(1 for h in out if h.get("proposing_llm") == "Opus")
    print(f"[review]   LLM attribution: Opus={opus_count} Sol={sol_count}", flush=True)
    if sol_count == 0 and opus_count > 0:
        print(
            "[review]   WARNING: zero Sol hypotheses in output — check whether lead "
            "reviewer merged Sol entries into Opus domains (anti-consolidation rule "
            "violation) or Sol produced no proposals.",
            flush=True,
        )
    return out


def tag_novelty(reviewed: list[dict]) -> list[dict]:
    """
    ONE Opus 4.8 + web-search call per candidate hypothesis that survived lead
    review (READY and NEEDS_ENRICHMENT; DISCARDED are skipped as they will not
    be tested or reported).

    Tags each hypothesis as:
      NOVEL              — this specific relationship is not established in the
                           drug-repurposing / pharmacology literature
      ALREADY_ESTABLISHED— documented or considered standard knowledge
      UNCLEAR            — evidence is mixed, literature is sparse, or web search
                           returned no useful signal

    This tag is METADATA ONLY. It is recorded in bisociation_history and shown
    in the final report, but it NEVER blocks, deprioritizes, or gates any
    hypothesis in testing, confirmation, or confound-checking. An
    ALREADY_ESTABLISHED hypothesis is still tested and reported as normal.
    """
    to_tag = [
        h for h in reviewed
        if h.get("tag") not in ("DISCARDED",) and h.get("hypothesis_text")
    ]
    if not to_tag:
        return reviewed
    print(
        f"[novelty] Tagging {len(to_tag)} hypotheses via web-search (informational only)...",
        flush=True,
    )
    for h in to_tag:
        htext = h.get("hypothesis_text", "")
        print(f"[novelty]   {htext[:90]}", flush=True)
        prompt = (
            "Search whether the following relationship is already established or "
            "textbook knowledge in pharmacology or drug-repurposing literature.\n\n"
            f"HYPOTHESIS: {htext}\n\n"
            "Cite specific sources (papers, reviews, databases) if you find them. "
            "Then classify as exactly one of:\n"
            "  NOVEL — this specific quantitative or structural relationship is NOT "
            "established (even if related concepts exist)\n"
            "  ALREADY_ESTABLISHED — documented or considered textbook in pharmacology "
            "or drug repurposing\n"
            "  UNCLEAR — evidence is mixed, literature is sparse, or novelty cannot "
            "be determined from available sources\n\n"
            'Return ONLY a JSON object:\n'
            '{"tag": "NOVEL" | "ALREADY_ESTABLISHED" | "UNCLEAR",\n'
            ' "reasoning": "<one sentence>",\n'
            ' "sources": ["<citation 1>", ...]}\n'
            "No prose outside the JSON."
        )
        try:
            raw = L.opus_with_search(prompt, max_tokens=2000)
            result = L.extract_json(raw)
            tag = result.get("tag") if isinstance(result, dict) else None
            if tag not in ("NOVEL", "ALREADY_ESTABLISHED", "UNCLEAR"):
                tag = "UNCLEAR"
            h["novelty_tag"] = tag
        except Exception as exc:  # noqa: BLE001
            h["novelty_tag"] = "UNCLEAR"
            print(f"[novelty]   parse error: {exc}", flush=True)
        print(f"[novelty]   → {h.get('novelty_tag', 'UNCLEAR')}", flush=True)
    return reviewed


def _framed(df: pd.DataFrame, framing: str) -> pd.DataFrame:
    pos = df["label"] == "repurposed-success"
    if framing == "narrow":
        neg = df["label"] == "genuine-failure"
    else:
        neg = df["label"].isin(["genuine-failure", "administrative-exclude"])
    sub = df[pos | neg].copy()
    sub["y"] = (sub["label"] == "repurposed-success").astype(int)
    return sub


def _run_single_test(sub: pd.DataFrame, spec: dict, kind: str):
    """
    Compute the feature(s) and run the pre-registered test for a spec on one framed
    subset. Returns (TestResult | None, why). `None` means the test was not run
    because of degenerate separation; `why` explains it.

    Handles all three predictor kinds: binary (Fisher), continuous (logistic), and
    interaction (logistic with a base:moderator term, reporting the interaction OR).
    """
    if kind == "interaction":
        base, mod = F.compute_interaction(sub, spec)
        chk = pd.DataFrame({"b": base, "m": mod, "y": sub["y"]}).dropna()
        if chk["y"].nunique() < 2:
            return None, "outcome has <2 classes in this subset"
        if chk["m"].nunique() < 2:
            return None, "moderator is constant in this subset"
        if chk["b"].nunique() < 2:
            return None, "base is constant in this subset"
        for _, g in chk.groupby("m"):
            if g["y"].nunique() < 2:
                return None, "perfect separation within a moderator level"
            if g["b"].nunique() < 2:
                return None, "base is constant within a moderator level"
        res = S.logistic_interaction(base, mod, sub["y"])
        # A singular / non-converged interaction fit yields NaN statistics that
        # would poison the cumulative FDR pass — treat it as not tested.
        if not all(math.isfinite(v) for v in
                   (res.odds_ratio, res.ci_low, res.ci_high, res.p_value)):
            return None, "interaction fit did not converge (non-finite statistics)"
        return res, ""
    feat = F.compute(sub, spec)
    ok, why = F.separation_ok(feat, sub["y"])
    if not ok:
        return None, why
    if kind == "binary":
        return S.fisher_binary(feat, sub["y"]), ""
    return S.logistic_continuous(feat, sub["y"]), ""


def test_ready(disc: pd.DataFrame, reviewed: list[dict], run_id: str, ts: str):
    """Run every READY hypothesis on the discovery half, both framings.

    Returns (log_rows, hist_rows, test_meta).
    test_meta maps hypothesis_id → {spec, kind, mech} so confirm_surviving()
    and confound_check_surviving() can replay the same test on the holdout half.
    """
    log_rows, hist_rows = [], []
    test_meta: dict = {}
    # IDs are prefixed with the run_id (a uuid4-derived unique string), so they are
    # globally collision-free WITHOUT reading the shared CSV — two concurrent runs
    # can never allocate the same id. Counters are local to this run.
    _hc = [0]
    _tc = [0]
    # Load all previously-tested feature_specs once for deduplication. A second dict
    # tracks specs that pass the dedup check within THIS batch so two generators
    # proposing the same computable proxy in the same run don't both get tested.
    _existing_specs = R.load_existing_feature_specs()
    _this_run_specs: dict[str, str] = {}  # canonical_json → hypothesis_id

    def new_hid() -> str:
        _hc[0] += 1
        return f"{run_id}-H{_hc[0]:02d}"

    def new_tid() -> str:
        _tc[0] += 1
        return f"{run_id}-T{_tc[0]:04d}"

    for h in reviewed:
        tag = h.get("tag")
        domain = h.get("domain", "")
        llm = h.get("proposing_llm", "")
        htext = h.get("hypothesis_text", "")
        mech = h.get("mechanistic_justification", "")
        spec = h.get("feature_spec")

        if tag not in ("READY",):
            # record the domain/hypothesis even though it produces no test.
            # SALVAGEABLE entries carry a suggested corrected feature_spec in
            # needs_or_discard_reason — they are recorded as SALVAGEABLE in
            # bisociation_history so future runs can look them up and promote them.
            hid = new_hid()
            note = h.get("needs_or_discard_reason", "")
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "", "outcome_note": f"{tag}: {note}",
            })
            continue

        if not F.is_supported(spec or {}):
            hid = new_hid()
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": "auto-demoted to NEEDS_ENRICHMENT: feature not computable from schema",
            })
            continue

        if not mech.strip():
            # HARD GUARDRAIL: every READY hypothesis must supply a mechanistic
            # justification. The review prompt requires it but LLM output is
            # imperfect — enforce in code so empty-mech hypotheses never enter
            # the cumulative FDR log regardless of the reviewer tag.
            hid = new_hid()
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": "hard-blocked: no mechanistic justification supplied",
            })
            continue

        if F.is_confounded(spec):
            # HARD GUARDRAIL: a feature built on prior_repurposing_count can never be
            # tested, because the outcome label is defined using that count. This is
            # enforced in code, not left to LLM review — even if the lead tags it READY
            # it is discarded here and never enters the cumulative FDR log.
            hid = new_hid()
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": "hard-blocked: label-confounded (prior_repurposing_count defines the outcome label)",
            })
            continue

        if F.is_indication_stage_proxy(spec):
            # HARD GUARDRAIL: ind_keyword features using disease-stage terminology
            # (refractory, resistant, relapsed, salvage) are near-tautological proxies
            # for administrative-exclude label assignment. Refractory/resistant
            # indications presuppose an existing approved first-line product by
            # definition, so the keyword co-varies with the label by construction, not
            # by biology. Cannot produce a valid test.
            hid = new_hid()
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": "hard-blocked: label-confounded (indication-stage keyword proxies administrative-exclude label assignment)",
            })
            continue

        # ── Feature-level deduplication ────────────────────────────────────────
        # Compare the normalized computable proxy (op + params, keys sorted, keywords
        # alphabetized) against every prior test in the persistent registry AND every
        # other hypothesis already claimed in this batch. Identical computable tests
        # re-appearing under a different domain name or narrative framing are NOT new
        # findings — they inflate the FDR denominator and present stale results as if
        # fresh. Skip and reference the original result instead of re-testing.
        canonical = R.spec_canonical(spec)
        _prior = _existing_specs.get(canonical) or (
            {"hypothesis_id": _this_run_specs[canonical], "run_id": run_id, "test_id": ""}
            if canonical in _this_run_specs else None
        )
        if _prior:
            ref_hid = _prior.get("hypothesis_id", "unknown")
            hid = new_hid()
            hist_rows.append({
                "test_id": "", "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": "", "outcome_framing": "",
                "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": (
                    f"SKIPPED (duplicate): identical feature_spec already tested as "
                    f"{ref_hid} — see that hypothesis for the statistical result"
                ),
                "feature_spec": json.dumps(spec),
            })
            print(
                f"[dedup] '{htext[:70]}' → identical feature_spec already in registry "
                f"as {ref_hid}; skipping re-test.",
                flush=True,
            )
            continue

        hid = new_hid()
        kind = F.predictor_kind(spec)
        _this_run_specs[canonical] = hid  # register for within-batch dedup
        # Store spec/kind/mech so the confirmation and confound steps can replay
        # the exact same test on the holdout half after FDR correction.
        test_meta[hid] = {"spec": spec, "kind": kind, "mech": mech}

        for framing in ("narrow", "broad"):
            sub = _framed(disc, framing)
            # ── Feature 4: lock methodology BEFORE the result is computed ──────
            locked_at = dt.datetime.now(dt.timezone.utc).isoformat()
            res, why = _run_single_test(sub, spec, kind)
            tid = new_tid()
            if res is None:
                hist_rows.append({
                    "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                    "session_timestamp": ts, "domain_description": domain,
                    "proposing_llm": llm, "resulting_hypothesis_text": htext,
                    "discovery_test_type": kind, "outcome_framing": framing,
                    "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                    "confirmation_pass": "", "confirmation_raw_p": "",
                    "confound_check_summary": "", "outcome_note": f"not tested: {why}",
                    "feature_spec": json.dumps(spec),
                })
                continue

            log_rows.append({
                "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                "run_timestamp": ts, "hypothesis_text": htext,
                "test_type": res.test_type, "outcome_framing": framing,
                "raw_p": res.p_value,
                # methodology locked before result:
                "significance_threshold": R.SIGNIFICANCE_THRESHOLD,
                "correction_method": R.CORRECTION_METHOD,
                "locked_at": locked_at,
            })
            hist_rows.append({
                "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": domain,
                "proposing_llm": llm, "resulting_hypothesis_text": htext,
                "discovery_test_type": res.test_type, "outcome_framing": framing,
                "discovery_raw_p": res.p_value, "discovery_fdr_p": "",
                "discovery_pass": "", "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": (
                    f"OR={res.odds_ratio:.3g} CI[{res.ci_low:.3g},{res.ci_high:.3g}] "
                    f"n={res.n} mech: {mech}"
                ),
                "feature_spec": json.dumps(spec),
            })
    return log_rows, hist_rows, test_meta


def confirm_surviving(hist_rows: list[dict], conf: pd.DataFrame, test_meta: dict) -> None:
    """
    For each hist_row where discovery_pass is True, replay the same test on the
    holdout confirmation half and fill confirmation_pass / confirmation_raw_p in-place.

    Uses a simple uncorrected p < 0.05 threshold (one pre-specified follow-up test
    per confirmed hypothesis; no additional multiple-comparison correction needed).
    """
    for hr in hist_rows:
        disc_pass = hr.get("discovery_pass")
        if disc_pass is not True and str(disc_pass).lower() != "true":
            continue
        hid = str(hr.get("hypothesis_id", ""))
        meta = test_meta.get(hid)
        if not meta:
            continue
        framing = str(hr.get("outcome_framing", "narrow"))
        spec, kind = meta["spec"], meta["kind"]
        sub = _framed(conf, framing)
        try:
            res, _ = _run_single_test(sub, spec, kind)
            if res is None:
                hr["confirmation_pass"] = False
                hr["confirmation_raw_p"] = ""
                continue
            hr["confirmation_raw_p"] = res.p_value
            hr["confirmation_pass"] = bool(res.p_value < 0.05)
            verdict = "PASS" if res.p_value < 0.05 else "fail"
            print(f"[confirm] {hr.get('test_id','')} {framing}: p={res.p_value:.4g} {verdict}", flush=True)
        except Exception as e:  # noqa: BLE001
            hr["confirmation_pass"] = False
            hr["confirmation_raw_p"] = ""
            print(f"[confirm] ERROR on {hr.get('test_id', '')}: {e}", flush=True)


def confound_check_surviving(
    hist_rows: list[dict], disc: pd.DataFrame, test_meta: dict
) -> None:
    """
    For hypotheses that passed BOTH discovery FDR and holdout confirmation,
    propose 1-3 confounders via Opus (separate call from the lead review),
    then run multivariate logistic adjustment for each computable confound.
    Results stored as JSON in confound_check_summary in-place.

    Each hypothesis_id is investigated only once; both framings' rows receive
    the same summary string.
    """
    results_by_hid: dict[str, str] = {}

    for hr in hist_rows:
        confirmed = hr.get("confirmation_pass")
        if confirmed is not True and str(confirmed).lower() != "true":
            continue
        hid = str(hr.get("hypothesis_id", ""))
        # reuse result if already computed (narrow + broad share one investigation)
        if hid in results_by_hid:
            hr["confound_check_summary"] = results_by_hid[hid]
            continue
        meta = test_meta.get(hid)
        if not meta:
            continue
        framing = str(hr.get("outcome_framing", "narrow"))
        print(f"[confound] investigating {hid} ({framing})...", flush=True)
        try:
            result = C.investigate(
                data_discovery=disc,
                hypothesis_text=str(hr.get("resulting_hypothesis_text", "")),
                feature_spec=meta["spec"],
                predictor_kind=meta["kind"],
                mechanistic_justification=meta["mech"],
                discovery_p=float(hr.get("discovery_raw_p") or 1.0),
                fdr_q=float(hr.get("discovery_fdr_p") or 1.0),
                confirmation_p=float(hr.get("confirmation_raw_p") or 1.0),
                framing=framing,
            )
            summary = json.dumps(result)
        except Exception as e:  # noqa: BLE001
            summary = json.dumps({"status": "error", "error": str(e)})
        results_by_hid[hid] = summary
        hr["confound_check_summary"] = summary


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            st.write(s)
            st.flush()

    def flush(self):
        for st in self.streams:
            st.flush()


def _fill_missing_domains(a: list[dict], b: list[dict], reviewed: list[dict]) -> list[dict]:
    """
    After lead_review() returns, detect any domain proposed by A or B that was
    silently omitted from the reviewer's consolidated output (not even tagged
    DISCARDED).  The reviewer is instructed to tag everything DISCARDED rather
    than drop it, but LLM output is imperfect — this guard ensures every proposed
    domain always gets at least one history row so future batches can exclude it.

    For each completely-dropped domain, one synthetic DISCARDED entry is inserted
    per proposed hypothesis.  If the domain had no hypotheses sub-list (shouldn't
    happen) a single placeholder entry is created for the domain.
    """
    reviewed_domains = {str(h.get("domain", "")).strip().lower() for h in reviewed}
    extra: list[dict] = []
    for llm_label, proposals in (("Opus", a), ("Sol", b)):
        for prop in proposals:
            dom = str(prop.get("domain", "")).strip()
            if not dom or dom.lower() in reviewed_domains:
                continue
            hypotheses = prop.get("hypotheses") or []
            if hypotheses:
                for hyp in hypotheses:
                    extra.append({
                        "domain": dom,
                        "proposing_llm": llm_label,
                        "hypothesis_text": hyp.get("hypothesis_text", ""),
                        "mechanistic_justification": hyp.get("mechanistic_justification", ""),
                        "feature_spec": hyp.get("feature_spec"),
                        "predictor_kind": None,
                        "tag": "DISCARDED",
                        "needs_or_discard_reason": (
                            "domain silently dropped by lead reviewer "
                            "(not included in consolidated output)"
                        ),
                    })
            else:
                extra.append({
                    "domain": dom,
                    "proposing_llm": llm_label,
                    "hypothesis_text": f"[{dom}] (no hypotheses listed)",
                    "mechanistic_justification": "",
                    "feature_spec": None,
                    "predictor_kind": None,
                    "tag": "DISCARDED",
                    "needs_or_discard_reason": (
                        "domain silently dropped by lead reviewer "
                        "(not included in consolidated output)"
                    ),
                })
    return extra


def run_batch(run_id: str | None = None) -> dict:
    """
    Run ONE full autonomous discovery batch (generation → lead review → discovery
    testing → cumulative FDR → confirmation → confound check → persist) and return
    a machine-readable summary. Does NOT touch sys.stdout, so it is safe to call
    from a long-running server process (e.g. the FastAPI background thread that
    backs the "Run new discovery batch" button). main() wraps this with a stdout
    tee for CLI use.
    """
    R.migrate_registries()  # idempotent: back-fills new methodology columns in existing CSVs

    df = pd.read_csv(DATA_CSV)
    disc = df[df["split"] == "discovery"].copy()
    conf = df[df["split"] == "confirmation"].copy()
    run_id = run_id or ("run-" + uuid.uuid4().hex[:8])
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    print(f"=== discovery run {run_id} @ {ts} ===", flush=True)
    print(f"discovery rows: {len(disc)} | confirmation rows: {len(conf)} (of {len(df)} total)",
          flush=True)

    a, b = generate()
    reviewed = lead_review(a, b)
    reviewed = tag_novelty(reviewed)

    # Guard: ensure every domain proposed by A or B gets a history row even if
    # the lead reviewer silently omitted the entire domain from its output.
    # Such domains must appear in the exclusion prompt so future batches don't
    # re-propose and re-discard the same ground.
    _dropped = _fill_missing_domains(a, b, reviewed)
    if _dropped:
        _dropped_domains = sorted({h["domain"] for h in _dropped})
        print(
            f"[run_batch] {len(_dropped)} hypothesis(es) across "
            f"{len(_dropped_domains)} domain(s) were silently dropped by the lead "
            f"reviewer — adding DISCARDED rows so they appear in future exclusion "
            f"prompts: {_dropped_domains}",
            flush=True,
        )
        reviewed = reviewed + _dropped

    log_rows, hist_rows, test_meta = test_ready(disc, reviewed, run_id, ts)

    # carry novelty_tag from reviewed into history rows (matched by hypothesis text)
    _novelty_map = {
        h.get("hypothesis_text", ""): h.get("novelty_tag", "")
        for h in reviewed
        if h.get("novelty_tag")
    }
    if _novelty_map:
        for hr in hist_rows:
            nt = _novelty_map.get(hr.get("resulting_hypothesis_text", ""))
            if nt:
                hr["novelty_tag"] = nt

    # append this run's tests to the cumulative log FIRST, then FDR over everything
    if log_rows:
        R.append_log_rows(log_rows)
    fdr = R.cumulative_fdr()
    qmap = {row["test_id"]: row["fdr_q"] for _, row in fdr.iterrows()}

    # fill FDR + pass back into this run's history rows
    for hr in hist_rows:
        tid = hr.get("test_id")
        if tid and tid in qmap and hr.get("discovery_raw_p") != "":
            q = qmap[tid]
            hr["discovery_fdr_p"] = q
            hr["discovery_pass"] = bool(q < FDR_Q)

    # confirmation step: run surviving hypotheses on the holdout half
    surviving_count = sum(1 for hr in hist_rows if hr.get("discovery_pass") is True)
    double_pass = 0
    if surviving_count:
        print(f"\n[confirm] {surviving_count} test(s) passed FDR — running on confirmation half...",
              flush=True)
        confirm_surviving(hist_rows, conf, test_meta)
        double_pass = sum(1 for hr in hist_rows if hr.get("confirmation_pass") is True)
        print(f"[confirm] {double_pass} test(s) also passed confirmation.", flush=True)

        if double_pass:
            print(f"[confound] {double_pass} doubly-confirmed — investigating confounders...",
                  flush=True)
            confound_check_surviving(hist_rows, disc, test_meta)

    # single history append: all fill-back (FDR, confirmation, confound) is done in-memory first
    R.append_history_rows(hist_rows)

    _report(run_id, reviewed, log_rows, qmap)

    return {
        "run_id": run_id,
        "domains": sorted({str(h.get("domain", "")).strip()
                           for h in reviewed if str(h.get("domain", "")).strip()}),
        "hypotheses_reviewed": len(reviewed),
        "tests_run": len(log_rows),
        "hypotheses_recorded": len({hr.get("hypothesis_id") for hr in hist_rows}),
        "surviving_discovery": surviving_count,
        "confirmed": double_pass,
    }


def run_continuous_batch(
    stop_flag: dict,
    max_domains: int = 20,
    max_hypotheses: int = 50,
    progress_callback=None,
) -> dict:
    """
    Chain autonomous discovery batches until EITHER:
      (a) at least one hypothesis achieves BOTH discovery_pass AND
          confirmation_pass (a double-pass hit), OR
      (b) the cumulative domain count across all batches >= max_domains, OR
      (c) the cumulative hypothesis count >= max_hypotheses, OR
      (d) stop_flag["stop"] is set True by the caller (manual stop).

    Every single test in every batch is logged to the cumulative FDR registry
    exactly as a single-batch run — no change to logging discipline.

    After each batch, calls progress_callback(progress_dict) if provided so
    the caller can persist live status (e.g. to the research_jobs table).

    Returns a summary dict suitable for storing in the research_job result_json.
    """
    total_domains: set[str] = set()
    total_hypotheses = 0
    total_tests = 0
    total_confirmed = 0
    batch_num = 0
    run_ids: list[str] = []

    while not stop_flag.get("stop"):
        batch_num += 1
        run_id = "run-" + uuid.uuid4().hex[:8]
        run_ids.append(run_id)
        print(
            f"\n=== CONTINUOUS batch {batch_num} ({run_id}) — "
            f"{len(total_domains)} domain(s), {total_hypotheses} hypothesis(es) so far ===",
            flush=True,
        )

        try:
            summary = run_batch(run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[continuous] batch {batch_num} failed: {exc}", flush=True)
            break

        total_domains.update(summary.get("domains") or [])
        total_hypotheses += summary.get("hypotheses_reviewed", 0)
        total_tests += summary.get("tests_run", 0)
        total_confirmed += summary.get("confirmed", 0)

        progress = {
            "batch_num": batch_num,
            "domains_explored": len(total_domains),
            "hypotheses_reviewed": total_hypotheses,
            "tests_run": total_tests,
            "confirmed": total_confirmed,
            "mode": "continuous",
            "run_ids": run_ids,
        }
        if progress_callback is not None:
            try:
                progress_callback(progress)
            except Exception:  # noqa: BLE001
                pass

        if total_confirmed > 0:
            print(
                f"[continuous] double-pass found after {batch_num} batch(es), "
                f"{len(total_domains)} domain(s), {total_hypotheses} hypothesis(es). Stopping.",
                flush=True,
            )
            break

        if len(total_domains) >= max_domains:
            print(f"[continuous] domain cap ({max_domains}) reached. Stopping.", flush=True)
            break

        if total_hypotheses >= max_hypotheses:
            print(f"[continuous] hypothesis cap ({max_hypotheses}) reached. Stopping.", flush=True)
            break

    return {
        "mode": "continuous",
        "batches_run": batch_num,
        "domains_explored": len(total_domains),
        "hypotheses_reviewed": total_hypotheses,
        "tests_run": total_tests,
        "confirmed": total_confirmed,
        "stopped_by_user": bool(stop_flag.get("stop")),
        "run_ids": run_ids,
    }


def main() -> None:
    import sys
    os.makedirs(os.path.join(HERE, "output"), exist_ok=True)
    _fh = open(os.path.join(HERE, "output", "discovery_report.txt"), "w")
    sys.stdout = _Tee(sys.__stdout__, _fh)
    run_batch()


def _report(run_id: str, reviewed: list[dict], log_rows: list[dict], qmap: dict) -> None:
    line = "=" * 74
    print(f"\n{line}\nDISCOVERY RESULTS — {run_id}\n{line}")
    print("temperature note: requested 0.7 could not be applied — claude-opus-4-8 "
          "rejects temperature and gpt-5.6-sol forces 1; model defaults used.")

    tested = [r for r in log_rows]
    print(f"\nREADY hypotheses tested this run: {len({r['hypothesis_id'] for r in tested})} "
          f"({len(tested)} tests across framings)")
    # build novelty lookup from reviewed list (hypothesis_text → tag)
    _novelty_lookup = {
        h.get("hypothesis_text", ""): h.get("novelty_tag", "")
        for h in reviewed if h.get("novelty_tag")
    }
    print(f"\n{'test_id':<9} {'framing':<7} {'test':<9} {'raw_p':>10} {'cum_q':>10}  pass  {'novelty':<21}  hypothesis")
    for r in sorted(tested, key=lambda x: float(x["raw_p"])):
        q = qmap.get(r["test_id"], float("nan"))
        passed = "YES" if q < FDR_Q else "no"
        nov = _novelty_lookup.get(r["hypothesis_text"], "")
        print(f"{r['test_id']:<9} {r['outcome_framing']:<7} {r['test_type']:<9} "
              f"{float(r['raw_p']):>10.4g} {q:>10.4g}  {passed:<4}  {nov or '—':<21}  {r['hypothesis_text'][:60]}")

    ne = [h for h in reviewed if h.get("tag") == "NEEDS_ENRICHMENT"]
    salvageable = [h for h in reviewed if h.get("tag") == "SALVAGEABLE"]
    disc_ = [h for h in reviewed if h.get("tag") == "DISCARDED"]
    print(f"\n-- NEEDS_ENRICHMENT (future-work appendix): {len(ne)} --")
    for h in ne:
        print(f"  * [{h.get('proposing_llm','?')}] [{h.get('domain','')}] "
              f"{h.get('hypothesis_text','')[:70]} "
              f"-> needs: {h.get('needs_or_discard_reason','')}")
    if salvageable:
        print(f"\n-- SALVAGEABLE (fixable feature_spec — promote in next run): {len(salvageable)} --")
        for h in salvageable:
            print(f"  * [{h.get('proposing_llm','?')}] [{h.get('domain','')}] "
                  f"{h.get('hypothesis_text','')[:60]} "
                  f"-> fix: {h.get('needs_or_discard_reason','')}")
    print(f"\n-- DISCARDED by lead review: {len(disc_)} --")
    for h in disc_:
        print(f"  * [{h.get('proposing_llm','?')}] [{h.get('domain','')}] "
              f"{h.get('hypothesis_text','')[:60]} "
              f"-> {h.get('needs_or_discard_reason','')}")

    total = len(R.load_log())
    print(f"\ncumulative hypothesis_log now holds {total} tests (FDR computed over all).")
    print(f"registry: data_prep/registry/hypothesis_log.csv + bisociation_history.csv")


if __name__ == "__main__":
    main()
