"""
PostgreSQL store for candidate-list triage runs (Audit mode).

A triage run is a frozen, retrievable record of an adversarial audit of a
caller-supplied candidate list against one completed AgentBio case: the exact
drugs submitted, the per-drug verdicts, and the portfolio summary. It persists
independently of the candidate pool files so an audit trail survives later
pipeline runs.

Schema: triage_runs
  id             VARCHAR PRIMARY KEY
  disease_name   VARCHAR NOT NULL     — canonical disease of the audited case
  job_id         VARCHAR              — case job the pool came from
  drugs_json     TEXT NOT NULL        — JSON list of submitted drug names
  results_json   TEXT NOT NULL        — JSON per-drug verdict rows
  summary_json   TEXT NOT NULL        — JSON portfolio summary counts
  created_at     TIMESTAMP WITH TIME ZONE NOT NULL

CREATE TABLE IF NOT EXISTS runs once at API startup (same pattern as
saved_reports).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


_DATABASE_URL = os.environ.get("DATABASE_URL", "")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS triage_runs (
    id             VARCHAR PRIMARY KEY,
    disease_name   VARCHAR NOT NULL,
    job_id         VARCHAR,
    drugs_json     TEXT NOT NULL,
    results_json   TEXT NOT NULL,
    summary_json   TEXT NOT NULL,
    created_at     TIMESTAMP WITH TIME ZONE NOT NULL
);
"""


def init_db() -> None:
    """Create the triage_runs table if it does not yet exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


def _conn():
    return psycopg2.connect(_DATABASE_URL)


def save_triage_run(
    *,
    disease_name: str,
    job_id: str | None,
    drugs: list[str],
    results: list[dict],
    summary: dict,
) -> dict:
    """Insert a triage run and return the stored row."""
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO triage_runs
                  (id, disease_name, job_id, drugs_json, results_json,
                   summary_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (run_id, disease_name, job_id, json.dumps(drugs),
                 json.dumps(results), json.dumps(summary), now),
            )
        conn.commit()
    return get_triage_run(run_id)  # type: ignore[return-value]


def _row_to_dict(row: dict) -> dict:
    d = dict(row)
    for col, key in (("drugs_json", "drugs"), ("results_json", "results"),
                     ("summary_json", "summary")):
        try:
            d[key] = json.loads(d[col]) if d.get(col) else None
        except Exception:  # noqa: BLE001
            d[key] = None
        d.pop(col, None)
    return d


def get_triage_run(run_id: str) -> dict | None:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM triage_runs WHERE id = %s", (run_id,))
            row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_triage_runs(limit: int = 100) -> list[dict]:
    """Recent triage runs, summary fields only (no per-drug results)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, disease_name, job_id, summary_json, created_at
                FROM triage_runs ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["summary"] = json.loads(d["summary_json"]) if d.get("summary_json") else None
        except Exception:  # noqa: BLE001
            d["summary"] = None
        d.pop("summary_json", None)
        out.append(d)
    return out
