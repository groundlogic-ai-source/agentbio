"""
Feature DSL — turns an LLM-proposed feature spec into a computable column over the
real dataset schema, or refuses it.

Available real columns (nothing else exists; anything requiring more is
NEEDS_ENRICHMENT, never a faked proxy):
  drug_name, ind_name, prior_repurposing_count, established_product, phase, status

Supported ops (fixed vocabulary):
  - prc_raw            continuous = prior_repurposing_count
  - prc_threshold      binary     = prior_repurposing_count >= params["k"]
  - established        binary     = established_product
  - ind_keyword        binary     = ind_name (lowercased) contains ANY params["keywords"]
  - drug_keyword       binary     = drug_name (lowercased) contains ANY params["keywords"]

Any spec whose op is not in this set cannot be computed from the schema and is
rejected (caller marks it NEEDS_ENRICHMENT).

Circularity guard: prior_repurposing_count is definitionally tied to the label
(repurposed-success requires prc>=1). Features built on prc_raw / prc_threshold are
flagged `label_confounded=True` so the Lead review / report can treat them with
suspicion; the pre-test separation check additionally rejects any feature that
perfectly separates the outcome (which also breaks Fisher/logistic).
"""
from __future__ import annotations

import pandas as pd

SUPPORTED_OPS = {"prc_raw", "prc_threshold", "established", "ind_keyword", "drug_keyword"}
CONFOUNDED_OPS = {"prc_raw", "prc_threshold"}
# Ops that produce a single 0/1 column and can therefore serve as a moderator or a
# base term inside an interaction. prc_raw is continuous, so it may be a base but is
# never a binary moderator.
_BINARY_OPS = {"prc_threshold", "established", "ind_keyword", "drug_keyword"}

# Disease-stage terminology stems that near-tautologically proxy administrative-exclude
# label assignment. Refractory/resistant/relapsed indications presuppose an existing
# approved first-line product by definition, so they co-vary with the label by
# construction rather than by biology.
_STAGE_PROXY_STEMS = frozenset({"refract", "resist", "relaps", "salvage"})


class FeatureError(ValueError):
    pass


def is_interaction(spec: dict) -> bool:
    return isinstance(spec, dict) and spec.get("op") == "interaction"


def is_supported(spec: dict) -> bool:
    if not isinstance(spec, dict):
        return False
    op = spec.get("op")
    if op in SUPPORTED_OPS:
        return True
    if op == "interaction":
        params = spec.get("params", {}) or {}
        base = params.get("base")
        mod = params.get("moderator")
        if not (isinstance(base, dict) and base.get("op") in SUPPORTED_OPS):
            return False
        if not (isinstance(mod, dict) and mod.get("op") in _BINARY_OPS):
            return False
        # Reject a structurally non-identifiable design: an identical base and
        # moderator makes base:moderator collinear with the base term, so the
        # logistic fit is singular and returns NaN statistics.
        if base == mod:
            return False
        return True
    return False


def is_confounded(spec: dict) -> bool:
    """An interaction is confounded if either of its terms is label-confounded."""
    if spec.get("op") == "interaction":
        params = spec.get("params", {}) or {}
        return (
            is_confounded(params.get("base", {}) or {})
            or is_confounded(params.get("moderator", {}) or {})
        )
    return spec.get("op") in CONFOUNDED_OPS


def is_indication_stage_proxy(spec: dict) -> bool:
    """
    True if this ind_keyword feature uses disease-stage terminology that
    near-tautologically proxies the administrative-exclude label.
    Refractory/resistant/relapsed indications presuppose an existing approved
    first-line drug, so the keyword co-varies with label assignment by
    construction, not by biology.
    """
    if spec.get("op") == "interaction":
        params = spec.get("params", {}) or {}
        return (
            is_indication_stage_proxy(params.get("base", {}) or {})
            or is_indication_stage_proxy(params.get("moderator", {}) or {})
        )
    if spec.get("op") != "ind_keyword":
        return False
    kws = [str(k).lower() for k in spec.get("params", {}).get("keywords", [])]
    return any(stem in kw for kw in kws for stem in _STAGE_PROXY_STEMS)


def compute(df: pd.DataFrame, spec: dict) -> pd.Series:
    """Return a numeric Series (binary 0/1 or continuous) aligned to df.index."""
    op = spec.get("op")
    params = spec.get("params", {}) or {}
    if op == "prc_raw":
        return df["prior_repurposing_count"].astype(float)
    if op == "prc_threshold":
        k = int(params["k"])
        return (df["prior_repurposing_count"] >= k).astype(int)
    if op == "established":
        return df["established_product"].astype(bool).astype(int)
    if op == "ind_keyword":
        kws = [str(k).lower() for k in params.get("keywords", [])]
        if not kws:
            raise FeatureError("ind_keyword requires non-empty 'keywords'")
        low = df["ind_name"].fillna("").str.lower()
        return low.apply(lambda s: int(any(k in s for k in kws)))
    if op == "drug_keyword":
        kws = [str(k).lower() for k in params.get("keywords", [])]
        if not kws:
            raise FeatureError("drug_keyword requires non-empty 'keywords'")
        low = df["drug_name"].fillna("").str.lower()
        return low.apply(lambda s: int(any(k in s for k in kws)))
    if op == "interaction":
        raise FeatureError("interaction specs must be evaluated via compute_interaction")
    raise FeatureError(f"unsupported op: {op!r}")


def compute_interaction(df: pd.DataFrame, spec: dict) -> tuple[pd.Series, pd.Series]:
    """
    Return (base_series, moderator_series) for an interaction spec. The moderator is
    always 0/1 binary; the base may be binary or continuous.
    """
    if spec.get("op") != "interaction":
        raise FeatureError("compute_interaction requires an 'interaction' op")
    params = spec.get("params", {}) or {}
    base_spec = params.get("base")
    mod_spec = params.get("moderator")
    if not isinstance(base_spec, dict) or not isinstance(mod_spec, dict):
        raise FeatureError("interaction requires 'base' and 'moderator' specs")
    if mod_spec.get("op") not in _BINARY_OPS:
        raise FeatureError("interaction 'moderator' must be a binary op")
    return compute(df, base_spec), compute(df, mod_spec)


def predictor_kind(spec: dict) -> str:
    """Fixed, pre-registered binary/continuous/interaction classification (by op)."""
    op = spec.get("op")
    if op == "interaction":
        return "interaction"
    return "continuous" if op == "prc_raw" else "binary"


def separation_ok(feature: pd.Series, outcome: pd.Series) -> tuple[bool, str]:
    """
    Reject features that perfectly separate the outcome (degenerate: infinite OR,
    zero Fisher cell, non-converging logistic). Returns (ok, reason).
    """
    df = pd.DataFrame({"f": feature, "y": outcome}).dropna()
    if df["y"].nunique() < 2:
        return False, "outcome has <2 classes in this subset"
    if df["f"].nunique() < 2:
        return False, "feature is constant in this subset"
    # binary feature: check for empty cells in the 2x2 table
    if set(df["f"].unique()) <= {0, 1}:
        ct = pd.crosstab(df["f"], df["y"])
        if (ct.values == 0).any():
            return False, "perfect separation (empty cell in 2x2 table)"
    return True, ""
