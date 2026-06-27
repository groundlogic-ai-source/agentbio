"""
Resume a paused Stage 3 pipeline run (the human_review interrupt).

The main pipeline (main_graph.py) pauses at the human_review node via interrupt()
and persists its state to checkpoints.db. This module reopens that checkpoint and
resumes the run with the reviewer's decision using Command(resume=...).

`resume_run()` is the single, reusable resume entry point: both this CLI and the
Stage 4 FastAPI backend call it, so the resume logic is never duplicated.

Usage (CLI):
    python resume_review.py <thread_id> <approve|reject|edit> [note]

Examples:
    python resume_review.py run-1719500000 approve
    python resume_review.py run-1719500000 reject "binding pose confidence too low"
    python resume_review.py run-1719500000 edit "rerun with num_samples=3"
"""

import json
import sys
from typing import Any, Optional

from langgraph.types import Command

from main_graph import build_graph

VALID_DECISIONS = ("approve", "reject", "edit")


def resume_run(thread_id: str, decision: str, note: str = "") -> Optional[dict[str, Any]]:
    """
    Resume the paused graph for `thread_id` with the human reviewer's `decision`.

    Reopens the on-disk checkpoint (checkpoints.db) and resumes the single paused
    human_review node via Command(resume=...). Returns the recorded review dict
    (or None if the run did not complete).

    This is the ONE resume implementation shared by the CLI and the API layer.
    """
    decision = (decision or "").lower()
    if decision not in VALID_DECISIONS:
        raise ValueError(
            f"decision must be one of {VALID_DECISIONS}, got {decision!r}")

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    resume_value = {"decision": decision, "note": note}
    result = graph.invoke(Command(resume=resume_value), config=config)
    return result.get("review")


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    thread_id = sys.argv[1]
    decision = sys.argv[2].lower()
    note = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    if decision not in VALID_DECISIONS:
        print(f"ERROR: decision must be approve|reject|edit, got '{decision}'")
        sys.exit(1)

    review = resume_run(thread_id, decision, note)
    if review is None:
        print("WARNING: run did not complete (no review recorded).")
    else:
        print("Run resumed and completed.")
        print(json.dumps(review, indent=2, default=str))


if __name__ == "__main__":
    main()
