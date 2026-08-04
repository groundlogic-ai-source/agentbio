"""
Feature DSL — turns an LLM-proposed feature spec into a computable column over the
real dataset schema, or refuses it.

Available real columns (base dataset):
  drug_name, ind_name, prior_repurposing_count, established_product, phase, status

Available enriched columns (present when enriched_dataset.csv has been generated):
  pubchem_mw            float | NaN — molecular weight from PubChem
  pubchem_xlogp         float | NaN — XLogP lipophilicity from PubChem
  chembl_molecule_type  str   | NaN — "Small molecule", "Antibody", "Enzyme", etc.
  chembl_max_phase      float | NaN — highest global development phase (0–4) in ChEMBL
  chembl_oral           float | NaN — 1.0 = oral drug, 0.0 = non-oral (from ChEMBL)

Supported ops (fixed vocabulary):

  Base dataset ops:
  - prc_raw              continuous = prior_repurposing_count
  - prc_threshold        binary     = prior_repurposing_count >= params["k"]
  - established          binary     = established_product
  - ind_keyword          binary     = ind_name (lowercased) contains ANY params["keywords"]
  - drug_keyword         binary     = drug_name (lowercased) contains ANY params["keywords"]

  Enriched ops (require enriched_dataset.csv — raise FeatureError if columns absent):
  - mw_raw               continuous = pubchem_mw (molecular weight, Da)
  - xlogp_raw            continuous = pubchem_xlogp (lipophilicity)
  - mw_threshold         binary     = pubchem_mw >= params["k"]
  - xlogp_threshold      binary     = pubchem_xlogp >= params["k"]
  - is_small_molecule    binary     = chembl_molecule_type == "Small molecule"
  - is_oral              binary     = chembl_oral == 1.0
  - global_max_phase_raw continuous = chembl_max_phase
  - global_max_phase_threshold binary = chembl_max_phase >= params["k"]

Any spec whose op is not in this set cannot be computed from the schema and is
rejected (caller marks it NEEDS_ENRICHMENT).

Circularity guard: prior_repurposing_count is definitionally tied to the label
(repurposed-success requires prc>=1). Features built on prc_raw / prc_threshold are
flagged `label_confounded=True` so the Lead review / report can treat them with
suspicion; the pre-test separation check additionally rejects any feature that
perfectly separates the outcome (which also breaks Fisher/logistic).
"""
from __future__ import annotations

import re

import pandas as pd

SUPPORTED_OPS = {
    "prc_raw", "prc_threshold", "established", "ind_keyword", "drug_keyword",
    "mw_raw", "xlogp_raw", "mw_threshold", "xlogp_threshold",
    "is_small_molecule", "is_oral", "global_max_phase_raw", "global_max_phase_threshold",
    "phase_threshold",
}
# Label-confounded: prc is definitionally tied to the label; phase_threshold is
# structurally tied to it (only UNAPPROVED pairs carry a repoDB trial phase, so
# the feature exists almost exclusively among failures). phase_threshold is a
# legitimate CONFOUND COVARIATE (e.g. phase-mix adjustment) but never a valid
# hypothesis predictor.
CONFOUNDED_OPS = {"prc_raw", "prc_threshold", "phase_threshold"}
# Enriched ops that require pubchem/chembl columns — graceful error if absent.
ENRICHED_OPS = {
    "mw_raw", "xlogp_raw", "mw_threshold", "xlogp_threshold",
    "is_small_molecule", "is_oral", "global_max_phase_raw", "global_max_phase_threshold",
}
# Ops that produce a single 0/1 column and can therefore serve as a moderator or a
# base term inside an interaction. prc_raw / mw_raw / xlogp_raw / global_max_phase_raw
# are continuous — they may be a base but are never binary moderators.
_BINARY_OPS = {
    "prc_threshold", "established", "ind_keyword", "drug_keyword",
    "mw_threshold", "xlogp_threshold", "is_small_molecule", "is_oral",
    "global_max_phase_threshold", "phase_threshold",
}

# Disease-stage terminology stems that near-tautologically proxy administrative-exclude
# label assignment. Refractory/resistant/relapsed indications presuppose an existing
# approved first-line product by definition, so they co-vary with the label by
# construction rather than by biology.
_STAGE_PROXY_STEMS = frozenset({"refract", "resist", "relaps", "salvage"})


class FeatureError(ValueError):
    pass


_PHASE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _indication_phase_num(s: pd.Series) -> pd.Series:
    """
    Map repoDB indication-level phase strings to the HIGHEST phase number
    reached: "Phase 1" -> 1.0, "Phase 1/Phase 2" -> 2.0, "Phase 2/Phase 3" ->
    3.0. NaN (approved pairs) and unparseable values stay NaN.
    """
    def one(v) -> float:
        if not isinstance(v, str):
            return float("nan")
        nums = [float(x) for x in _PHASE_NUM_RE.findall(v)]
        return max(nums) if nums else float("nan")
    return s.map(one)


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


def _require_col(df: pd.DataFrame, col: str, op: str) -> pd.Series:
    """Return df[col] or raise FeatureError if the column is missing (enriched dataset not generated)."""
    if col not in df.columns:
        raise FeatureError(
            f"op '{op}' requires column '{col}' which is absent — "
            f"run enrich_dataset.py first to generate enriched_dataset.csv"
        )
    return df[col]


def compute(df: pd.DataFrame, spec: dict) -> pd.Series:
    """Return a numeric Series (binary 0/1 or continuous) aligned to df.index."""
    op = spec.get("op")
    params = spec.get("params", {}) or {}

    # ---- base dataset ops ----
    if op == "prc_raw":
        return df["prior_repurposing_count"].astype(float)
    if op == "prc_threshold":
        k = int(params["k"])
        return (df["prior_repurposing_count"] >= k).astype(int)
    if op == "established":
        return df["established_product"].fillna(False).astype(bool).astype(int)
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

    # ---- enriched ops (require enriched_dataset.csv columns) ----
    if op == "mw_raw":
        return _require_col(df, "pubchem_mw", op).astype(float)
    if op == "xlogp_raw":
        return _require_col(df, "pubchem_xlogp", op).astype(float)
    if op == "mw_threshold":
        k = float(params["k"])
        col = _require_col(df, "pubchem_mw", op).astype(float)
        return (col >= k).astype(int)
    if op == "xlogp_threshold":
        k = float(params["k"])
        col = _require_col(df, "pubchem_xlogp", op).astype(float)
        return (col >= k).astype(int)
    if op == "is_small_molecule":
        col = _require_col(df, "chembl_molecule_type", op)
        return (col == "Small molecule").astype(int)
    if op == "is_oral":
        col = _require_col(df, "chembl_oral", op).astype(float)
        return (col == 1.0).astype(int)
    if op == "global_max_phase_raw":
        return _require_col(df, "chembl_max_phase", op).astype(float)
    if op == "global_max_phase_threshold":
        k = float(params["k"])
        col = _require_col(df, "chembl_max_phase", op).astype(float)
        return (col >= k).astype(int)

    # Indication-level trial phase from repoDB (NOT the ChEMBL drug-global
    # max phase above). Only unapproved pairs carry a phase value; approved
    # pairs are NaN and code as 0, so this reads "reached phase >= k WITHOUT
    # being approved" — structurally tied to the failure label (CONFOUNDED_OPS).
    # Intended for confound adjustment (e.g. the phase-mix confound on the
    # oncology finding), not for hypothesis prediction.
    if op == "phase_threshold":
        k = float(params["k"])
        return (_indication_phase_num(df["phase"]) >= k).astype(int)

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
