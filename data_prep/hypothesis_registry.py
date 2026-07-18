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
    # ── Feature 4: methodology locked before any result is computed ──────────
    "significance_threshold",  # always 0.05; recorded so future changes are auditable
    "correction_method",       # always "benjamini_hochberg"
    "locked_at",               # ISO timestamp set BEFORE the test runs
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
    "confirmation_raw_p",       # raw p on the holdout half (empty if not confirmed)
    "confound_check_summary",   # JSON: Opus-proposed confounders + adjusted OR results
    "outcome_note",
]

# Methodology constants — recorded per-log-entry so every row is self-describing.
# If either ever changes, ALL downstream re-tests must create NEW rows, never
# overwrite existing ones (enforced by the append-only design of append_log_rows).
SIGNIFICANCE_THRESHOLD: float = 0.05
CORRECTION_METHOD: str = "benjamini_hochberg"


def _load(path: str, cols: list[str]) -> pd.DataFrame:
    if os.path.exists(path):
        df = pd.read_csv(path)
        for c in cols:
            if c not in df.columns:
                df[c] = pd.NA
        return df[cols]
    return pd.DataFrame(columns=cols)


def load_log() -> pd.DataFrame:
    # Lock the read so we never observe a half-written CSV during a concurrent
    # append/rewrite. Uses a fresh lock fd; must NOT be called from inside an
    # already-held _registry_lock() (flock is not reentrant across fds → deadlock).
    with _registry_lock():
        return _load(LOG_CSV, LOG_COLS)


def load_history() -> pd.DataFrame:
    with _registry_lock():
        return _load(HIST_CSV, HIST_COLS)


def migrate_registries() -> None:
    """
    One-time, idempotent migration: back-fill new methodology columns that were
    added after the first run so existing CSV rows remain fully self-describing.
    Safe to call on every startup; exits immediately if nothing needs updating.
    Uses the same fcntl lock as all other writes.
    """
    with _registry_lock():
        # -- hypothesis_log.csv ------------------------------------------------
        if os.path.exists(LOG_CSV):
            df = pd.read_csv(LOG_CSV)
            dirty = False
            if "significance_threshold" not in df.columns:
                df["significance_threshold"] = SIGNIFICANCE_THRESHOLD
                dirty = True
            if "correction_method" not in df.columns:
                df["correction_method"] = CORRECTION_METHOD
                dirty = True
            if "locked_at" not in df.columns:
                # Best approximation: use run_timestamp if present, else empty
                if "run_timestamp" in df.columns:
                    df["locked_at"] = df["run_timestamp"]
                else:
                    df["locked_at"] = ""
                dirty = True
            if dirty:
                # Reorder to canonical column order (adds any still-missing cols)
                for c in LOG_COLS:
                    if c not in df.columns:
                        df[c] = pd.NA
                df[LOG_COLS].to_csv(LOG_CSV, index=False)

        # -- bisociation_history.csv -------------------------------------------
        if os.path.exists(HIST_CSV):
            df = pd.read_csv(HIST_CSV)
            dirty = False
            for new_col in ("confirmation_raw_p", "confound_check_summary"):
                if new_col not in df.columns:
                    df[new_col] = ""
                    dirty = True
            if dirty:
                for c in HIST_COLS:
                    if c not in df.columns:
                        df[c] = pd.NA
                df[HIST_COLS].to_csv(HIST_CSV, index=False)


def update_history_row(test_id: str, **fields) -> None:
    """
    Update specific fields of an existing bisociation_history row in-place.
    Allowed fields: confirmation_pass, confirmation_raw_p, confound_check_summary.
    Used by the confirmation and confound steps to fill in results after discovery.
    Protected by the same registry lock as all other writes.
    """
    allowed = {"confirmation_pass", "confirmation_raw_p", "confound_check_summary"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"update_history_row: unknown fields {sorted(bad)}")
    with _registry_lock():
        if not os.path.exists(HIST_CSV):
            return
        df = pd.read_csv(HIST_CSV)
        mask = df["test_id"] == test_id
        for col, val in fields.items():
            if col not in df.columns:
                df[col] = ""
            df.loc[mask, col] = val
        df.to_csv(HIST_CSV, index=False)


def append_log_rows(rows: list[dict]) -> pd.DataFrame:
    with _registry_lock():
        df = _load(LOG_CSV, LOG_COLS)  # lock-free read; we already hold the lock
        add = pd.DataFrame(rows, columns=LOG_COLS)
        out = pd.concat([df, add], ignore_index=True)
        out.to_csv(LOG_CSV, index=False)
        return out


def append_history_rows(rows: list[dict]) -> pd.DataFrame:
    with _registry_lock():
        df = _load(HIST_CSV, HIST_COLS)  # lock-free read; we already hold the lock
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
    with _registry_lock():
        df = _load(LOG_CSV, LOG_COLS).copy()  # lock-free read under the held lock
    if df.empty:
        df["fdr_q"] = []
        return df
    pvals = [float(p) for p in df["raw_p"]]
    df["fdr_q"] = benjamini_hochberg(pvals)
    return df
