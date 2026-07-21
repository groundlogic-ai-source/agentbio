"""
PostgreSQL store for user-saved hypothesis reports (the "Saved Reports" tab).

A saved report is a frozen snapshot of a full auditable write-up the user chose
to keep: the Opus narrative plus the exact audit numbers (facts) at save time.
It is deliberately decoupled from the live registry so a saved report never
changes even if cumulative FDR later shifts.

Schema: saved_reports
  id                VARCHAR PRIMARY KEY
  hypothesis_id     VARCHAR NOT NULL
  hypothesis_text   TEXT
  report_markdown   TEXT NOT NULL
  facts_json        TEXT            — JSON snapshot of the audit numbers
  generated_at      TIMESTAMP       — when the report was generated
  saved_at          TIMESTAMP       — when the user saved it

CREATE TABLE IF NOT EXISTS runs once at API startup.
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
CREATE TABLE IF NOT EXISTS saved_reports (
    id                VARCHAR PRIMARY KEY,
    hypothesis_id     VARCHAR NOT NULL,
    hypothesis_text   TEXT,
    report_markdown   TEXT    NOT NULL,
    facts_json        TEXT,
    generated_at      TIMESTAMP WITH TIME ZONE,
    saved_at          TIMESTAMP WITH TIME ZONE NOT NULL
);
"""


def init_db() -> None:
    """Create the saved_reports table if it does not yet exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SQL)
        conn.commit()


def _conn():
    return psycopg2.connect(_DATABASE_URL)


def _row_to_dict(row: dict) -> dict:
    d = dict(row)
    if d.get("facts_json"):
        try:
            d["facts"] = json.loads(d["facts_json"])
        except Exception:  # noqa: BLE001
            d["facts"] = None
    else:
        d["facts"] = None
    d.pop("facts_json", None)
    return d


_IDEMPOTENCY_WINDOW_SECONDS = 60


def save_report(
    *,
    hypothesis_id: str,
    hypothesis_text: str | None,
    report_markdown: str,
    facts: dict | None,
    generated_at: str | None,
) -> dict:
    """Insert a new saved report snapshot and return the stored row.

    Idempotent within a 60-second window: if a report for the same
    hypothesis_id was saved in the last 60 seconds, return that existing
    row instead of inserting a duplicate.
    """
    now = datetime.now(timezone.utc)
    facts_json = json.dumps(facts) if facts is not None else None

    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT * FROM saved_reports
                WHERE hypothesis_id = %s
                  AND saved_at >= %s - interval '%s seconds'
                ORDER BY saved_at DESC
                LIMIT 1
                """,
                (hypothesis_id, now, _IDEMPOTENCY_WINDOW_SECONDS),
            )
            existing = cur.fetchone()
        if existing:
            return _row_to_dict(existing)

        report_id = str(uuid.uuid4())
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO saved_reports
                  (id, hypothesis_id, hypothesis_text, report_markdown,
                   facts_json, generated_at, saved_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (report_id, hypothesis_id, hypothesis_text, report_markdown,
                 facts_json, generated_at, now),
            )
        conn.commit()
    return get_report(report_id)  # type: ignore[return-value]


def get_report(report_id: str) -> dict | None:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM saved_reports WHERE id = %s", (report_id,))
            row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_reports(limit: int = 200) -> list[dict]:
    with _conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM saved_reports ORDER BY saved_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_report(report_id: str) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_reports WHERE id = %s", (report_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
