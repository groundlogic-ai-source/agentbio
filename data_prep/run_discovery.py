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
        status = "passed discovery" if passed is True or str(passed) == "True" else "did not survive"
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
    a = L.extract_json_list(a_raw)
    print(f"[gen]   A proposed {len(a)} domains: {_domain_names(a)}", flush=True)

    print("[gen] LLM B (GPT-5.6 Sol) proposing domains...", flush=True)
    b_raw = L.sol(base)
    b = L.extract_json_list(b_raw)
    print(f"[gen]   B proposed {len(b)} domains: {_domain_names(b)}", flush=True)

    dupes = _overlap(a, b)
    if dupes:
        print(f"[gen] overlap detected {dupes} -> reprompting B with exclusions", flush=True)
        excl = ", ".join(sorted(set(_domain_names(a)) | set(dupes)))
        reprompt = (
            base
            + f"\n\nADDITIONAL EXCLUSION: do NOT propose any of these domains (already "
            f"taken by the other model): {excl}. Propose genuinely different phenomena."
        )
        b_raw = L.sol(reprompt)
        b = L.extract_json_list(b_raw)
        print(f"[gen]   B re-proposed: {_domain_names(b)}", flush=True)
    return a, b


def lead_review(a: list[dict], b: list[dict]) -> list[dict]:
    payload = {"LLM_A_Opus": a, "LLM_B_Sol": b}
    prompt = f"""
You are the LEAD reviewer (Claude Opus 4.8). Below are bisociative hypotheses from two
models (A = Opus, B = Sol). Review ALL of them and produce a single consolidated list.

Rules:
- Keep the one-sentence mechanistic justification for each; if it is not a real causal
  or structural argument (just "sounds plausible"), DISCARD the hypothesis.
- DISCARD anything trivial or redundant with features a cheminformatics repurposing
  pipeline already uses (e.g. Tanimoto structural similarity, Open Targets association
  score).
- DISCARD label-confounded features (those built on prior_repurposing_count), since the
  outcome label is defined using that count.
- LABEL-ENTANGLEMENT STRUCTURAL CHECK (mandatory for EVERY candidate — show your
  reasoning even for hypotheses you keep): reason explicitly about whether the proposed
  feature's defining terms could be definitionally or structurally entangled with how
  the outcome label itself was constructed. The core test is: "If I know this feature
  value, does that already tell me something about whether the label was assigned — not
  by biology, but by the logic of how the label is defined?" DISCARD anything where the
  answer is yes. Record your one-sentence structural reasoning (not just the conclusion)
  in needs_or_discard_reason for EVERY candidate, including ones you keep (write "not
  entangled: [why]" for passing items). The canonical illustration is an indication-stage
  keyword ("refractory", "relapsed", "salvage"): such indications definitionally
  presuppose an existing approved first-line therapy, so the keyword co-varies with the
  administrative label by construction. But this is an ILLUSTRATION, not a complete
  list — new tautological patterns will not resemble old ones. Evaluate each feature's
  structural relationship to label construction independently; never rely on keyword
  pattern-matching alone.
- ACTIVELY PROPOSE INTERACTION-EFFECT HYPOTHESES: do not restrict yourself to
  single-variable main effects. Where two computable features plausibly interact,
  propose a hypothesis that a base feature's effect DIFFERS across the levels of a
  binary moderator (e.g. "the effect of an indication keyword on repurposing success
  is stronger among established products than non-established products"). Encode these
  with the "interaction" op (base + binary moderator) defined in the DSL below. Add at
  least one well-motivated interaction hypothesis if any plausible one exists.
- Re-tag each surviving hypothesis READY (testable now via the feature DSL) or
  NEEDS_ENRICHMENT (name the exact missing data).
- Keep feature_spec exactly as an op/params object for READY items (or null for NE).

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
  "tag": "READY" | "NEEDS_ENRICHMENT" | "DISCARDED",
  "needs_or_discard_reason": "..."
}}

Input:
{json.dumps(payload, indent=2)}
""".strip()
    print("[review] Lead (Opus 4.8) consolidating...", flush=True)
    out = L.extract_json_list(L.opus(prompt, max_tokens=8000))
    tags = {}
    for h in out:
        tags[h.get("tag", "?")] = tags.get(h.get("tag", "?"), 0) + 1
    print(f"[review]   {len(out)} hypotheses: {tags}", flush=True)
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
        if h.get("tag") != "DISCARDED" and h.get("hypothesis_text")
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

        if tag != "READY":
            # record the domain/hypothesis even though it produces no test
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

        hid = new_hid()
        kind = F.predictor_kind(spec)
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
    disc_ = [h for h in reviewed if h.get("tag") == "DISCARDED"]
    print(f"\n-- NEEDS_ENRICHMENT (future-work appendix): {len(ne)} --")
    for h in ne:
        print(f"  * [{h.get('domain','')}] {h.get('hypothesis_text','')[:70]} "
              f"-> needs: {h.get('needs_or_discard_reason','')}")
    print(f"\n-- DISCARDED by lead review: {len(disc_)} --")
    for h in disc_:
        print(f"  * [{h.get('domain','')}] {h.get('hypothesis_text','')[:60]} "
              f"-> {h.get('needs_or_discard_reason','')}")

    total = len(R.load_log())
    print(f"\ncumulative hypothesis_log now holds {total} tests (FDR computed over all).")
    print(f"registry: data_prep/registry/hypothesis_log.csv + bisociation_history.csv")


if __name__ == "__main__":
    main()
