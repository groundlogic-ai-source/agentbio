import sqlite3
import hashlib
import json
import os
import time
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "cache.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(value_json)
    finally:
        conn.close()


def set(key: str, value: Any, ttl_days: float = 7) -> None:
    conn = _get_conn()
    try:
        expires_at = time.time() + ttl_days * 86400
        conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value, default=str), expires_at),
        )
        conn.commit()
    finally:
        conn.close()
