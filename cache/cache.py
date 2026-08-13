import queue
import sqlite3
import hashlib
import json
import os
import threading
import time
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "cache.db")
_BUSY_TIMEOUT_MS = 30_000
_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()

# Writes are HYBRID: set() first tries an inline write under a BOUNDED lock
# acquire (2s), preserving the read-your-writes contract that callers and
# tests rely on; on contention it falls back to a bounded queue drained by
# one daemon writer thread.
# Root cause of the 2026-08-12 prefetch wedges: a worker held the old
# process-wide write lock while stuck inside sqlite conn.close() (no timeout
# covers a threading.Lock wait), freezing every network lane at once. The
# cache is best-effort — a dropped write only costs a refetch — so workers
# must NEVER block on SQLite without a timeout. A stuck lock-holder now just
# pushes everyone else onto the queue after 2s; a stuck queue writer fills
# the queue and writes get dropped while lanes keep flowing.
_WRITE_LOCK = threading.Lock()
_INLINE_LOCK_TIMEOUT_SECONDS = 2.0
_WRITE_QUEUE: "queue.Queue[tuple]" = queue.Queue(maxsize=20_000)
_WRITER_START_LOCK = threading.Lock()
_writer_started = False
_dropped_writes = 0


def _get_conn() -> sqlite3.Connection:
    """Open a cache connection safe for concurrent source-enrichment workers.

    Reviewer prefetch uses several thread pools.  Re-running schema DDL on every
    connection made otherwise-independent cache writes contend on SQLite's
    database lock.  Initialize each database path once per process, use WAL so
    readers do not block the single writer, and retain a generous busy timeout
    for coordination with other AgentBio processes.
    """
    db_path = os.path.abspath(DB_PATH)
    conn = sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    if db_path not in _INITIALIZED_PATHS:
        with _INIT_LOCK:
            if db_path not in _INITIALIZED_PATHS:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                conn.commit()
                _INITIALIZED_PATHS.add(db_path)
    return conn


def make_key(func_name: str, *args, **kwargs) -> str:
    raw = json.dumps({"fn": func_name, "args": args, "kwargs": kwargs}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str) -> Optional[Any]:
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value_json, expires_at = row
        if time.time() > expires_at:
            conn.close()
            _enqueue_write("delete", key, expires_at, None)
            return None
        return json.loads(value_json)
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass


def _writer_loop() -> None:
    """Single daemon writer thread: drains the write queue on ONE persistent
    connection (opened lazily, used only by this thread). If SQLite ever
    hangs (commit/close), only THIS thread is lost — workers never block;
    the queue fills and writes are dropped, which for a best-effort cache
    only costs a refetch."""
    conn = None
    while True:
        item = _WRITE_QUEUE.get()
        try:
            if conn is None:
                conn = _get_conn()
            if item[0] == "delete":
                conn.execute(
                    "DELETE FROM cache WHERE key = ? AND expires_at = ?",
                    (item[1], item[2]),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO cache (key, value, expires_at) "
                    "VALUES (?, ?, ?)",
                    (item[1], item[2], item[3]),
                )
            conn.commit()
        except Exception:  # noqa: BLE001 — best-effort cache, reconnect next write
            try:
                if conn is not None:
                    conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
        finally:
            _WRITE_QUEUE.task_done()


def _enqueue_write(op: str, key: str, value_or_expiry, expires_at) -> None:
    global _writer_started, _dropped_writes
    with _WRITER_START_LOCK:
        if not _writer_started:
            threading.Thread(target=_writer_loop, daemon=True,
                             name="cache-writer").start()
            _writer_started = True
    try:
        _WRITE_QUEUE.put_nowait((op, key, value_or_expiry, expires_at))
    except queue.Full:
        _dropped_writes += 1
        if _dropped_writes % 500 == 1:
            print(f"[cache] writer queue full — dropped {_dropped_writes} "
                  "write(s) so far (cache is best-effort)", flush=True)


def _flush_writes_for_test(timeout: float = 30.0) -> None:
    """Test hook: wait until the writer queue has drained."""
    deadline = time.time() + timeout
    while _WRITE_QUEUE.unfinished_tasks and time.time() < deadline:
        time.sleep(0.01)
    if _WRITE_QUEUE.unfinished_tasks:
        raise TimeoutError("cache writer queue did not drain")


def set(key: str, value: Any, ttl_days: float = 7) -> None:
    """Cache a value. Overwrites any existing entry for the same key.

    Inline synchronous write under a BOUNDED lock acquire (read-your-writes
    holds in the healthy case); if the lock cannot be acquired within 2s —
    e.g. a holder wedged inside SQLite — the write falls back to the async
    writer queue so callers never block on SQLite beyond the timeout.
    """
    expires_at = time.time() + ttl_days * 86400
    value_json = json.dumps(value, default=str)
    if _WRITE_LOCK.acquire(timeout=_INLINE_LOCK_TIMEOUT_SECONDS):
        try:
            try:
                conn = _get_conn()
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO cache (key, value, expires_at) "
                        "VALUES (?, ?, ?)",
                        (key, value_json, expires_at),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return
            except sqlite3.Error:
                pass  # fall through to the queued write
        finally:
            _WRITE_LOCK.release()
    _enqueue_write("put", key, value_json, expires_at)
