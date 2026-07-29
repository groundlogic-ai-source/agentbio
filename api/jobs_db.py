"""
Job tracking for the Silver Bullet FastAPI backend (Stage 4).

Storage is Replit-managed PostgreSQL (via DATABASE_URL), NOT a local SQLite file.
Cloud Run's per-instance disk is ephemeral, so any job history written to a local
file is silently reset on every redeploy / instance recycle / cold start.
PostgreSQL is durable and survives all of those, so both the `jobs` table and the
`explored_targets` table live there.

Schema ownership: the two tables are created in the *development* database via the
Replit database tooling and propagated to *production* by the Replit Publish diff
(which introspects both databases and applies the delta). This module therefore
intentionally does NOT run DDL at startup — production schema is not the
application's responsibility. It DOES perform a one-time, idempotent DATA seed of
historical jobs (see `_seed_if_empty`) so that a brand-new/empty database is
populated from the committed snapshot in `api/seed_jobs.json`.

This is still a SEPARATE store from the LangGraph checkpoints.db: that holds graph
execution state (managed by LangGraph's SqliteSaver); this holds *job metadata*
(status, progress, cost) that the API exposes. Mixing the two would couple our
schema to LangGraph internals, so they stay apart.
"""

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras

_DATABASE_URL = os.environ["DATABASE_URL"]

# Committed historical snapshot, imported into a fresh (empty) database exactly
# once. Lives next to this module so it is always bundled with the deploy image.
_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "seed_jobs.json")

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

# Serialize writes from the background graph thread and the request threads within
# a single process. Cross-instance safety (multiple Cloud Run instances) is
# provided by PostgreSQL constraints — see claim_next_unexplored / record_explored,
# which rely on the explored_targets primary key + ON CONFLICT for atomicity.
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


def _connect() -> "psycopg2.extensions.connection":
    return psycopg2.connect(_DATABASE_URL)


@contextmanager
def _conn(lock: bool = False) -> Iterator["psycopg2.extensions.connection"]:
    """Open a connection, commit on success, roll back on error, always close.

    A new connection per operation keeps background job threads independent (a
    psycopg2 connection is not safe to share across threads). Pass lock=True for
    write paths to serialize them within this process.
    """
    if lock:
        _LOCK.acquire()
    try:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    finally:
        # Release the lock even if _connect() itself raises, otherwise a single
        # transient connection failure would leak the lock and deadlock every
        # later write path in this process.
        if lock:
            _LOCK.release()


def init_db() -> None:
    """Prepare the job store for use. Safe to call repeatedly.

    Does NOT create tables: schema is owned by the dev database tooling and the
    Publish diff (see module docstring). This only performs the one-time seed of
    historical jobs into an empty database.
    """
    _seed_if_empty()


def _seed_if_empty() -> None:
    """Import the committed historical jobs/explored_targets into an EMPTY store.

    No-op if the jobs table already has any rows, so it never overwrites or
    resurrects data on an established database. Every insert uses
    ON CONFLICT DO NOTHING, so it is idempotent and safe even if two fresh
    instances start concurrently.
    """
    if not os.path.exists(_SEED_FILE):
        return
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM jobs")
        (count,) = cur.fetchone()
        if count:
            return
        with open(_SEED_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        for job in data.get("jobs", []):
            row = {col: job.get(col) for col in _COLUMNS}
            if row.get("total_cost_usd") is None:
                row["total_cost_usd"] = 0.0
            if row.get("archived") is None:
                row["archived"] = 0
            cur.execute(
                """
                INSERT INTO jobs (job_id, thread_id, disease_name, status,
                    current_stage, created_at, updated_at, error_message,
                    total_cost_usd, report_path, decision, review_notes,
                    repurposing_only, archived)
                VALUES (%(job_id)s, %(thread_id)s, %(disease_name)s, %(status)s,
                    %(current_stage)s, %(created_at)s, %(updated_at)s,
                    %(error_message)s, %(total_cost_usd)s, %(report_path)s,
                    %(decision)s, %(review_notes)s, %(repurposing_only)s,
                    %(archived)s)
                ON CONFLICT (job_id) DO NOTHING
                """,
                row,
            )
        for pair in data.get("explored_targets", []):
            cur.execute(
                """
                INSERT INTO explored_targets (disease_key, target_key,
                    disease_name, target_symbol, job_id, created_at)
                VALUES (%(disease_key)s, %(target_key)s, %(disease_name)s,
                    %(target_symbol)s, %(job_id)s, %(created_at)s)
                ON CONFLICT (disease_key, target_key) DO NOTHING
                """,
                pair,
            )


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def find_completed_job_by_disease(disease_name: str) -> Optional[dict[str, Any]]:
    """Return the most recently created completed or awaiting_review job for a
    disease, matched case-insensitively. Returns None if no match exists."""
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('completed', 'awaiting_review')
              AND LOWER(disease_name) = LOWER(%s)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (disease_name,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


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
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO jobs (job_id, thread_id, disease_name, status,
                              current_stage, created_at, updated_at,
                              error_message, total_cost_usd, report_path)
            VALUES (%s, %s, %s, 'queued', NULL, %s, %s, NULL, 0.0, NULL)
            """,
            (job_id, thread_id, disease_name, now, now),
        )
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
    # Column names come from the _COLUMNS allowlist above, never user input, so
    # interpolating them into the SET clause is safe. Values are parameterized.
    assignments = ", ".join(f"{col} = %s" for col in updatable)
    values = list(updatable.values()) + [job_id]

    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE jobs SET {assignments} WHERE job_id = %s", values)
    return get_job(job_id)


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def list_jobs(include_archived: bool = False) -> list[dict[str, Any]]:
    """All jobs, most recent first. Excludes archived rows by default."""
    with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if include_archived:
            cur.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, updated_at DESC"
            )
        else:
            cur.execute(
                "SELECT * FROM jobs WHERE archived = 0 "
                "ORDER BY created_at DESC, updated_at DESC"
            )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def archive_job(job_id: str) -> Optional[dict[str, Any]]:
    """
    Soft-archive a job (sets archived=1). The job record, its report, and any
    explored_targets rows are left completely intact — archiving never removes
    data and never prevents auto-explore from correctly skipping already-tried
    (disease, target) pairs.
    """
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET archived = 1, updated_at = %s WHERE job_id = %s",
            (_now(), job_id),
        )
    return get_job(job_id)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def record_explored(disease_name: str, target_symbol: str,
                    job_id: Optional[str] = None) -> None:
    """
    Mark a (disease, target) pair as explored. Idempotent: re-recording the same
    pair is a no-op (ON CONFLICT DO NOTHING on the normalized key).
    """
    disease_key = _norm(disease_name)
    target_key = _norm(target_symbol)
    if not disease_key or not target_key:
        return
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO explored_targets
                (disease_key, target_key, disease_name, target_symbol,
                 job_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (disease_key, target_key) DO NOTHING
            """,
            (disease_key, target_key, disease_name, target_symbol,
             job_id, _now()),
        )


def get_explored_pairs() -> set[tuple[str, str]]:
    """
    Every explored (disease, target) pair as a set of normalized
    (disease_key, target_key) tuples, for fast membership checks.
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT disease_key, target_key FROM explored_targets")
        rows = cur.fetchall()
    return {(r[0], r[1]) for r in rows}


def count_jobs_today() -> int:
    """
    Count jobs created in the current UTC calendar day.
    Used by the daily-cap guardrail (api/guardrails.py) to enforce DAILY_RUN_CAP.
    """
    today_prefix = time.strftime("%Y-%m-%d", time.gmtime())
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM jobs WHERE created_at LIKE %s",
            (today_prefix + "%",),
        )
        (count,) = cur.fetchone()
    return count


def reap_orphaned_running_jobs() -> int:
    """
    On server startup, mark any jobs still in 'running' status as 'error'.
    These are orphans from a previous process that was killed mid-run (e.g. a
    uvicorn restart). Their background threads no longer exist, so they will
    never self-update. Returns the number of jobs reaped.
    """
    msg = "Job killed: server restarted while this job was in progress."
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status='error', error_message=%s, updated_at=%s "
            "WHERE status='running'",
            (msg, _now()),
        )
        return cur.rowcount


def claim_next_unexplored(
    candidates: list[tuple[Optional[str], Optional[str]]],
    job_id: Optional[str] = None,
) -> Optional[tuple[str, str]]:
    """
    Atomically pick AND record the first (disease_name, target_symbol) in
    ranked `candidates` whose normalized pair is not yet explored.

    Race safety: the chosen pair is claimed with INSERT ... ON CONFLICT DO NOTHING
    RETURNING. Only the caller whose insert actually lands (RETURNING yields a row)
    treats the pair as claimed; a concurrent claimant that loses the insert sees no
    returned row and moves on to the next candidate. This closes the pick/record
    TOCTOU window both across threads (via _LOCK) AND across separate Cloud Run
    instances (via the explored_targets primary key). Returns the chosen
    (disease_name, target_symbol) as given, or None if every candidate is already
    explored (the caller decides how to fall back).
    """
    now = _now()
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT disease_key, target_key FROM explored_targets")
        explored = {(r[0], r[1]) for r in cur.fetchall()}
        for disease_name, target_symbol in candidates:
            dkey, tkey = _norm(disease_name), _norm(target_symbol)
            if not dkey or not tkey or (dkey, tkey) in explored:
                continue
            cur.execute(
                """
                INSERT INTO explored_targets
                    (disease_key, target_key, disease_name, target_symbol,
                     job_id, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (disease_key, target_key) DO NOTHING
                RETURNING disease_key
                """,
                (dkey, tkey, disease_name, target_symbol, job_id, now),
            )
            if cur.fetchone() is not None:
                return (disease_name, target_symbol)  # type: ignore[return-value]
            # Lost the race to another claimant; keep walking the ranked list.
    return None
