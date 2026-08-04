"""
Post-confirmation confound investigation (Feature 1).

For each hypothesis that survives both discovery FDR correction AND holdout
confirmation, Claude Opus 4.8 proposes 1-3 specific named confounders that
could explain the correlation without the original hypothesis being real.
For each proposed confound that is computable from the dataset DSL, a
multivariate logistic regression is run with the confound as an additional
covariate, and we report whether the original effect survives adjustment.

Results are always framed as "candidate explanations checked," never as
"the confirmed mechanism."  The output is a JSON-serialisable dict stored
in bisociation_history.confound_check_summary.
"""
from __future__ import annotations

import json
import traceback
from typing import Any

import pandas as pd

import features as F
import llm_clients as L
import stats_tests as S


# Same DSL doc that run_discovery exposes to the LLMs, reproduced here so
# confound_check can prompt Opus with the same vocabulary.
_DSL_DOC = """
Computable ops over columns: drug_name, ind_name, prior_repurposing_count,
established_product, phase, status.

  {"op": "prc_raw"}                                   continuous
  {"op": "prc_threshold", "params": {"k": <int>}}     binary
  {"op": "established"}                               binary
  {"op": "ind_keyword",  "params": {"keywords": [...]}} binary
  {"op": "drug_keyword", "params": {"keywords": [...]}} binary
  {"op": "phase_threshold", "params": {"k": <float>}} binary
      Indication-level trial phase (repoDB 'phase' column) reached >= k WITHOUT
      the pair being approved — approved pairs carry no phase and code as 0.
      Use for phase-mix confounds (e.g. "oncology attempts skew to later
      trial stages"). k in {1, 2, 3}; k=2 or k=3 capture late-stage mix.

Anything else → set feature_spec to null and computable to false.
""".strip()


def propose_confounders(
    hypothesis_text: str,
    feature_spec: dict,
    mechanistic_justification: str,
    discovery_p: float,
    fdr_q: float,
    confirmation_p: float,
) -> list[dict]:
    """
    Call Opus to propose 1-3 specific confounders for a doubly-surviving hypothesis.
    Returns a list of dicts (may be empty on parse failure):
      [{"confound_name": str, "rationale": str, "computable": bool,
        "feature_spec": dict | None}]
    """
    prompt = f"""You are reviewing a drug-repurposing dataset hypothesis that
survived both discovery FDR correction and holdout confirmation. Your job is to
propose specific, named alternative explanations that could account for the
observed correlation WITHOUT the original hypothesis being real.

Hypothesis: "{hypothesis_text}"
Mechanistic justification given: "{mechanistic_justification}"
Feature spec tested: {json.dumps(feature_spec)}
Discovery raw p: {discovery_p:.4g}  |  FDR q: {fdr_q:.4g}
Holdout confirmation p: {confirmation_p:.4g}

Dataset columns: drug_name, ind_name, prior_repurposing_count,
established_product, phase, status.

{_DSL_DOC}

Task: Propose 1-3 SPECIFIC, NAMED confounding variables — not generic categories.
For each:
1. Name it precisely (e.g. "therapeutic-area breadth" not "indication type")
2. Write a ≤2-sentence causal rationale: why might it drive both the predictor
   AND repurposing success, independently of the proposed mechanism?
3. State whether it is computable from the dataset using the DSL above
4. If computable, provide the feature_spec JSON; otherwise null

Return ONLY a JSON array — no prose before or after:
[
  {{
    "confound_name": "...",
    "rationale": "...",
    "computable": true,
    "feature_spec": {{...}}
  }},
  ...
]
""".strip()

    try:
        raw = L.opus(prompt, max_tokens=2000)
        result = L.extract_json(raw)
        if not isinstance(result, list):
            return []
        out = []
        for item in result:
            if not isinstance(item, dict) or "confound_name" not in item:
                continue
            out.append({
                "confound_name": str(item.get("confound_name", "")),
                "rationale": str(item.get("rationale", "")),
                "computable": bool(item.get("computable", False)),
                "feature_spec": item.get("feature_spec"),
            })
        return out
    except Exception as e:  # noqa: BLE001
        return [{"confound_name": f"proposal_error: {e}", "rationale": "",
                 "computable": False, "feature_spec": None}]


def run_confound_checks(
    data: pd.DataFrame,
    primary_feature: pd.Series,
    primary_kind: str,
    outcome_series: pd.Series,
    confounders: list[dict],
    framing: str,
) -> list[dict]:
    """
    For each proposed confound whose feature_spec is computable, run a
    multivariate logistic regression (primary feature + confound covariate)
    and record whether the original effect survives adjustment.

    `data` should be the DISCOVERY split (same data the original test used);
    confound checking is not an independent confirmation — it is an explanatory
    diagnostic on the same discovery data.

    Returns a list of result dicts, one per confound:
      {
        "confound_name": str,
        "computable": bool,
        "rationale": str,
        "adjustment_result": {            # only present when computable
          "or_unadjusted": float,
          "p_unadjusted": float,
          "or_adjusted": float,
          "ci_low_adjusted": float,
          "ci_high_adjusted": float,
          "p_adjusted": float,
          "n": int,
          "survives_adjustment": bool,    # p_adjusted < 0.10 (lenient; discovery is done)
          "note": str,
        } | None
      }
    """
    results: list[dict] = []

    for conf in confounders:
        spec = conf.get("feature_spec")
        entry: dict[str, Any] = {
            "confound_name": conf["confound_name"],
            "rationale": conf.get("rationale", ""),
            "computable": conf.get("computable", False),
            "adjustment_result": None,
        }

        if not conf.get("computable") or spec is None:
            results.append(entry)
            continue

        try:
            sub = data.copy()
            cov_feat = F.compute(sub, spec)
            ok, why = F.separation_ok(cov_feat, outcome_series)

            unadj: S.TestResult
            if primary_kind == "binary":
                unadj = S.fisher_binary(primary_feature, outcome_series)
            else:
                unadj = S.logistic_continuous(primary_feature, outcome_series)

            if not ok:
                entry["adjustment_result"] = {
                    "or_unadjusted": unadj.odds_ratio,
                    "p_unadjusted": unadj.p_value,
                    "or_adjusted": None,
                    "ci_low_adjusted": None,
                    "ci_high_adjusted": None,
                    "p_adjusted": None,
                    "n": unadj.n,
                    "survives_adjustment": None,
                    "note": f"covariate not testable: {why}",
                }
            else:
                adj = S.logistic_binary_adjusted(primary_feature, cov_feat, outcome_series)
                entry["adjustment_result"] = {
                    "or_unadjusted": round(unadj.odds_ratio, 4),
                    "p_unadjusted": round(unadj.p_value, 6),
                    "or_adjusted": round(adj.odds_ratio, 4),
                    "ci_low_adjusted": round(adj.ci_low, 4),
                    "ci_high_adjusted": round(adj.ci_high, 4),
                    "p_adjusted": round(adj.p_value, 6),
                    "n": adj.n,
                    "survives_adjustment": adj.p_value < 0.10,
                    "note": (
                        "effect survives adjustment" if adj.p_value < 0.10
                        else "effect attenuated by adjustment — confound may explain finding"
                    ),
                }
        except Exception as e:  # noqa: BLE001
            entry["adjustment_result"] = {"error": traceback.format_exc(limit=3)}

        results.append(entry)

    return results


def investigate(
    data_discovery: pd.DataFrame,
    hypothesis_text: str,
    feature_spec: dict,
    predictor_kind: str,
    mechanistic_justification: str,
    discovery_p: float,
    fdr_q: float,
    confirmation_p: float,
    framing: str,
) -> dict:
    """
    High-level entry point: propose confounders then run the computable checks.
    Returns a dict safe to JSON-serialise into bisociation_history.confound_check_summary.

    The dict schema:
    {
      "status": "completed" | "skipped" | "error",
      "note": "candidate explanations checked — not the confirmed mechanism",
      "confounders": [ ... ],  # from propose_confounders
      "checks": [ ... ],       # from run_confound_checks
    }
    """
    if "framing_label" in data_discovery.columns:
        framed = data_discovery[data_discovery["framing_label"].isin(
            ["narrow_positive", "narrow_negative"]
            if framing == "narrow"
            else ["narrow_positive", "narrow_negative", "broad_negative"]
        )].copy()
    else:
        # Standard case: raw discovery split with a "label" column, no pre-computed "y".
        pos = data_discovery["label"] == "repurposed-success"
        neg = (data_discovery["label"] == "genuine-failure") if framing == "narrow" else \
              data_discovery["label"].isin(["genuine-failure", "administrative-exclude"])
        framed = data_discovery[pos | neg].copy()
        framed["y"] = (framed["label"] == "repurposed-success").astype(int)

    if F.is_interaction(feature_spec):
        # Interaction hypotheses already ARE the moderator analysis (base effect
        # conditioned on the moderator). Single-covariate logistic adjustment does
        # not map cleanly onto an interaction term, so we skip the confound sweep
        # rather than report a misleading adjusted OR.
        return {"status": "skipped",
                "note": "interaction hypothesis — confound sweep not applicable to an "
                        "interaction term; the moderator is itself the conditioning variable"}

    primary_feat = F.compute(framed, feature_spec)
    ok, _ = F.separation_ok(primary_feat, framed["y"])
    if not ok:
        return {"status": "skipped",
                "note": "primary feature has no variance on discovery data; skipping confound check"}

    confounders = propose_confounders(
        hypothesis_text, feature_spec, mechanistic_justification,
        discovery_p, fdr_q, confirmation_p,
    )

    checks = run_confound_checks(
        framed, primary_feat, predictor_kind, framed["y"], confounders, framing,
    )

    return {
        "status": "completed",
        "note": "candidate explanations checked — not the confirmed mechanism",
        "confounders": confounders,
        "checks": checks,
    }
