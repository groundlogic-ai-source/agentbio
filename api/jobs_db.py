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
    "decision",
    "review_notes",
    "repurposing_only",
    "archived",
)

# Columns added after the original schema shipped; applied via ALTER TABLE on
# existing jobs.db files (SQLite has no "ADD COLUMN IF NOT EXISTS").
_MIGRATIONS = (
    ("decision", "decision TEXT"),
    ("review_notes", "review_notes TEXT"),
    ("repurposing_only", "repurposing_only INTEGER"),
    ("archived", "archived INTEGER NOT NULL DEFAULT 0"),
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
                report_path     TEXT,
                decision        TEXT,
                review_notes    TEXT,
                repurposing_only INTEGER
            )
            """
        )
        existing = {row["name"]
                    for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        for col, ddl in _MIGRATIONS:
            if col not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {ddl}")

        # explored_targets: every (disease, target) pair ever selected by a run
        # (any status). Blank-disease auto-pick consults this so repeated runs
        # walk DOWN the ranked list instead of re-selecting the same #1 pair.
        # Keys are stored normalized (lowercased/stripped) for stable matching.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS explored_targets (
                disease_key   TEXT NOT NULL,
                target_key    TEXT NOT NULL,
                disease_name  TEXT,
                target_symbol TEXT,
                job_id        TEXT,
                created_at    TEXT NOT NULL,
                PRIMARY KEY (disease_key, target_key)
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
            VALUES (?, ?, ?, 'queued', NULL, ?, ?, NULL, 0.0, NULL)
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


def list_jobs(include_archived: bool = False) -> list[dict[str, Any]]:
    """All jobs, most recent first. Excludes archived rows by default."""
    with _connect() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE archived = 0 "
                "ORDER BY created_at DESC, updated_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def archive_job(job_id: str) -> Optional[dict[str, Any]]:
    """
    Soft-archive a job (sets archived=1). The job record, its report, and any
    explored_targets rows are left completely intact — archiving never removes
    data and never prevents auto-explore from correctly skipping already-tried
    (disease, target) pairs.
    """
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE jobs SET archived = 1, updated_at = ? WHERE job_id = ?",
            (_now(), job_id),
        )
        conn.commit()
    return get_job(job_id)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def record_explored(disease_name: str, target_symbol: str,
                    job_id: Optional[str] = None) -> None:
    """
    Mark a (disease, target) pair as explored. Idempotent: re-recording the same
    pair is a no-op (INSERT OR IGNORE on the normalized key).
    """
    disease_key = _norm(disease_name)
    target_key = _norm(target_symbol)
    if not disease_key or not target_key:
        return
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO explored_targets
                (disease_key, target_key, disease_name, target_symbol,
                 job_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (disease_key, target_key, disease_name, target_symbol, job_id, _now()),
        )
        conn.commit()


def get_explored_pairs() -> set[tuple[str, str]]:
    """
    Every explored (disease, target) pair as a set of normalized
    (disease_key, target_key) tuples, for fast membership checks.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT disease_key, target_key FROM explored_targets"
        ).fetchall()
    return {(r["disease_key"], r["target_key"]) for r in rows}


def reap_orphaned_running_jobs() -> int:
    """
    On server startup, mark any jobs still in 'running' status as 'error'.
    These are orphans from a previous process that was killed mid-run (e.g. a
    uvicorn restart). Their background threads no longer exist, so they will
    never self-update. Returns the number of jobs reaped.
    """
    msg = "Job killed: server restarted while this job was in progress."
    with _LOCK, _connect() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status='error', error_message=?, updated_at=? "
            "WHERE status='running'",
            (msg, _now()),
        )
        conn.commit()
        return cursor.rowcount


def claim_next_unexplored(
    candidates: list[tuple[Optional[str], Optional[str]]],
    job_id: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """
    Atomically pick AND record the first (disease_name, target_symbol) in
    ranked `candidates` whose normalized pair is not yet explored.

    The read of explored pairs and the insert of the chosen pair happen under one
    lock + one connection, so two concurrent blank runs can never claim the same
    pair (closes the pick/record TOCTOU window). Returns the chosen
    (disease_name, target_symbol) as given, or None if every candidate is already
    explored (the caller decides how to fall back).
    """
    now = _now()
    with _LOCK, _connect() as conn:
        explored = {
            (r["disease_key"], r["target_key"])
            for r in conn.execute(
                "SELECT disease_key, target_key FROM explored_targets"
            ).fetchall()
        }
        for disease_name, target_symbol in candidates:
            dkey, tkey = _norm(disease_name), _norm(target_symbol)
            if not dkey or not tkey or (dkey, tkey) in explored:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO explored_targets
                    (disease_key, target_key, disease_name, target_symbol,
                     job_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (dkey, tkey, disease_name, target_symbol, job_id, now),
            )
            conn.commit()
            return (disease_name, target_symbol)  # type: ignore[return-value]
    return None
