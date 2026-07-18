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


class FeatureError(ValueError):
    pass


def is_supported(spec: dict) -> bool:
    return isinstance(spec, dict) and spec.get("op") in SUPPORTED_OPS


def is_confounded(spec: dict) -> bool:
    return spec.get("op") in CONFOUNDED_OPS


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
    raise FeatureError(f"unsupported op: {op!r}")


def predictor_kind(spec: dict) -> str:
    """Fixed, pre-registered binary/continuous classification (decided by op)."""
    return "continuous" if spec.get("op") == "prc_raw" else "binary"


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
