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
_WRITE_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()


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
            with _WRITE_LOCK:
                delete_conn = _get_conn()
                try:
                    delete_conn.execute(
                        "DELETE FROM cache WHERE key = ? AND expires_at = ?",
                        (key, expires_at),
                    )
                    delete_conn.commit()
                finally:
                    delete_conn.close()
            return None
        return json.loads(value_json)
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass


def set(key: str, value: Any, ttl_days: float = 7) -> None:
    expires_at = time.time() + ttl_days * 86400
    value_json = json.dumps(value, default=str)
    with _WRITE_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value_json, expires_at),
            )
            conn.commit()
        finally:
            conn.close()
