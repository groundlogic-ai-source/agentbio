"""
Resume a paused Stage 3 pipeline run (the human_review interrupt).

The main pipeline (main_graph.py) pauses at the human_review node via interrupt()
and persists its state to checkpoints.db. This CLI reopens that checkpoint and
resumes the run with the reviewer's decision using Command(resume=...).

Usage:
    python resume_review.py <thread_id> <approve|reject|edit> [note]

Examples:
    python resume_review.py run-1719500000 approve
    python resume_review.py run-1719500000 reject "binding pose confidence too low"
    python resume_review.py run-1719500000 edit "rerun with num_samples=3"
"""

import json
import sys

from langgraph.types import Command

from main_graph import build_graph


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    thread_id = sys.argv[1]
    decision = sys.argv[2].lower()
    note = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    if decision not in ("approve", "reject", "edit"):
        print(f"ERROR: decision must be approve|reject|edit, got '{decision}'")
        sys.exit(1)

    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    resume_value = {"decision": decision, "note": note}

    result = graph.invoke(Command(resume=resume_value), config=config)

    review = result.get("review")
    if review is None:
        print("WARNING: run did not complete; current state keys:", list(result.keys()))
    else:
        print("Run resumed and completed.")
        print(json.dumps(review, indent=2, default=str))


if __name__ == "__main__":
    main()
