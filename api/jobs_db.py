"""
Job tracking for the Silver Bullet FastAPI backend (Stage 4).

This is deliberately a SEPARATE SQLite file from the LangGraph checkpoints.db.
checkpoints.db holds graph execution state (managed by LangGraph's SqliteSaver);
this database holds *job metadata* (status, progress, cost) that the API exposes.
Mixing the two would couple our schema to LangGraph internals, so they stay apart.
"""

import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

# jobs.db lives next to checkpoints.db at the repo root, but is its own file.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS_DB = os.path.join(_REPO_ROOT, "jobs.db")

VALID_STATUSES = (
    "queued",
    "running",
    "awaiting_review",
    "completed",
    "error",
)
VALID_STAGES = (
    "target_selection",
    "biologist",
    "chemist",
    "reviewer",
    "structure_validation",
    "writer",
    "awaiting_review",
    "done",
)

# SQLite allows one writer at a time; serialize writes from the background graph
# thread and the request threads to avoid "database is locked" on the hobby box.
_LOCK = threading.Lock()

_COLUMNS = (
    "job_id",
    "thread_id",
    "disease_name",
    "status",
    "current_stage",
    "created_at",
    "updated_at",
    "error_message",
    "total_cost_usd",
    "report_path",
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(JOBS_DB, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the jobs table if it does not exist. Safe to call repeatedly."""
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id          TEXT PRIMARY KEY,
                thread_id       TEXT NOT NULL,
                disease_name    TEXT,
                status          TEXT NOT NULL,
                current_stage   TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                error_message   TEXT,
                total_cost_usd  REAL NOT NULL DEFAULT 0.0,
                report_path     TEXT
            )
            """
        )
        conn.commit()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def create_job(disease_name: Optional[str] = None,
               thread_id: Optional[str] = None) -> dict[str, Any]:
    """
    Insert a new job in the 'queued' state and return its full record.

    job_id is a fresh UUID; thread_id (the LangGraph checkpoint key) defaults to
    'job-<job_id>' so a job maps 1:1 to its graph thread.
    """
    job_id = uuid.uuid4().hex
    thread_id = thread_id or f"job-{job_id}"
    now = _now()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (job_id, thread_id, disease_name, status,
                              current_stage, created_at, updated_at,
                              error_message, total_cost_usd, report_path)
            VALUES (?, ?, ?, 'queued', 'target_selection', ?, ?, NULL, 0.0, NULL)
            """,
            (job_id, thread_id, disease_name, now, now),
        )
        conn.commit()
    job = get_job(job_id)
    assert job is not None
    return job


def update_job_status(job_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    """
    Update arbitrary columns on a job (always bumps updated_at) and return the
    refreshed record. Unknown columns are rejected to catch typos early.
    """
    updatable = {k: v for k, v in fields.items()
                 if k in _COLUMNS and k != "job_id"}
    unknown = set(fields) - set(updatable) - {"job_id"}
    if unknown:
        raise ValueError(f"unknown job columns: {sorted(unknown)}")

    updatable["updated_at"] = _now()
    assignments = ", ".join(f"{col} = ?" for col in updatable)
    values = list(updatable.values()) + [job_id]

    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE job_id = ?", values)
        conn.commit()
    return get_job(job_id)


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs() -> list[dict[str, Any]]:
    """All jobs, most recent first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
