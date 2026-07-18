"""
Lightweight PostgreSQL store for user-submitted research hypothesis jobs (Feature 3).

Schema: research_jobs
  job_id            VARCHAR PRIMARY KEY
  hypothesis_text   TEXT NOT NULL
  status            VARCHAR  — pending | running | completed | error
  locked_at         TIMESTAMP   — methodology commitment time (before any result)
  significance_threshold  FLOAT
  correction_method VARCHAR
  result_json       TEXT        — JSON blob with log+history rows on success
  error_message     TEXT
  created_at        TIMESTAMP
  updated_at        TIMESTAMP

CREATE TABLE IF NOT EXISTS is run once at API startup.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


_DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── DDL ────────────────────────────────────────────────────────────────────────

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS research_jobs (
    job_id                VARCHAR PRIMARY KEY,
    hypothesis_text       TEXT    NOT NULL,
    status                VARCHAR NOT NULL DEFAULT 'pending',
    locked_at             TIMESTAMP WITH TIME ZONE,
    significance_threshold FLOAT,
    correction_method     VARCHAR,
    result_json           TEXT,
    error_message         TEXT,
    created_at            TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at            TIMESTAMP WITH TIME ZONE NOT NULL
);
"""


def init_db() -> None:
    """Create the research_jobs table if it does not yet exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


# ── Connection helper ──────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(_DATABASE_URL)


# ── CRUD ───────────────────────────────────────────────────────────────────────

def create_job(hypothesis_text: str) -> str:
    """
    Insert a new research job in 'pending' state.
    locked_at is set immediately — before the hypothesis is parsed or tested —
    to satisfy the methodology-locking guarantee (Feature 4).
    Returns the new job_id.
    """
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO research_jobs
                  (job_id, hypothesis_text, status, locked_at,
                   significance_threshold, correction_method,
                   created_at, updated_at)
                VALUES (%s, %s, 'pending', %s, %s, %s, %s, %s)
                """,
                (job_id, hypothesis_text, now, 0.05, "benjamini_hochberg", now, now),
            )
        conn.commit()
    return job_id


def update_job(job_id: str, **fields) -> None:
    """
    Update mutable fields on a research job.
    Allowed keys: status, result_json, error_message.
    updated_at is always refreshed.
    """
    allowed = {"status", "result_json", "error_message"}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"update_job: unknown fields {sorted(bad)}")

    now = datetime.now(timezone.utc)
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [now, job_id]
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE research_jobs SET {sets}, updated_at = %s WHERE job_id = %s",
                vals,
            )
        conn.commit()


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM research_jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    # Deserialise result_json if present
    if d.get("result_json"):
        try:
            d["result"] = json.loads(d["result_json"])
        except Exception:  # noqa: BLE001
            d["result"] = None
    else:
        d["result"] = None
    return d


def list_jobs(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM research_jobs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [dict(r) for r in rows]
