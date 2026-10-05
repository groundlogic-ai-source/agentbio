"""
Job tracking for the AgentBio FastAPI backend (Stage 4).

Storage is PostgreSQL (via DATABASE_URL), NOT a local SQLite file.
Cloud Run's per-instance disk is ephemeral, so any job history written to a local
file is silently reset on every redeploy / instance recycle / cold start.
PostgreSQL is durable and survives all of those, so both the `jobs` table and the
`explored_targets` table live there.

Schema ownership: this module intentionally does NOT run DDL at startup. The two
tables were originally created by external database tooling that introspected dev
and production and applied the delta, so the application never owned the schema.
That tooling is gone; `db/schema/001_core_tables.sql` and
`api/migrations/20260820_job_artifacts.sql` reconstruct the DDL for a fresh
deployment. Startup DDL is still deliberately absent — production schema is not the
application's responsibility. It DOES perform a one-time, idempotent DATA seed of
historical jobs (see `_seed_if_empty`) so that a brand-new/empty database is
populated from the committed snapshot in `api/seed_jobs.json`.

This is still a SEPARATE store from the LangGraph checkpoints.db: that holds graph
execution state (managed by LangGraph's SqliteSaver); this holds *job metadata*
(status, progress, cost) that the API exposes. Mixing the two would couple our
schema to LangGraph internals, so they stay apart.
"""

import json
import hashlib
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras

from api.policy_contracts import canonical_json_bytes

try:
    _DATABASE_URL = os.environ["DATABASE_URL"]
except KeyError:  # a bare KeyError at import time says nothing useful
    raise RuntimeError(
        "DATABASE_URL is not set. Stage 4 (the FastAPI service) stores job "
        "state in PostgreSQL; Stages 1-3 do not need it and run without it. "
        "Set it to a libpq connection string, e.g. "
        "postgresql://user:pass@host:5432/agentbio, and create the schema "
        "with db/schema/001_core_tables.sql plus "
        "api/migrations/20260820_job_artifacts.sql."
    ) from None

# Committed historical snapshot, imported into a fresh (empty) database exactly
# once. Lives next to this module so it is always bundled with the deploy image.
_SEED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "seed_jobs.json")

VALID_STATUSES = (
    "queued",
    "running",
    "awaiting_review",
    "reviewing",
    "completed",
    "no_eligible_candidate",
    "source_unavailable",
    "degraded_unscorable",
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
    "decision_contract_version",
    "report_contract_version",
    "report_sha256",
    "candidate_snapshot_sha256",
    "reviewer_input_fingerprint",
)

# ``job_artifacts`` is deliberately schema-managed with the other durable job
# tables.  Keep this DDL here (rather than executing it on application startup)
# so the development database migration is reviewable and Publish can apply the
# same delta to production.
#
# Development DDL:
# CREATE TABLE job_artifacts (
#   job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
#   kind TEXT NOT NULL,
#   artifact_id TEXT NOT NULL,
#   content_sha256 TEXT NOT NULL,
#   filename TEXT NOT NULL,
#   content_type TEXT NOT NULL,
#   size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
#   payload BYTEA NOT NULL,
#   report_sha256 TEXT NOT NULL,
#   candidate_snapshot_sha256 TEXT NOT NULL,
#   created_at TEXT NOT NULL,
#   PRIMARY KEY (job_id, kind, artifact_id),
#   CHECK (artifact_id ~ '^[a-f0-9]{64}$'),
#   CHECK (content_sha256 ~ '^[a-f0-9]{64}$')
# );
# CREATE UNIQUE INDEX job_artifacts_singleton_kind_uq
#   ON job_artifacts (job_id, kind)
#   WHERE kind IN ('report_md', 'report_pdf', 'evidence_zip');
JOB_ARTIFACTS_DEVELOPMENT_DDL = """\
CREATE TABLE job_artifacts (
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
  kind TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
  payload BYTEA NOT NULL,
  report_sha256 TEXT NOT NULL,
  candidate_snapshot_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, kind, artifact_id),
  CHECK (artifact_id ~ '^[a-f0-9]{64}$'),
  CHECK (content_sha256 ~ '^[a-f0-9]{64}$')
);
CREATE UNIQUE INDEX job_artifacts_singleton_kind_uq
  ON job_artifacts (job_id, kind)
  WHERE kind IN ('report_md', 'report_pdf', 'evidence_zip');
CREATE FUNCTION job_artifacts_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'job_artifacts rows are immutable'; END; $$;
CREATE TRIGGER job_artifacts_no_update BEFORE UPDATE ON job_artifacts
  FOR EACH ROW EXECUTE FUNCTION job_artifacts_immutable();
CREATE TRIGGER job_artifacts_no_delete BEFORE DELETE ON job_artifacts
  FOR EACH ROW EXECUTE FUNCTION job_artifacts_immutable();
"""

ARTIFACT_KINDS = frozenset(("report_md", "report_pdf", "evidence_zip", "cif"))
_SINGLETON_ARTIFACT_KINDS = frozenset(("report_md", "report_pdf", "evidence_zip"))
# These are deliberately below typical reverse-proxy limits.  A dossier must
# not turn the database into an unbounded general-purpose file store.
_ARTIFACT_MAX_BYTES = {
    "report_md": 10 * 1024 * 1024,
    "report_pdf": 25 * 1024 * 1024,
    "evidence_zip": 100 * 1024 * 1024,
    "cif": 10 * 1024 * 1024,
}


def finalize_job_artifact_bundle(
    job_id: str, *, artifacts: list[dict[str, Any]],
    report_path: str, decision_contract_version: str,
    report_contract_version: str, report_sha256: str,
    candidate_snapshot_sha256: str, reviewer_input_fingerprint: str,
) -> dict[str, Any]:
    """Atomically establish a complete immutable dossier bundle.

    This is the only writer used by finalization.  It intentionally does not
    call ``save_job_artifact``: there is no committed state in which a current
    report can observe just one member of a bundle.
    """
    required = {"report_md", "report_pdf", "evidence_zip"}
    kinds = [str(a.get("kind")) for a in artifacts]
    if not required.issubset(kinds) or len(kinds) != len(set(
            (a.get("kind"), a.get("artifact_id")) for a in artifacts)):
        raise ValueError("incomplete or duplicate immutable artifact bundle")
    normalized: list[dict[str, Any]] = []
    for item in artifacts:
        kind, payload = item.get("kind"), item.get("payload")
        if kind not in ARTIFACT_KINDS or not isinstance(payload, bytes):
            raise ValueError("invalid artifact bundle item")
        digest = hashlib.sha256(payload).hexdigest()
        if item.get("artifact_id", digest) != digest or len(payload) > _ARTIFACT_MAX_BYTES[kind]:
            raise ValueError("invalid immutable artifact bytes")
        normalized.append({**item, "artifact_id": digest, "content_sha256": digest,
                           "size_bytes": len(payload)})
    report = next(a for a in normalized if a["kind"] == "report_md")
    if report["content_sha256"] != report_sha256:
        raise ValueError("report markdown hash does not match metadata")
    with _conn(lock=True) as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,))
        job = cur.fetchone()
        if not job:
            raise ValueError(f"job not found: {job_id}")
        cur.execute("SELECT payload_json FROM job_candidate_snapshots WHERE job_id=%s FOR UPDATE",
                    (job_id,))
        snap = cur.fetchone()
        if not snap:
            raise ValueError("durable candidate snapshot missing at finalization")
        payload = snap["payload_json"] if isinstance(snap, dict) else snap[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != candidate_snapshot_sha256:
            raise ValueError("candidate snapshot changed during report finalization")
        cur.execute("SELECT * FROM job_artifacts WHERE job_id=%s FOR UPDATE", (job_id,))
        existing = [dict(row) for row in cur.fetchall()]
        if existing:
            # A retry is valid only for the exact full bundle and exact job
            # provenance.  A partial set cannot be repaired in place.
            if (len(existing) != len(normalized)
                    or { (r["kind"], r["artifact_id"]) for r in existing }
                       != { (r["kind"], r["artifact_id"]) for r in normalized }
                    or any(bytes(r["payload"]) != next(a["payload"] for a in normalized
                                                        if a["kind"] == r["kind"]
                                                        and a["artifact_id"] == r["artifact_id"])
                           for r in existing)
                    or job["report_sha256"] != report_sha256
                    or job["candidate_snapshot_sha256"] != candidate_snapshot_sha256
                    or job.get("report_path") != report_path
                    or job.get("decision_contract_version") != decision_contract_version
                    or job.get("report_contract_version") != report_contract_version
                    or job.get("reviewer_input_fingerprint")
                    != reviewer_input_fingerprint):
                raise ValueError("partial or different immutable artifact bundle exists")
            return dict(job)
        for item in normalized:
            cur.execute("""INSERT INTO job_artifacts
                (job_id,kind,artifact_id,content_sha256,filename,content_type,size_bytes,
                 payload,report_sha256,candidate_snapshot_sha256,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (job_id, item["kind"], item["artifact_id"], item["content_sha256"],
                 item["filename"], item["content_type"], item["size_bytes"],
                 psycopg2.Binary(item["payload"]), report_sha256,
                 candidate_snapshot_sha256, _now()))
        cur.execute("""UPDATE jobs SET report_path=%s, decision_contract_version=%s,
            report_contract_version=%s, report_sha256=%s,
            candidate_snapshot_sha256=%s, reviewer_input_fingerprint=%s,
            updated_at=%s WHERE job_id=%s RETURNING *""",
            (report_path, decision_contract_version, report_contract_version,
             report_sha256, candidate_snapshot_sha256, reviewer_input_fingerprint,
             _now(), job_id))
        return dict(cur.fetchone())


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
            # Committed seed rows predate actionable dossier provenance. Keep
            # their evidence, but never seed them into the live review queue.
            if (
                row.get("status") == "awaiting_review"
                and not row.get("decision_contract_version")
            ):
                row["archived"] = 1
            cur.execute(
                """
                INSERT INTO jobs (job_id, thread_id, disease_name, status,
                    current_stage, created_at, updated_at, error_message,
                    total_cost_usd, report_path, decision, review_notes,
                    repurposing_only, archived, decision_contract_version,
                    report_contract_version, report_sha256,
                    candidate_snapshot_sha256, reviewer_input_fingerprint)
                VALUES (%(job_id)s, %(thread_id)s, %(disease_name)s, %(status)s,
                    %(current_stage)s, %(created_at)s, %(updated_at)s,
                    %(error_message)s, %(total_cost_usd)s, %(report_path)s,
                    %(decision)s, %(review_notes)s, %(repurposing_only)s,
                    %(archived)s, %(decision_contract_version)s,
                    %(report_contract_version)s, %(report_sha256)s,
                    %(candidate_snapshot_sha256)s,
                    %(reviewer_input_fingerprint)s)
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
    """Return the newest finished job with a reusable candidate pool.

    ``no_eligible_candidate`` is a terminal, valid pipeline outcome: the case
    ran to the eligibility gate but nothing was authorized to proceed. Its
    persisted candidate snapshot is still a legitimate basis for the Audit
    surface, so it must be discoverable alongside completed and awaiting-review
    jobs.
    """
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('completed', 'awaiting_review', 'no_eligible_candidate')
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


def save_candidate_snapshot(job_id: str, payload: dict[str, Any]) -> None:
    """Upsert a durable reviewer-payload snapshot for one job.

    The table is schema-managed alongside ``jobs`` (no application startup DDL):
    job_candidate_snapshots(job_id text primary key, payload_json jsonb not null,
    created_at text not null, updated_at text not null).
    """
    now = _now()
    with _conn(lock=True) as conn, conn.cursor() as cur:
        # Serialize against report finalization. Once report provenance exists,
        # the snapshot becomes immutable; exact-content retries remain safe.
        cur.execute(
            """
            SELECT report_sha256, candidate_snapshot_sha256
            FROM jobs WHERE job_id = %s FOR UPDATE
            """,
            (job_id,),
        )
        job_row = cur.fetchone()
        if not job_row:
            raise ValueError(f"job not found: {job_id}")
        cur.execute(
            """
            SELECT payload_json FROM job_candidate_snapshots
            WHERE job_id = %s FOR UPDATE
            """,
            (job_id,),
        )
        existing_row = cur.fetchone()
        existing_payload = existing_row[0] if existing_row else None
        if isinstance(existing_payload, str):
            existing_payload = json.loads(existing_payload)
        incoming_hash = hashlib.sha256(
            canonical_json_bytes(payload)).hexdigest()
        existing_hash = (
            hashlib.sha256(canonical_json_bytes(existing_payload)).hexdigest()
            if isinstance(existing_payload, dict) else None
        )
        report_sha = job_row[0]
        bound_snapshot_sha = job_row[1]
        if report_sha and existing_hash != incoming_hash:
            raise ValueError(
                "candidate snapshot is immutable after report finalization")
        if report_sha and bound_snapshot_sha != incoming_hash:
            raise ValueError(
                "candidate snapshot does not match finalized report metadata")
        if existing_hash == incoming_hash:
            return
        cur.execute(
            """
            INSERT INTO job_candidate_snapshots
                (job_id, payload_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE SET
                payload_json = EXCLUDED.payload_json,
                updated_at = EXCLUDED.updated_at
            """,
            (job_id, psycopg2.extras.Json(payload), now, now),
        )


def save_job_artifact(
    job_id: str,
    kind: str,
    payload: bytes,
    *,
    filename: str,
    content_type: str,
    report_sha256: str,
    candidate_snapshot_sha256: str,
    artifact_id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist one immutable dossier artifact.

    Repeating an interrupted finalization with byte-for-byte identical content is
    safe.  Supplying different bytes for an existing logical artifact is not:
    the caller must create a new job rather than silently rewrite evidence.
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unsupported artifact kind: {kind}")
    if not isinstance(payload, bytes):
        raise ValueError("artifact payload must be bytes")
    if not filename or "/" in filename or "\\" in filename or "\x00" in filename:
        raise ValueError("artifact filename must be a basename")
    if not content_type or "\r" in content_type or "\n" in content_type:
        raise ValueError("invalid artifact content type")
    if len(payload) > _ARTIFACT_MAX_BYTES[kind]:
        raise ValueError(f"{kind} artifact exceeds maximum size")
    content_sha256 = hashlib.sha256(payload).hexdigest()
    artifact_id = artifact_id or content_sha256
    if artifact_id != content_sha256:
        # Artifact URLs use a content-addressed ID.  Permitting an unrelated ID
        # would make an immutable cache URL claim the wrong bytes.
        raise ValueError("artifact_id must equal payload SHA-256")
    if len(report_sha256) != 64 or len(candidate_snapshot_sha256) != 64:
        raise ValueError("artifact provenance hashes must be SHA-256 values")

    with _conn(lock=True) as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE", (job_id,))
        if not cur.fetchone():
            raise ValueError(f"job not found: {job_id}")
        # The partial unique index in the DDL is the cross-instance guarantee;
        # this lock produces a useful immutable-content error before an insert.
        if kind in _SINGLETON_ARTIFACT_KINDS:
            cur.execute(
                """SELECT * FROM job_artifacts WHERE job_id = %s AND kind = %s
                   FOR UPDATE""",
                (job_id, kind),
            )
        else:
            cur.execute(
                """SELECT * FROM job_artifacts
                   WHERE job_id = %s AND kind = %s AND artifact_id = %s FOR UPDATE""",
                (job_id, kind, artifact_id),
            )
        existing = cur.fetchone()
        if existing:
            existing = dict(existing)
            if (existing["content_sha256"] != content_sha256
                    or bytes(existing["payload"]) != payload
                    or existing["report_sha256"] != report_sha256
                    or existing["candidate_snapshot_sha256"]
                    != candidate_snapshot_sha256):
                raise ValueError("immutable artifact already exists with different content")
            return _artifact_metadata(existing)
        cur.execute(
            """INSERT INTO job_artifacts
               (job_id, kind, artifact_id, content_sha256, filename, content_type,
                size_bytes, payload, report_sha256, candidate_snapshot_sha256, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (job_id, kind, artifact_id, content_sha256, filename, content_type,
             len(payload), psycopg2.Binary(payload), report_sha256,
             candidate_snapshot_sha256, _now()),
        )
        saved = cur.fetchone()
    return _artifact_metadata(dict(saved))


def _artifact_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Return public-safe artifact metadata; never leak PostgreSQL BYTEA."""
    return {key: row[key] for key in (
        "job_id", "kind", "artifact_id", "content_sha256", "filename",
        "content_type", "size_bytes", "report_sha256",
        "candidate_snapshot_sha256", "created_at",
    )}


def get_job_artifact(
    job_id: str, kind: str, artifact_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Fetch one artifact including payload for an API response."""
    if kind not in ARTIFACT_KINDS:
        return None
    if kind in _SINGLETON_ARTIFACT_KINDS:
        artifact_id = None
    with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if artifact_id:
            cur.execute(
                """SELECT * FROM job_artifacts WHERE job_id = %s AND kind = %s
                   AND artifact_id = %s""", (job_id, kind, artifact_id),
            )
        else:
            cur.execute(
                "SELECT * FROM job_artifacts WHERE job_id = %s AND kind = %s",
                (job_id, kind),
            )
        row = cur.fetchone()
    return dict(row) if row else None


def list_job_artifacts(job_id: str, kind: Optional[str] = None) -> list[dict[str, Any]]:
    """List public metadata for a job's durable artifacts, without payloads."""
    if kind is not None and kind not in ARTIFACT_KINDS:
        return []
    with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if kind:
            cur.execute(
                "SELECT * FROM job_artifacts WHERE job_id = %s AND kind = %s "
                "ORDER BY created_at, artifact_id", (job_id, kind),
            )
        else:
            cur.execute(
                "SELECT * FROM job_artifacts WHERE job_id = %s "
                "ORDER BY kind, created_at, artifact_id", (job_id,),
            )
        rows = cur.fetchall()
    return [_artifact_metadata(dict(row)) for row in rows]


# Compact aliases retain a natural API for callers/tests while keeping the
# explicit names above clear at call sites.
save_artifact = save_job_artifact
get_artifact = get_job_artifact
list_artifacts = list_job_artifacts


def claim_job_for_review(job_id: str) -> Optional[dict[str, Any]]:
    """Atomically claim one awaiting-review job; exactly one caller can win."""
    with _conn(lock=True) as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE jobs
            SET status = 'reviewing', current_stage = 'awaiting_review',
                updated_at = %s
            WHERE job_id = %s AND status = 'awaiting_review'
            RETURNING *
            """,
            (_now(), job_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def finalize_job_report(
        job_id: str,
        *,
        report_path: str,
        decision_contract_version: str,
        report_contract_version: str,
        report_sha256: str,
        candidate_snapshot_sha256: str,
        reviewer_input_fingerprint: str,
) -> dict[str, Any]:
    """Atomically bind report metadata to the currently locked snapshot."""
    with _conn(lock=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT job_id FROM jobs WHERE job_id = %s FOR UPDATE",
            (job_id,),
        )
        if not cur.fetchone():
            raise ValueError(f"job not found: {job_id}")
        cur.execute(
            "SELECT payload_json FROM job_candidate_snapshots "
            "WHERE job_id = %s FOR UPDATE",
            (job_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("durable candidate snapshot missing at finalization")
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        actual_hash = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        if actual_hash != candidate_snapshot_sha256:
            raise ValueError(
                "candidate snapshot changed during report finalization")
        now = _now()
        cur.execute(
            """
            UPDATE jobs SET report_path = %s,
                decision_contract_version = %s,
                report_contract_version = %s,
                report_sha256 = %s,
                candidate_snapshot_sha256 = %s,
                reviewer_input_fingerprint = %s,
                updated_at = %s
            WHERE job_id = %s
            RETURNING *
            """,
            (
                report_path, decision_contract_version,
                report_contract_version, report_sha256,
                candidate_snapshot_sha256, reviewer_input_fingerprint,
                now, job_id,
            ),
        )
        finalized = cur.fetchone()
        if not finalized:
            raise ValueError(f"job not found: {job_id}")
    return dict(finalized) if isinstance(finalized, dict) else {
        "job_id": job_id,
        "report_path": report_path,
        "report_sha256": report_sha256,
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
    }


def get_candidate_snapshot(job_id: str) -> Optional[dict[str, Any]]:
    """Return a job's durable reviewer snapshot, if the schema/table is present."""
    with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT payload_json FROM job_candidate_snapshots WHERE job_id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    payload = row["payload_json"]
    # psycopg2 normally decodes jsonb to dict; accepting a string keeps this
    # seam compatible with mocked cursors and non-default JSON adapters.
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


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
        reaped = cur.rowcount
        review_msg = (
            "Uncertain review outcome: server restarted while the job was in "
            "reviewing state. The decision/checkpoint may have had side effects; "
            "the job was not restored to awaiting_review. Attempt context: "
        )
        cur.execute(
            """
            UPDATE jobs SET status='error',
                error_message=%s || COALESCE(error_message, decision, 'unavailable'),
                updated_at=%s
            WHERE status='reviewing'
            """,
            (review_msg, _now()),
        )
        return reaped + cur.rowcount


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
