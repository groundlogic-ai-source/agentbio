"""
Pre-registered statistical tests for the discovery phase.

Rule (fixed before testing, decided by the feature's predictor kind):
  - binary predictor     -> Fisher's exact test    (odds ratio + 95% CI + p-value)
  - continuous predictor -> logistic regression     (odds ratio + 95% CI + p-value)

Outcome is coded 1 = repurposed-success (positive), 0 = failure (negative). All
tests run on the DISCOVERY half only; the confirmation half is held out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import fisher_exact


@dataclass
class TestResult:
    test_type: str
    odds_ratio: float
    ci_low: float
    ci_high: float
    p_value: float
    n: int
    note: str = ""


def fisher_binary(feature: pd.Series, outcome: pd.Series) -> TestResult:
    df = pd.DataFrame({"f": feature, "y": outcome}).dropna()
    df = df.copy()
    df["f"] = df["f"].astype(int)
    df["y"] = df["y"].astype(int)
    # 2x2: rows feature 0/1, cols outcome 0/1
    ct = pd.crosstab(df["f"], df["y"]).reindex(index=[0, 1], columns=[0, 1], fill_value=0)
    a = ct.loc[1, 1]  # feature=1, success
    b = ct.loc[1, 0]  # feature=1, failure
    c = ct.loc[0, 1]  # feature=0, success
    d = ct.loc[0, 0]  # feature=0, failure
    orr, p = fisher_exact([[a, b], [c, d]], alternative="two-sided")
    # Haldane-Anscombe 0.5 correction for the CI when any cell is zero
    a2, b2, c2, d2 = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    log_or = math.log((a2 * d2) / (b2 * c2))
    se = math.sqrt(1 / a2 + 1 / b2 + 1 / c2 + 1 / d2)
    ci_low = math.exp(log_or - 1.96 * se)
    ci_high = math.exp(log_or + 1.96 * se)
    return TestResult("fisher_exact", float(orr), ci_low, ci_high, float(p), int(len(df)))


def logistic_binary_adjusted(
    feature: pd.Series, covariate: pd.Series, outcome: pd.Series
) -> TestResult:
    """
    Logistic regression for a binary primary predictor with one binary/continuous
    covariate.  Returns OR/CI/p for the primary feature after adjustment.
    Used by confound-investigation to test whether a confirmed effect survives
    covariate control.
    """
    df = pd.DataFrame({
        "f": feature.astype(float), "c": covariate.astype(float),
        "y": outcome,
    }).dropna()
    df = df.copy()
    df["y"] = df["y"].astype(int)
    X = sm.add_constant(df[["f", "c"]])
    model = sm.Logit(df["y"], X)
    res = model.fit(disp=0, method="bfgs", maxiter=200)
    coef = res.params["f"]
    ci = res.conf_int().loc["f"]
    p = res.pvalues["f"]
    return TestResult(
        "logistic_adjusted",
        float(np.exp(coef)), float(np.exp(ci[0])), float(np.exp(ci[1])),
        float(p), int(len(df)),
        note="OR adjusted for one covariate",
    )


def logistic_interaction(
    base: pd.Series, moderator: pd.Series, outcome: pd.Series
) -> TestResult:
    """
    Test whether the effect of `base` on the outcome DIFFERS across the levels of a
    binary `moderator`. Fits y ~ base + moderator + base:moderator and reports the
    OR/CI/p of the INTERACTION term (base:moderator). An interaction OR far from 1
    with a small p means the base predictor's effect is moderated.

    The returned odds_ratio is the multiplicative change in the base effect's odds
    ratio when the moderator flips from 0 to 1 (not the base main effect itself).
    """
    df = pd.DataFrame({
        "b": base.astype(float),
        "m": moderator.astype(float),
        "y": outcome,
    }).dropna()
    df = df.copy()
    df["y"] = df["y"].astype(int)
    df["bm"] = df["b"] * df["m"]
    X = sm.add_constant(df[["b", "m", "bm"]])
    model = sm.Logit(df["y"], X)
    res = model.fit(disp=0, method="bfgs", maxiter=200)
    coef = res.params["bm"]
    ci = res.conf_int().loc["bm"]
    p = res.pvalues["bm"]
    return TestResult(
        "logistic_interaction",
        float(np.exp(coef)), float(np.exp(ci[0])), float(np.exp(ci[1])),
        float(p), int(len(df)),
        note="OR of the interaction term (base effect ratio when moderator=1 vs 0)",
    )


def logistic_interaction3(
    base: pd.Series, moderator: pd.Series, moderator2: pd.Series, outcome: pd.Series
) -> TestResult:
    """
    Three-way conditional interaction. Fits
        y ~ b + m1 + m2 + b:m1 + b:m2 + m1:m2 + b:m1:m2
    and reports the OR/CI/p of the THREE-WAY term (b:m1:m2).

    That term answers "is the moderation of `base` by `moderator` itself
    different depending on `moderator2`?" — i.e. the claim "X behaves this way
    under Y when Z is happening, but not when Z is absent".

    The returned odds_ratio is the ratio of the two-way (base x moderator)
    interaction odds ratio at moderator2=1 versus moderator2=0. It is NOT a
    main effect and must not be read as one.

    Power is NOT checked here — the pre-registered events-per-parameter and
    per-stratum guards run in the caller before this is invoked.
    """
    df = pd.DataFrame({
        "b": base.astype(float),
        "m1": moderator.astype(float),
        "m2": moderator2.astype(float),
        "y": outcome,
    }).dropna()
    df = df.copy()
    df["y"] = df["y"].astype(int)
    df["b_m1"] = df["b"] * df["m1"]
    df["b_m2"] = df["b"] * df["m2"]
    df["m1_m2"] = df["m1"] * df["m2"]
    df["b_m1_m2"] = df["b"] * df["m1"] * df["m2"]
    X = sm.add_constant(df[["b", "m1", "m2", "b_m1", "b_m2", "m1_m2", "b_m1_m2"]])
    model = sm.Logit(df["y"], X)
    res = model.fit(disp=0, method="bfgs", maxiter=400)
    coef = res.params["b_m1_m2"]
    ci = res.conf_int().loc["b_m1_m2"]
    p = res.pvalues["b_m1_m2"]
    return TestResult(
        "logistic_interaction3",
        float(np.exp(coef)), float(np.exp(ci[0])), float(np.exp(ci[1])),
        float(p), int(len(df)),
        note=(
            "OR of the three-way term (base x moderator interaction ratio when "
            "moderator2=1 vs 0)"
        ),
    )


def logistic_continuous(feature: pd.Series, outcome: pd.Series) -> TestResult:
    df = pd.DataFrame({"f": feature.astype(float), "y": outcome}).dropna()
    df = df.copy()
    df["y"] = df["y"].astype(int)
    X = sm.add_constant(df[["f"]])
    model = sm.Logit(df["y"], X)
    res = model.fit(disp=0, method="bfgs", maxiter=200)
    coef = res.params["f"]
    ci = res.conf_int().loc["f"]
    p = res.pvalues["f"]
    return TestResult(
        "logistic",
        float(np.exp(coef)),
        float(np.exp(ci[0])),
        float(np.exp(ci[1])),
        float(p),
        int(len(df)),
        note="OR per +1 unit of predictor",
    )
