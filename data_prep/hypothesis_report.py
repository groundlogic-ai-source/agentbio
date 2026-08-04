"""
Full write-up generation for doubly-surviving hypotheses (discovery + confirmation).

For a hypothesis that passed BOTH cumulative-FDR discovery AND holdout confirmation,
this module assembles every already-computed number from the registry into a
structured `facts` dict, then asks Claude Opus 4.8 to narrate a full report that is
grounded STRICTLY in those numbers — it introduces no new statistics and makes no
claims the audit trail does not already support.

Nothing here recomputes a statistic. Every number in the report originates from
hypothesis_log.csv / bisociation_history.csv (the same rows the Research tab shows),
so the narrative is auditable line-by-line against the registry.
"""
from __future__ import annotations

import json
import re

import hypothesis_registry as R
import llm_clients as L

# outcome_note format written by run_discovery / api:
#   "OR=0.461 CI[0.339,0.634] n=4203 mech: <one-sentence justification>"
_EFFECT_RE = re.compile(
    r"OR=([-\d.eE+]+)\s+CI\[([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\]\s+n=(\d+)"
)


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        # Guard: nan/inf cannot be serialized to valid JSON. Treat them as
        # missing so the report correctly shows "no result" rather than NaN.
        import math
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _truthy(v) -> bool:
    return str(v).strip().lower() == "true"


def _parse_effect(note: str) -> dict | None:
    m = _EFFECT_RE.search(str(note or ""))
    if not m:
        return None
    return {
        "odds_ratio": float(m.group(1)),
        "ci_low": float(m.group(2)),
        "ci_high": float(m.group(3)),
        "n": int(m.group(4)),
    }


def _parse_mech(note: str) -> str:
    s = str(note or "")
    i = s.find("mech:")
    return s[i + len("mech:"):].strip() if i >= 0 else ""


def _parse_spec(raw):
    """Parse a persisted feature_spec cell (JSON string) into a dict, or None."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, TypeError):
        return None


def render_spec(spec: dict | None) -> str:
    """
    Render a feature_spec into the LITERAL computable proxy in plain English —
    the mechanical test that was actually run, deliberately stated separately
    from the hypothesis's biological/mechanistic story.
    """
    if not spec:
        return ""
    op = spec.get("op")
    params = spec.get("params", {}) or {}
    if op == "established":
        return ("drug is flagged as an established product "
                "(established_product == true)")
    if op == "prc_raw":
        return "prior_repurposing_count, used as a continuous predictor"
    if op == "prc_threshold":
        return f"prior_repurposing_count >= {params.get('k')}"
    if op == "ind_keyword":
        kws = [str(k) for k in params.get("keywords", [])]
        return "indication name (lowercased) contains any of: [" + ", ".join(kws) + "]"
    if op == "drug_keyword":
        kws = [str(k) for k in params.get("keywords", [])]
        return "drug name (lowercased) contains any of: [" + ", ".join(kws) + "]"
    if op == "mw_raw":
        return "molecular weight (pubchem_mw), used as a continuous predictor"
    if op == "xlogp_raw":
        return "XLogP lipophilicity (pubchem_xlogp), used as a continuous predictor"
    if op == "mw_threshold":
        return f"molecular weight (pubchem_mw) >= {params.get('k')} Da"
    if op == "xlogp_threshold":
        return f"XLogP lipophilicity (pubchem_xlogp) >= {params.get('k')} (binary split)"
    if op == "is_small_molecule":
        return "drug is a small molecule (chembl_molecule_type == 'Small molecule')"
    if op == "is_oral":
        return "drug is orally administered (chembl_oral == true)"
    if op == "global_max_phase_raw":
        return "maximum clinical phase reached globally (chembl_max_phase), continuous"
    if op == "global_max_phase_threshold":
        return f"maximum clinical phase reached globally (chembl_max_phase) >= {params.get('k')}"
    if op == "interaction":
        base = render_spec(params.get("base")) or "(unspecified base)"
        mod = render_spec(params.get("moderator")) or "(unspecified moderator)"
        return (
            f"interaction: whether the effect of [{base}] on repurposing success "
            f"DIFFERS across the two groups defined by [{mod}] "
            "(logistic base:moderator term)"
        )
    return f"unrecognized op: {op!r}"


def collect_facts(hypothesis_id: str) -> dict | None:
    """
    Gather every already-computed number for one hypothesis_id from the registry.

    Discovery FDR q-values are recomputed cumulatively at read time (exactly as the
    /api/research/hypotheses endpoint does), because stored per-run q-values go stale
    as new tests are appended. Nothing else is computed — effect sizes, confirmation
    p-values, and the full confound-check detail are read verbatim from the CSVs.

    Returns None if the hypothesis_id is unknown.
    """
    R.migrate_registries()
    hist = R.load_history_full()
    fdr = R.cumulative_fdr()
    qmap = {row["test_id"]: row["fdr_q"] for _, row in fdr.iterrows()}
    # Confirmation status is recomputed at read time for the same reason discovery
    # q-values are: the cumulative confirmation family grows with every attempt.
    cqmap = R.confirmation_qmap()

    rows = hist[hist["hypothesis_id"] == hypothesis_id]
    if rows.empty:
        return None

    framings: list[dict] = []
    confound_check: dict | None = None
    passed_both = False
    feature_spec: dict | None = None

    for _, r in rows.iterrows():
        if feature_spec is None:
            feature_spec = _parse_spec(r.get("feature_spec"))
        tid = r.get("test_id")
        q = qmap.get(tid)
        disc_pass = q is not None and float(q) < R.SIGNIFICANCE_THRESHOLD
        cq = cqmap.get(tid)
        conf_pass = cq is not None and float(cq) < R.CONFIRMATION_ALPHA
        if disc_pass and conf_pass:
            passed_both = True

        # confound_check_summary is investigated once per hypothesis and copied to
        # both framing rows; keep the first completed one we see.
        raw_cs = r.get("confound_check_summary")
        if confound_check is None and isinstance(raw_cs, str) and raw_cs.strip():
            try:
                parsed = json.loads(raw_cs)
                if isinstance(parsed, dict) and parsed.get("status") == "completed":
                    confound_check = parsed
            except (ValueError, TypeError):
                pass

        framings.append({
            "framing": r.get("outcome_framing"),
            "test_type": r.get("discovery_test_type"),
            "discovery_raw_p": _to_float(r.get("discovery_raw_p")),
            "discovery_fdr_q": _to_float(q),
            "discovery_pass": disc_pass,
            "confirmation_raw_p": _to_float(r.get("confirmation_raw_p")),
            "confirmation_fdr_q": _to_float(cq),
            "confirmation_pass": conf_pass,
            "confirmation_pass_at_test_time": _truthy(r.get("confirmation_pass")),
            "effect_size": _parse_effect(r.get("outcome_note")),
        })

    first = rows.iloc[0]
    raw_novelty = first.get("novelty_tag")
    novelty_tag = (
        str(raw_novelty).strip()
        if raw_novelty is not None and str(raw_novelty).strip()
        else None
    )

    # Bisociative provenance: reconstruct what BOTH generators proposed in this
    # run, and the lead reviewer's per-hypothesis decision.  This works because
    # test_ready() always persists a history row even for DISCARDED hypotheses,
    # so every domain ever proposed in the run is present in bisociation_history.
    provenance: dict | None = None
    run_id = str(first.get("run_id", "") or "")
    if run_id:
        try:
            siblings = R.load_run_context(run_id)
            _opus_kws = {"opus", "a", "llm_a_opus", "llm_a", "claude", "claude opus"}
            _sol_kws  = {"sol", "b", "llm_b_sol", "llm_b", "gpt", "chatgpt", "gpt-5.6"}
            opus_domains = sorted({
                str(r["domain_description"])
                for r in siblings
                if r.get("proposing_llm", "").lower().strip() in _opus_kws
                   and r.get("domain_description")
            })
            sol_domains = sorted({
                str(r["domain_description"])
                for r in siblings
                if r.get("proposing_llm", "").lower().strip() in _sol_kws
                   and r.get("domain_description")
            })
            lead_decisions = []
            for r in siblings:
                note = str(r.get("outcome_note", "") or "")
                dom  = str(r.get("domain_description", "") or "")
                htext = str(r.get("resulting_hypothesis_text", "") or "")
                llm  = str(r.get("proposing_llm", "") or "")
                if dom:
                    lead_decisions.append({
                        "domain": dom,
                        "hypothesis": htext[:120],
                        "proposing_llm": llm,
                        "lead_decision": note[:300],
                    })
            provenance = {
                "run_id": run_id,
                "opus_domains": opus_domains,
                "sol_domains": sol_domains,
                "lead_decisions": lead_decisions,
            }
        except Exception:  # noqa: BLE001
            provenance = None

    return {
        "hypothesis_id": hypothesis_id,
        "hypothesis_text": str(first.get("resulting_hypothesis_text", "")),
        "domain": str(first.get("domain_description", "")),
        "proposing_llm": str(first.get("proposing_llm", "")),
        "mechanistic_justification": _parse_mech(first.get("outcome_note")),
        "feature_spec": feature_spec,
        "how_tested": render_spec(feature_spec),
        "novelty_tag": novelty_tag,
        "significance_threshold": R.SIGNIFICANCE_THRESHOLD,
        "correction_method": R.CORRECTION_METHOD,
        "passed_both": passed_both,
        "framings": framings,
        "confound_check": confound_check,
        "provenance": provenance,
    }


_PROMPT_TEMPLATE = """You are a scientific writer producing a rigorous, auditable \
write-up for a drug-repurposing statistical finding that survived BOTH cumulative \
false-discovery-rate (FDR) correction on the discovery split AND an independent \
holdout confirmation test.

CRITICAL GROUNDING RULES — read carefully:
- Use ONLY the numbers in the FACTS JSON below. Do NOT invent, estimate, round \
differently, or introduce any statistic that is not present in FACTS.
- Do NOT claim causation, biological truth, or clinical relevance. This is a \
statistical association in a curated dataset, nothing more.
- If a number is null/missing in FACTS, say so explicitly rather than guessing.
- Every quantitative claim you make must be traceable to a field in FACTS.

Write the report in Markdown with these sections, in this order:

## Hypothesis
State the full hypothesis text verbatim, plus the source domain and which model \
proposed it. One short paragraph. If FACTS.novelty_tag is not null, add one sentence: \
"Prior-literature tag (web-search, informational): [tag]" — where tag is NOVEL, \
ALREADY_ESTABLISHED, or UNCLEAR. A tag of ALREADY_ESTABLISHED does NOT weaken this \
report; the statistical finding stands on its own regardless of prior knowledge.

## Bisociative provenance
Using FACTS.provenance, show what BOTH generators actually proposed in this run. \
List ALL domains proposed by Opus under a "Opus proposed:" bullet list, and ALL \
domains proposed by Sol under a "Sol proposed:" bullet list. Then describe, from \
FACTS.provenance.lead_decisions, the lead reviewer's consolidation: for each \
hypothesis, state whether it was marked READY, NEEDS_ENRICHMENT, or DISCARDED, \
and give its lead_decision rationale in one sentence. If FACTS.provenance is null, \
state plainly that run-level context was not available for this seeded entry.

## How this was actually tested
State the LITERAL computable proxy from FACTS.how_tested — the exact mechanical \
rule applied to the dataset columns (e.g. "indication name contains any of: \
[...]"). This is deliberately separate from the biological/mechanistic story: \
report the mechanical test verbatim, then in ONE sentence note that this proxy — \
not the broader biological claim — is what the statistics below actually measured. \
If FACTS.how_tested is empty/null, state plainly that the literal feature proxy \
was not persisted for this run and therefore cannot be shown (do NOT invent it). \
Do NOT restate the mechanistic justification here.

## Statistical evidence underneath the p-value
For EACH outcome framing present in FACTS, report the regression/effect summary \
that sits underneath the p-value — not just the p-value. Give the odds ratio, its \
95% confidence interval, the sample size n, the test type, the raw discovery \
p-value, and the cumulative FDR q-value. Present this as a compact Markdown table \
with one row per framing. Then, in one sentence per framing, state the effect size \
in plain terms (e.g. what an odds ratio below 1 vs above 1 means for the odds of \
repurposing success, and roughly how large the effect is).

## Discovery vs. confirmation
Put the discovery result and the holdout-confirmation result side by side (a small \
Markdown table with columns: framing, discovery raw p, FDR q, discovery pass, \
confirmation raw p, confirmation pass). State clearly that passing confirmation \
means the effect reproduced on data never used during discovery.

## Confound checks
This is the most important section. The FACTS.confound_check object lists the \
specific alternative explanations that were tested. Name EACH confound checked. \
For each one, report:
- its name and the one-to-two-sentence rationale for why it might explain the \
correlation spuriously,
- whether it was computable from the dataset,
- if computable: the unadjusted odds ratio and p-value for the primary effect, the \
odds ratio and 95% CI and p-value AFTER adjusting for that confound, and whether \
the original effect SURVIVED adjustment (survives_adjustment).
- if it was NOT computable or hit an untestable condition (adjustment_result null \
or containing a "note" about not being testable), say so explicitly and do not \
fabricate adjusted numbers.
Then write a one-paragraph verdict: did the effect survive adjustment for every \
computable confound, or did it fail (not survive) any? If it failed to survive any \
single confound, you MUST state that explicitly and name which confound.

## Bottom line
Two-to-three sentences. What the finding is, what it is not (no causal/clinical \
claim), and the strongest remaining caveat given the confound results.

FACTS:
{facts_json}
"""


def build_prompt(facts: dict) -> str:
    return _PROMPT_TEMPLATE.format(facts_json=json.dumps(facts, indent=2))


def generate_report(facts: dict, max_tokens: int = 4000) -> str:
    """Call Opus 4.8 to narrate the report, grounded strictly in `facts`."""
    return L.opus(build_prompt(facts), max_tokens=max_tokens)
