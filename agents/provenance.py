"""
Shared provenance log for the Stage 2 agents.

Every external fact used to build a candidate's score is recorded here with its
source type (e.g. "pmid", "chembl_activity", "biogrid_interaction") and id, plus
which agent used it. The reviewer uses `dedupe_pairs` so that the SAME source id
is only counted once even when it supports multiple parts of the analysis.

The log is a single JSON file (output/provenance_log.json) appended to by the
biologist, chemist, and reviewer in turn.
"""

import json
import os
from typing import Any, Iterable

from agents.target_selection import OUTPUT_DIR

PROVENANCE_PATH = os.path.join(OUTPUT_DIR, "provenance_log.json")


def _load_raw() -> dict[str, Any]:
    if os.path.exists(PROVENANCE_PATH):
        try:
            with open(PROVENANCE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"entries": []}


def _save_raw(data: dict[str, Any]) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(PROVENANCE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def load_provenance() -> dict[str, Any]:
    return _load_raw()


def reset() -> None:
    """Clear the log — call once at the start of a fresh Stage 2 run."""
    _save_raw({"entries": []})


def log_many(entries: Iterable[dict[str, Any]]) -> None:
    """
    Append multiple provenance entries. Each entry should have at least
    `source_type`, `source_id`, and `used_by`; `context` is optional.

    De-duplicates on write: the same (source_type, source_id, used_by) is logged
    only once, so the raw log stays idempotent even if an agent is re-run on its
    own without a preceding `reset()`. Constraint: a pmid / chembl activity id is
    counted once.
    """
    data = _load_raw()
    existing = {
        (e.get("source_type"), e.get("source_id"), e.get("used_by"))
        for e in data["entries"]
    }
    for e in entries:
        key = (e.get("source_type"), str(e.get("source_id")), e.get("used_by"))
        if key in existing:
            continue
        existing.add(key)
        data["entries"].append({
            "source_type": e.get("source_type"),
            "source_id": str(e.get("source_id")),
            "used_by": e.get("used_by"),
            "context": e.get("context"),
        })
    _save_raw(data)


def log_provenance(source_type: str, source_id: Any, used_by: str,
                   context: str | None = None) -> None:
    log_many([{
        "source_type": source_type,
        "source_id": source_id,
        "used_by": used_by,
        "context": context,
    }])


def dedupe_pairs(pairs: Iterable[tuple[str, Any]]) -> list[dict[str, str]]:
    """
    Collapse (source_type, source_id) pairs to unique entries, preserving order.
    Returns [{source_type, source_id}].
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for stype, sid in pairs:
        key = (str(stype), str(sid))
        if key in seen:
            continue
        seen.add(key)
        out.append({"source_type": str(stype), "source_id": str(sid)})
    return out
