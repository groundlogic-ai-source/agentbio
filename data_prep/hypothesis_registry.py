"""
Persistent hypothesis registry for the bisociative discovery pipeline.

Storage is Replit-managed PostgreSQL (via DATABASE_URL), NOT local CSV files.
Cloud Run's per-instance disk is ephemeral, so any registry written to a local
file is silently reset on every redeploy / instance recycle / cold start — this
is exactly how discovery run "run-e5873b1c" was lost. PostgreSQL is durable and
survives all of those.

Two tables (both cross-referenced by shared test_id / hypothesis_id):

1. hypothesis_log  — the CUMULATIVE log of every statistical test ever run
   against this dataset, across ALL runs. Single source of truth for
   Benjamini-Hochberg FDR correction: FDR is always computed over the entire
   log, never just the current run's batch, so repeated future runs with new
   domains do not silently recreate the multiple-comparisons problem.

2. bisociation_history — the narrative record: every proposed domain (used or
   not, passed or not), its proposing LLM, the resulting hypothesis, its
   discovery/confirmation outcomes, and (going forward) the literal feature_spec
   proxy that was actually tested.

Schema ownership: both tables are created in the *development* database via the
Replit database tooling and propagated to *production* by the Replit Publish diff
(which introspects both databases and applies the delta). This module therefore
intentionally does NOT run DDL at startup — production schema is not the
application's responsibility. It DOES perform a one-time, idempotent DATA seed
(see `seed_if_empty`) from the committed snapshot in `api/seed_registry.json` so
that a brand-new/empty database is populated with the historical record.

IDs (assigned in run_discovery, prefixed with the run_id so they are
collision-free across concurrent runs without any global counter):
- hypothesis_id: per distinct hypothesis, e.g. run-0f0aea63-H01.
- test_id: per statistical test = (hypothesis x outcome_framing).
"""
from __future__ import annotations

import json
import os

import pandas as pd
import psycopg2
import psycopg2.extras

_DATABASE_URL = os.environ["DATABASE_URL"]

# Committed historical snapshot, imported into a fresh (empty) database exactly
# once. Lives under api/ so it is always bundled with the deploy image.
_SEED_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "api",
    "seed_registry.json",
)

LOG_COLS = [
    "test_id",
    "hypothesis_id",
    "run_id",
    "run_timestamp",
    "hypothesis_text",
    "test_type",
    "outcome_framing",
    "raw_p",
    # ── methodology locked before any result is computed ────────────────────
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
    "archived",                 # bool — UI-only flag; never affects FDR log
    "feature_spec",             # JSON: the literal computable proxy that was tested
]

# Boolean columns in bisociation_history (nullable where the step has not run).
_HIST_BOOL_COLS = ("discovery_pass", "confirmation_pass", "archived")

# Methodology constants — recorded per-log-entry so every row is self-describing.
SIGNIFICANCE_THRESHOLD: float = 0.05
CORRECTION_METHOD: str = "benjamini_hochberg"


# ── Connection helper ────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(_DATABASE_URL)


# ── Seeding (one-time, idempotent) ────────────────────────────────────────────

def seed_if_empty() -> None:
    """Import the committed historical registry into an EMPTY store.

    No-op if bisociation_history already has any rows, so it never overwrites or
    resurrects data on an established database. Every insert uses
    ON CONFLICT DO NOTHING, so it is idempotent and safe even if two fresh
    instances start concurrently.
    """
    if not os.path.exists(_SEED_FILE):
        return
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM bisociation_history")
            (count,) = cur.fetchone()
            if count:
                return
            with open(_SEED_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            for row in data.get("hypothesis_log", []):
                _insert_log_row(cur, {c: row.get(c) for c in LOG_COLS})
            for row in data.get("bisociation_history", []):
                _insert_history_row(cur, {c: row.get(c) for c in HIST_COLS})
        conn.commit()


# Back-compat shim: callers still invoke migrate_registries() at startup. The old
# CSV-column back-fill is obsolete (schema is owned by the DB), so this now just
# ensures the historical snapshot is seeded into an empty database.
def migrate_registries() -> None:
    seed_if_empty()


# ── Row coercion helpers ──────────────────────────────────────────────────────

def _to_bool(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "t", "yes"):
            return True
        if s in ("false", "0", "f", "no", ""):
            return False if s != "" else None
    return bool(v)


def _to_float(v):
    if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _clean_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v


def _insert_log_row(cur, row: dict) -> None:
    cur.execute(
        """
        INSERT INTO hypothesis_log
            (test_id, hypothesis_id, run_id, run_timestamp, hypothesis_text,
             test_type, outcome_framing, raw_p, significance_threshold,
             correction_method, locked_at)
        VALUES (%(test_id)s, %(hypothesis_id)s, %(run_id)s, %(run_timestamp)s,
                %(hypothesis_text)s, %(test_type)s, %(outcome_framing)s,
                %(raw_p)s, %(significance_threshold)s, %(correction_method)s,
                %(locked_at)s)
        ON CONFLICT (test_id) DO NOTHING
        """,
        {
            "test_id": row.get("test_id"),
            "hypothesis_id": _clean_str(row.get("hypothesis_id")),
            "run_id": _clean_str(row.get("run_id")),
            "run_timestamp": _clean_str(row.get("run_timestamp")),
            "hypothesis_text": _clean_str(row.get("hypothesis_text")),
            "test_type": _clean_str(row.get("test_type")),
            "outcome_framing": _clean_str(row.get("outcome_framing")),
            "raw_p": _to_float(row.get("raw_p")),
            "significance_threshold": _to_float(row.get("significance_threshold")),
            "correction_method": _clean_str(row.get("correction_method")),
            "locked_at": _clean_str(row.get("locked_at")),
        },
    )


def _insert_history_row(cur, row: dict) -> None:
    cur.execute(
        """
        INSERT INTO bisociation_history
            (test_id, hypothesis_id, run_id, session_timestamp,
             domain_description, proposing_llm, resulting_hypothesis_text,
             discovery_test_type, outcome_framing, discovery_raw_p,
             discovery_fdr_p, discovery_pass, confirmation_pass,
             confirmation_raw_p, confound_check_summary, outcome_note,
             archived, feature_spec)
        VALUES (%(test_id)s, %(hypothesis_id)s, %(run_id)s, %(session_timestamp)s,
                %(domain_description)s, %(proposing_llm)s,
                %(resulting_hypothesis_text)s, %(discovery_test_type)s,
                %(outcome_framing)s, %(discovery_raw_p)s, %(discovery_fdr_p)s,
                %(discovery_pass)s, %(confirmation_pass)s, %(confirmation_raw_p)s,
                %(confound_check_summary)s, %(outcome_note)s, %(archived)s,
                %(feature_spec)s)
        ON CONFLICT (hypothesis_id, (COALESCE(outcome_framing, ''))) DO NOTHING
        """,
        {
            "test_id": row.get("test_id"),
            "hypothesis_id": _clean_str(row.get("hypothesis_id")),
            "run_id": _clean_str(row.get("run_id")),
            "session_timestamp": _clean_str(row.get("session_timestamp")),
            "domain_description": _clean_str(row.get("domain_description")),
            "proposing_llm": _clean_str(row.get("proposing_llm")),
            "resulting_hypothesis_text": _clean_str(row.get("resulting_hypothesis_text")),
            "discovery_test_type": _clean_str(row.get("discovery_test_type")),
            "outcome_framing": _clean_str(row.get("outcome_framing")),
            "discovery_raw_p": _to_float(row.get("discovery_raw_p")),
            "discovery_fdr_p": _to_float(row.get("discovery_fdr_p")),
            "discovery_pass": _to_bool(row.get("discovery_pass")),
            "confirmation_pass": _to_bool(row.get("confirmation_pass")),
            "confirmation_raw_p": _to_float(row.get("confirmation_raw_p")),
            "confound_check_summary": _clean_str(row.get("confound_check_summary")),
            "outcome_note": _clean_str(row.get("outcome_note")),
            "archived": _to_bool(row.get("archived")) or False,
            "feature_spec": _clean_str(row.get("feature_spec")),
        },
    )


# ── Loads (return DataFrames identical in shape to the old CSV loads) ──────────

def _fetch_df(query: str, cols: list[str]) -> pd.DataFrame:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query)
            rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols]


def load_log() -> pd.DataFrame:
    # Ordered by locked_at then test_id for a stable, chronological view.
    return _fetch_df(
        "SELECT * FROM hypothesis_log ORDER BY locked_at NULLS FIRST, test_id",
        LOG_COLS,
    )


def load_history() -> pd.DataFrame:
    # feature_spec is intentionally excluded from the returned columns to keep
    # this identical to the historical shape consumed by the API and report;
    # use get_feature_spec() / load_history_full() to read the proxy.
    return _fetch_df(
        "SELECT * FROM bisociation_history "
        "ORDER BY session_timestamp NULLS FIRST, test_id",
        [c for c in HIST_COLS if c != "feature_spec"],
    )


def load_history_full() -> pd.DataFrame:
    """Like load_history() but includes the feature_spec column."""
    return _fetch_df(
        "SELECT * FROM bisociation_history "
        "ORDER BY session_timestamp NULLS FIRST, test_id",
        HIST_COLS,
    )


def get_feature_spec(test_id: str):
    """Return the persisted feature_spec (parsed JSON) for a test_id, or None."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT feature_spec FROM bisociation_history WHERE test_id = %s",
                (test_id,),
            )
            row = cur.fetchone()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


# ── Writes ────────────────────────────────────────────────────────────────────

def update_history_row(test_id: str, **fields) -> None:
    """
    Update specific fields of an existing bisociation_history row in-place.
    Allowed fields: confirmation_pass, confirmation_raw_p, confound_check_summary.
    Used by the confirmation and confound steps to fill in results after discovery.
    """
    allowed = {"confirmation_pass", "confirmation_raw_p", "confound_check_summary"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"update_history_row: unknown fields {sorted(bad)}")
    if not fields:
        return
    coerced = {}
    for k, v in fields.items():
        if k == "confirmation_pass":
            coerced[k] = _to_bool(v)
        elif k == "confirmation_raw_p":
            coerced[k] = _to_float(v)
        else:
            coerced[k] = _clean_str(v)
    sets = ", ".join(f"{k} = %s" for k in coerced)
    vals = list(coerced.values()) + [test_id]
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE bisociation_history SET {sets} WHERE test_id = %s", vals
            )
        conn.commit()


def set_hypothesis_archived(hypothesis_id: str, archived: bool) -> bool:
    """
    Set the archived flag on all history rows matching hypothesis_id.
    Archiving is UI-only — it never touches hypothesis_log or affects FDR.
    Returns True if at least one row was updated, False if hypothesis_id not found.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bisociation_history SET archived = %s "
                "WHERE hypothesis_id = %s",
                (bool(archived), hypothesis_id),
            )
            updated = cur.rowcount
        conn.commit()
    return updated > 0


def set_feature_spec(test_id: str, feature_spec) -> None:
    """Persist the literal computable proxy for a test row (JSON-encoded)."""
    payload = None
    if feature_spec is not None:
        payload = (
            feature_spec if isinstance(feature_spec, str)
            else json.dumps(feature_spec)
        )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bisociation_history SET feature_spec = %s WHERE test_id = %s",
                (payload, test_id),
            )
        conn.commit()


def append_log_rows(rows: list[dict]) -> pd.DataFrame:
    with _conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                _insert_log_row(cur, row)
        conn.commit()
    return load_log()


def append_history_rows(rows: list[dict]) -> pd.DataFrame:
    with _conn() as conn:
        with conn.cursor() as cur:
            for row in rows:
                _insert_history_row(cur, row)
        conn.commit()
    return load_history_full()


# ── FDR ───────────────────────────────────────────────────────────────────────

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
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        val = pvals[i] * m / rank
        prev = min(prev, val)
        q[i] = min(prev, 1.0)
    return q


def cumulative_fdr() -> pd.DataFrame:
    """
    Recompute BH-FDR across the ENTIRE cumulative hypothesis_log and return the
    log with an added `fdr_q` column. This is the correction the pipeline must
    report against — every test ever recorded, not just the current run.
    """
    df = load_log().copy()
    if df.empty:
        df["fdr_q"] = []
        return df
    pvals = [float(p) for p in df["raw_p"]]
    df["fdr_q"] = benjamini_hochberg(pvals)
    return df
