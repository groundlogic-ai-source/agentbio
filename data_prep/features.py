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

import json
import re

import pandas as pd

# ── Boolean composition ops (radical_hypotheses_preregistration.md v1) ────────
# Encode a multi-part conditional claim ("X AND Y AND NOT Z") as ONE binary
# column, so it is tested by Fisher's exact on a 2x2 table (1 parameter)
# instead of a many-parameter interaction model. This is the only route by
# which a multi-part hypothesis is powered in the narrow framing, which has
# just 51 genuine failures in the discovery half.
_COMPOSITION_OPS = {"all_of", "any_of", "not_op"}

SUPPORTED_OPS = {
    "prc_raw", "prc_threshold", "established", "ind_keyword", "drug_keyword",
    "mw_raw", "xlogp_raw", "mw_threshold", "xlogp_threshold",
    "is_small_molecule", "is_oral", "global_max_phase_raw", "global_max_phase_threshold",
    "phase_threshold",
} | _COMPOSITION_OPS

# ── Pre-registered power thresholds (frozen before implementation) ───────────
MIN_EVENTS_PER_PARAMETER = 10   # standard EPP rule for logistic fits
MIN_INTERACTION_STRATUM_N = 30  # rows required in each moderator stratum
MIN_COMPOSITE_TRUE_N = 10       # rows required in a composed feature's TRUE cell
MAX_COMPOSITION_TERMS = 4
MAX_COMPOSITION_DEPTH = 3

# Model parameter counts (including intercept) used by the EPP guard.
_N_PARAMS = {"interaction": 4, "interaction3": 8}

# Source column behind each enriched op. Used to propagate genuine missingness
# through composition: an unobserved XLogP must NOT silently become "not
# lipophilic" when wrapped in not_op. Ops absent from this map are always
# observed (indication/drug text, established flag).
_OP_SOURCE_COL = {
    "mw_raw": "pubchem_mw", "mw_threshold": "pubchem_mw",
    "xlogp_raw": "pubchem_xlogp", "xlogp_threshold": "pubchem_xlogp",
    "is_small_molecule": "chembl_molecule_type",
    "is_oral": "chembl_oral",
    "global_max_phase_raw": "chembl_max_phase",
    "global_max_phase_threshold": "chembl_max_phase",
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
} | _COMPOSITION_OPS

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


def _is_binary_spec(spec, depth: int = 1) -> bool:
    """True if `spec` yields a single 0/1 column (composition validated recursively)."""
    if not isinstance(spec, dict):
        return False
    op = spec.get("op")
    if op in _COMPOSITION_OPS:
        return _composition_ok(spec, depth)
    return op in _BINARY_OPS


def _composition_ok(spec: dict, depth: int = 1) -> bool:
    """Structural validity of an all_of / any_of / not_op spec."""
    if depth > MAX_COMPOSITION_DEPTH:
        return False
    op = spec.get("op")
    params = spec.get("params", {}) or {}
    if op == "not_op":
        return _is_binary_spec(params.get("term"), depth + 1)
    terms = params.get("terms")
    if not isinstance(terms, list) or not (2 <= len(terms) <= MAX_COMPOSITION_TERMS):
        return False
    if any(not _is_binary_spec(t, depth + 1) for t in terms):
        return False
    # A repeated term is degenerate: "X AND X" is just X, and it would evade
    # feature-level dedup by looking like a novel composite.
    canon = [json.dumps(t, sort_keys=True) for t in terms]
    return len(set(canon)) == len(canon)


def is_supported(spec: dict) -> bool:
    if not isinstance(spec, dict):
        return False
    op = spec.get("op")
    # Composition must be checked BEFORE the SUPPORTED_OPS shortcut, or a
    # malformed all_of would pass validation on its op name alone.
    if op in _COMPOSITION_OPS:
        return _composition_ok(spec)
    if op in SUPPORTED_OPS:
        return True
    if op == "interaction":
        params = spec.get("params", {}) or {}
        base = params.get("base")
        mod = params.get("moderator")
        if not (isinstance(base, dict) and _spec_usable_as_base(base)):
            return False
        if not _is_binary_spec(mod):
            return False
        # Reject a structurally non-identifiable design: an identical base and
        # moderator makes base:moderator collinear with the base term, so the
        # logistic fit is singular and returns NaN statistics.
        if base == mod:
            return False
        return True
    if op == "interaction3":
        params = spec.get("params", {}) or {}
        base = params.get("base")
        m1 = params.get("moderator")
        m2 = params.get("moderator2")
        if not (isinstance(base, dict) and _spec_usable_as_base(base)):
            return False
        if not (_is_binary_spec(m1) and _is_binary_spec(m2)):
            return False
        # Any repeated term collapses the three-way product onto a lower-order
        # term, making the b:m1:m2 coefficient non-identifiable.
        if base == m1 or base == m2 or m1 == m2:
            return False
        return True
    return False


def _spec_usable_as_base(spec: dict) -> bool:
    """A base term may be any supported op (binary or continuous)."""
    op = spec.get("op")
    if op in _COMPOSITION_OPS:
        return _composition_ok(spec)
    return op in SUPPORTED_OPS


def _sub_specs(spec: dict) -> list[dict]:
    """Every nested child spec of an interaction / composition op."""
    op = spec.get("op")
    params = spec.get("params", {}) or {}
    if op == "interaction":
        return [params.get("base") or {}, params.get("moderator") or {}]
    if op == "interaction3":
        return [
            params.get("base") or {},
            params.get("moderator") or {},
            params.get("moderator2") or {},
        ]
    if op == "not_op":
        return [params.get("term") or {}]
    if op in ("all_of", "any_of"):
        return [t for t in (params.get("terms") or []) if isinstance(t, dict)]
    return []


def is_confounded(spec: dict) -> bool:
    """Confounded if ANY nested term is label-confounded.

    Recurses through interactions AND boolean compositions so a composed
    feature cannot smuggle a prior_repurposing_count term past the hard
    guardrail by burying it inside an all_of.
    """
    if not isinstance(spec, dict):
        return False
    children = _sub_specs(spec)
    if children:
        return any(is_confounded(c) for c in children)
    return spec.get("op") in CONFOUNDED_OPS


def is_indication_stage_proxy(spec: dict) -> bool:
    """
    True if this ind_keyword feature uses disease-stage terminology that
    near-tautologically proxies the administrative-exclude label.
    Refractory/resistant/relapsed indications presuppose an existing approved
    first-line drug, so the keyword co-varies with label assignment by
    construction, not by biology.

    Recurses through interactions AND boolean compositions — burying a
    "refractory" keyword inside an all_of must not evade the guardrail.
    """
    if not isinstance(spec, dict):
        return False
    children = _sub_specs(spec)
    if children:
        return any(is_indication_stage_proxy(c) for c in children)
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


def _missing_mask(df: pd.DataFrame, spec: dict) -> pd.Series:
    """
    True where the data behind `spec` is genuinely unobserved.

    Threshold ops code NaN as 0 ("not above the cutoff"), which is harmless on
    its own but becomes a false claim under negation: an unknown XLogP must not
    read as "not lipophilic" inside a not_op. Composition therefore propagates
    missingness and those rows drop from the test (complete-case analysis).
    """
    if not isinstance(spec, dict):
        return pd.Series(False, index=df.index)
    children = _sub_specs(spec)
    if children:
        out = pd.Series(False, index=df.index)
        for c in children:
            out = out | _missing_mask(df, c)
        return out
    col = _OP_SOURCE_COL.get(spec.get("op"))
    if col and col in df.columns:
        return df[col].isna()
    return pd.Series(False, index=df.index)


def compute(df: pd.DataFrame, spec: dict) -> pd.Series:
    """Return a numeric Series (binary 0/1 or continuous) aligned to df.index."""
    op = spec.get("op")
    params = spec.get("params", {}) or {}

    # ---- boolean composition (multi-part conditional subgroups) ----
    if op in _COMPOSITION_OPS:
        if op == "not_op":
            out = 1.0 - compute(df, params.get("term") or {}).astype(float)
        else:
            terms = params.get("terms") or []
            if not terms:
                raise FeatureError(f"{op} requires a non-empty 'terms' list")
            series = [compute(df, t).astype(float) for t in terms]
            acc = series[0]
            for s in series[1:]:
                acc = acc * s if op == "all_of" else (acc + s).clip(upper=1.0)
            out = acc
        return out.where(~_missing_mask(df, spec))

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
    if op == "interaction3":
        raise FeatureError("interaction3 specs must be evaluated via compute_interaction3")
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
    if not _is_binary_spec(mod_spec):
        raise FeatureError("interaction 'moderator' must be a binary op")
    return compute(df, base_spec), compute(df, mod_spec)


def compute_interaction3(
    df: pd.DataFrame, spec: dict
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Return (base, moderator, moderator2) for a three-way interaction spec.
    Both moderators are 0/1 binary; the base may be binary or continuous.
    """
    if spec.get("op") != "interaction3":
        raise FeatureError("compute_interaction3 requires an 'interaction3' op")
    params = spec.get("params", {}) or {}
    base_spec = params.get("base")
    m1_spec = params.get("moderator")
    m2_spec = params.get("moderator2")
    if not all(isinstance(s, dict) for s in (base_spec, m1_spec, m2_spec)):
        raise FeatureError(
            "interaction3 requires 'base', 'moderator' and 'moderator2' specs"
        )
    if not (_is_binary_spec(m1_spec) and _is_binary_spec(m2_spec)):
        raise FeatureError("interaction3 moderators must both be binary ops")
    return compute(df, base_spec), compute(df, m1_spec), compute(df, m2_spec)


# ── Pre-registered power guards ───────────────────────────────────────────────

def events_per_parameter_ok(outcome: pd.Series, n_params: int) -> tuple[bool, str]:
    """
    Standard events-per-parameter rule: the MINORITY outcome class must supply
    at least MIN_EVENTS_PER_PARAMETER observations per fitted parameter.

    On this dataset the narrow framing carries only ~51 genuine failures in the
    discovery half, so an 8-parameter three-way fit is refused there by design
    rather than returning an underpowered p-value.
    """
    y = pd.Series(outcome).dropna().astype(int)
    if y.nunique() < 2:
        return False, "outcome has <2 classes in this subset"
    minority = int(min((y == 1).sum(), (y == 0).sum()))
    need = MIN_EVENTS_PER_PARAMETER * n_params
    if minority < need:
        return False, (
            f"insufficient power: {minority} minority-class events for "
            f"{n_params} parameters (need >= {need} at "
            f"{MIN_EVENTS_PER_PARAMETER} events/parameter)"
        )
    return True, ""


def composite_support_ok(feature: pd.Series, outcome: pd.Series) -> tuple[bool, str]:
    """A composed binary subgroup must contain at least MIN_COMPOSITE_TRUE_N rows."""
    df = pd.DataFrame({"f": feature, "y": outcome}).dropna()
    n_true = int((df["f"].astype(float) == 1).sum())
    if n_true < MIN_COMPOSITE_TRUE_N:
        return False, (
            f"composed subgroup too small: {n_true} rows satisfy the condition "
            f"(need >= {MIN_COMPOSITE_TRUE_N})"
        )
    return True, ""


# Ops producing a continuous column. Anything here MUST route to a logistic
# test: sending it to Fisher's exact casts the value to int and reindexes the
# 2x2 table to [0, 1], which zeroes every cell and reports a meaningless p=1.
# (Observed in production: a standalone mw_raw hypothesis logged p=1 in both
# framings while the correct logistic fit on the same column gave p=0.0019.)
_CONTINUOUS_OPS = {"prc_raw", "mw_raw", "xlogp_raw", "global_max_phase_raw"}


def predictor_kind(spec: dict) -> str:
    """Fixed, pre-registered binary/continuous/interaction classification (by op)."""
    op = spec.get("op")
    if op in ("interaction", "interaction3"):
        return op
    return "continuous" if op in _CONTINUOUS_OPS else "binary"


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
