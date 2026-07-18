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
    df = pd.DataFrame({"f": feature.astype(int), "y": outcome.astype(int)}).dropna()
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
        "y": outcome.astype(int),
    }).dropna()
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


def logistic_continuous(feature: pd.Series, outcome: pd.Series) -> TestResult:
    df = pd.DataFrame({"f": feature.astype(float), "y": outcome.astype(int)}).dropna()
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
