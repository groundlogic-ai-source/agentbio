"""
Persistent, git-tracked hypothesis registry for the bisociative discovery pipeline.

Two CSV stores (human-auditable, diff-friendly), kept under data_prep/registry/:

1. hypothesis_log.csv  — the CUMULATIVE log of every statistical test ever run
   against this dataset, across ALL runs. This is the single source of truth for
   Benjamini-Hochberg FDR correction: FDR is always computed over the entire log,
   never just the current run's batch, so repeated future runs with new domains do
   not silently recreate the multiple-comparisons problem this design prevents.

2. bisociation_history.csv — the narrative record: every proposed domain (used or
   not, passed or not), its proposing LLM, the resulting hypothesis, and its
   discovery/confirmation outcomes. Shares `test_id` / `hypothesis_id` with the log
   so the two can be cross-referenced.

IDs (assigned in run_discovery, prefixed with the run_id so they are collision-free
across concurrent runs without any global counter):
- hypothesis_id: per distinct hypothesis, e.g. run-0f0aea63-H01.
- test_id: per statistical test = (hypothesis x outcome_framing), e.g. run-0f0aea63-T0001.
  A hypothesis tested under both the narrow and broad framing produces two test_ids
  that share one hypothesis_id (two comparisons for FDR).
"""
from __future__ import annotations

import contextlib
import fcntl
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REG_DIR = os.path.join(HERE, "registry")
LOG_CSV = os.path.join(REG_DIR, "hypothesis_log.csv")
HIST_CSV = os.path.join(REG_DIR, "bisociation_history.csv")
LOCK_FILE = os.path.join(REG_DIR, ".registry.lock")


@contextlib.contextmanager
def _registry_lock():
    """
    Exclusive advisory lock around read-modify-write of the registry CSVs, so two
    concurrent runs cannot interleave appends and corrupt the files. Combined with
    run-id-prefixed IDs (assigned in run_discovery), this makes the registry safe
    under concurrency.
    """
    os.makedirs(REG_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)

LOG_COLS = [
    "test_id",
    "hypothesis_id",
    "run_id",
    "run_timestamp",
    "hypothesis_text",
    "test_type",
    "outcome_framing",
    "raw_p",
]

HIST_COLS = [
    "test_id",
    "hypothesis_id",
    "run_id",
    "session_timestamp",
    "domain_description",
    "proposing_llm",
    "resulting_hypothesis_text",
    "discovery_test_type",
    "outcome_framing",
    "discovery_raw_p",
    "discovery_fdr_p",
    "discovery_pass",
    "confirmation_pass",
    "outcome_note",
]


def _load(path: str, cols: list[str]) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path)
        for c in cols:
            if c not in df.columns:
                df[c] = pd.NA
        return df[cols]
    return pd.DataFrame(columns=cols)


def load_log() -> pd.DataFrame:
    return _load(LOG_CSV, LOG_COLS)


def load_history() -> pd.DataFrame:
    return _load(HIST_CSV, HIST_COLS)


def append_log_rows(rows: list[dict]) -> pd.DataFrame:
    with _registry_lock():
        df = load_log()
        add = pd.DataFrame(rows, columns=LOG_COLS)
        out = pd.concat([df, add], ignore_index=True)
        out.to_csv(LOG_CSV, index=False)
        return out


def append_history_rows(rows: list[dict]) -> pd.DataFrame:
    with _registry_lock():
        df = load_history()
        add = pd.DataFrame(rows, columns=HIST_COLS)
        out = pd.concat([df, add], ignore_index=True)
        out.to_csv(HIST_CSV, index=False)
        return out


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    """
    Return BH-adjusted p-values (q-values) in the SAME order as the input.
    Standard step-up procedure with monotonicity enforcement.
    """
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    q = [0.0] * m
    prev = 1.0
    # walk from largest p to smallest, enforcing monotone non-increasing q
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = pvals[i] * m / rank
        prev = min(prev, val)
        q[i] = min(prev, 1.0)
    return q


def cumulative_fdr() -> pd.DataFrame:
    """
    Recompute BH-FDR across the ENTIRE cumulative hypothesis_log and return the log
    with an added `fdr_q` column. This is the correction that step 6 must report
    against — every test ever recorded, not just the current run.
    """
    df = load_log().copy()
    if df.empty:
        df["fdr_q"] = []
        return df
    pvals = [float(p) for p in df["raw_p"]]
    df["fdr_q"] = benjamini_hochberg(pvals)
    return df
