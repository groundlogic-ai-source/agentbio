"""
Centralized Stage 1 sweep process manager.

Imported by both api/main.py (startup auto-trigger) and main_graph.py
(polling wait before target selection).  A single module-level _proc reference
means only ONE sweep process can run at a time, regardless of which caller
triggered it.
"""

import os
import subprocess
import threading
import time
from typing import Optional

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
TOP_CANDIDATES = os.path.join(REPO_ROOT, "output", "top_candidates.json")
SWEEP_LOG = "/tmp/sweep_run.log"

_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
_lock = threading.Lock()


def is_running() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None


def ensure_running() -> Optional[int]:
    """
    Start the sweep as a background subprocess if it is not already running.
    Returns the PID of the (new or existing) process, or None if top_candidates.json
    already exists (nothing to do).
    """
    if os.path.exists(TOP_CANDIDATES):
        return None  # already have a ranked list
    global _proc
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return _proc.pid  # already running
        log_fh = open(SWEEP_LOG, "a", buffering=1)
        _proc = subprocess.Popen(
            ["python", "-m", "agents.target_selection"],
            cwd=REPO_ROOT,
            stdout=log_fh,
            stderr=log_fh,
        )
        return _proc.pid


def status() -> dict:
    with _lock:
        if _proc is None:
            return {"status": "not_started"}
        rc = _proc.poll()
        return {
            "status": "running" if rc is None else ("ok" if rc == 0 else "error"),
            "returncode": rc,
            "pid": _proc.pid,
            "log": SWEEP_LOG,
        }


def wait_for_candidates(
    poll_interval: int = 30,
    max_wait_seconds: int = 24 * 3600,
) -> list:
    """
    Block until top_candidates.json exists and is non-empty, then return its rows.

    Ensures the sweep is running before waiting.  Raises RuntimeError if the sweep
    subprocess exits with a non-zero code (so the job fails fast on a real error
    rather than polling until the 24-hour ceiling).
    """
    ensure_running()

    deadline = time.time() + max_wait_seconds
    waited = 0

    while time.time() < deadline:
        # Check whether the file appeared yet.
        if os.path.exists(TOP_CANDIDATES):
            try:
                import json
                with open(TOP_CANDIDATES, "r", encoding="utf-8") as f:
                    rows = json.load(f)
                if rows:
                    print(f"[sweep_manager] sweep finished — "
                          f"{len(rows)} candidates ready (waited ~{waited}s)")
                    return rows
            except (ValueError, OSError):
                pass  # file partially written; keep waiting

        # Check if the sweep exited with an error — fail fast rather than timing out.
        with _lock:
            rc = _proc.poll() if _proc is not None else None
        if rc is not None and rc != 0:
            raise RuntimeError(
                f"Stage 1 sweep exited with code {rc}. "
                f"Check {SWEEP_LOG} for details."
            )

        time.sleep(poll_interval)
        waited += poll_interval
        if waited % 1800 == 0:  # log every 30 minutes
            hours = waited / 3600
            print(f"[sweep_manager] still waiting for sweep "
                  f"({hours:.1f}h elapsed)")

    raise RuntimeError(
        f"Stage 1 sweep did not produce top_candidates.json within "
        f"{max_wait_seconds // 3600}h. Check {SWEEP_LOG} for errors."
    )
