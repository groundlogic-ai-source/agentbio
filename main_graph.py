"""
Stage 3 orchestration graph (LangGraph).

Wires the existing Stage 1/2 agents plus the Stage 3 structure-validation and
writer steps into a single, checkpointed pipeline:

    target_selection -> biologist -> chemist -> reviewer
        -> structure_validation -> writer -> human_review

Key properties
--------------
- DURABLE CHECKPOINTS: a SqliteSaver backed by an on-disk SQLite file
  (checkpoints.db) persists every step, so a run can be paused and resumed from a
  separate process (see resume_review.py).
- HUMAN IN THE LOOP: ONLY the human_review node calls interrupt(). It performs no
  API calls before the interrupt, so resuming (which re-executes the node from its
  start) is safe and never re-spends.
- IDEMPOTENT UPSTREAM: the Stage 1/2 nodes reuse the on-disk artifact from a prior
  successful stage run when present, so re-running Stage 3 does not redo the
  expensive Stage 1/2 work. Set STAGE3_FORCE_RECOMPUTE=1 to force fresh upstream
  computation.

Run a new pipeline:   python main_graph.py
Resume a paused run:  python resume_review.py <thread_id> <approve|reject|edit> [note]
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from agents.target_selection import (
    OUTPUT_DIR,
    run as run_target_selection,
    select_for_disease,
)
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from agents.reviewer import (
    run_reviewer,
    COMPOSITE_WEIGHTS,
    LIPINSKI_PENALTY,
    STRONG_MATCH_THRESHOLD,
)
from agents import provenance
from data_sources.afdb import get_structure_confidence
from data_sources.uniprot import get_protein_sequence
from data_sources import boltz_api
from agents import writer

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CHECKPOINT_DB = os.path.join(REPO_ROOT, "checkpoints.db")

FORCE_RECOMPUTE = os.environ.get("STAGE3_FORCE_RECOMPUTE", "0") == "1"
# When the Reviewer flags zero STRONG_MATCH candidates, still produce one report
# for the highest-ranked candidate (clearly marked as below threshold) unless this
# is set, in which case only true STRONG_MATCH candidates are written.
STRONG_ONLY = os.environ.get("STAGE3_STRONG_ONLY", "0") == "1"
# Cap how many candidates get a (paid) structure prediction per run.
MAX_STRUCTURE_CANDIDATES = int(os.environ.get("STAGE3_MAX_CANDIDATES", "3"))
BOLTZ_NUM_SAMPLES = int(os.environ.get("STAGE3_BOLTZ_SAMPLES", "1"))


class PipelineState(TypedDict, total=False):
    requested_disease: str
    target: dict[str, Any]
    biologist_output: dict[str, Any]
    chemist_output: dict[str, Any]
    reviewed: dict[str, Any]
    selected: list[dict[str, Any]]
    structure_results: dict[str, Any]
    reports: list[dict[str, Any]]
    review: dict[str, Any]


def _load_json(name: str) -> Optional[Any]:
    path = os.path.join(OUTPUT_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _write_json(name: str, data: Any) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(os.path.join(OUTPUT_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ----------------------------------------------------------------------------- nodes


# Stage 2/3 artifacts whose contents are specific to the SELECTED target. They are
# reused across runs keyed only on file existence, so they MUST be cleared whenever
# the selected target changes — otherwise a new target reuses the previous target's
# biologist/chemist/reviewer/structure output and the report describes the wrong pair.
_DOWNSTREAM_ARTIFACTS = (
    "biologist_output.json",
    "chemist_output.json",
    "reviewed_candidates.json",
    "structure_validation.json",
)
_ACTIVE_SELECTION = "active_selection.json"


def _target_from_row(r: dict[str, Any]) -> dict[str, Any]:
    """Build the graph `target` dict, carrying the real Stage 1 scores forward."""
    return {
        "target_symbol": r["target_symbol"],
        "uniprot_id": r.get("uniprot_id"),
        "ensembl_id": r.get("ensembl_id"),
        "disease_name": r["disease_name"],
        "orpha_code": r.get("orpha_code"),
        "ot_association_score": r.get("ot_association_score", 0.0),
        "tractability_score": r.get("tractability_score"),
        "unmet_need_score": r.get("unmet_need_score"),
    }


def _invalidate_downstream_if_target_changed(target: dict[str, Any]) -> None:
    """
    Clear Stage 2/3 artifacts when the selected (disease, target) differs from the
    one the on-disk artifacts were built for. Resume is unaffected: it replays from
    checkpoints.db, not these files.
    """
    marker = (target.get("disease_name"), target.get("target_symbol"))
    prev = _load_json(_ACTIVE_SELECTION)
    prev_marker = None
    if isinstance(prev, dict):
        prev_marker = (prev.get("disease_name"), prev.get("target_symbol"))
    if prev_marker == marker:
        return
    for name in _DOWNSTREAM_ARTIFACTS:
        path = os.path.join(OUTPUT_DIR, name)
        if os.path.exists(path):
            os.remove(path)
            print(f"[graph] target_selection: cleared stale {name} (target changed)")


def _pick_unexplored_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Auto-pick the highest-ranked (disease, target) pair not yet used by any prior
    run. The pick is claimed ATOMICALLY (selected + recorded under one DB lock) so
    two concurrent blank runs can't grab the same pair. Falls back to the top row
    once the whole ranked list has been explored.
    """
    from api import jobs_db  # lazy: avoids a graph->api import cycle at module load

    jobs_db.init_db()  # CLI graph runs may not have gone through the API boot path
    candidates = [(r.get("disease_name"), r.get("target_symbol")) for r in rows]
    claimed = jobs_db.claim_next_unexplored(candidates)
    if claimed is None:
        print("[graph] target_selection: every ranked pair already explored — "
              "falling back to the top candidate")
        return rows[0]

    dkey, tkey = jobs_db._norm(claimed[0]), jobs_db._norm(claimed[1])
    for r in rows:
        if (jobs_db._norm(r.get("disease_name")),
                jobs_db._norm(r.get("target_symbol"))) == (dkey, tkey):
            return r
    return rows[0]


_SWEEP_LOG = "/tmp/sweep_run.log"
_WORKSPACE = REPO_ROOT
_graph_sweep_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]


def _ensure_sweep_running() -> None:
    """Start the Stage 1 sweep as a background subprocess if not already running."""
    global _graph_sweep_proc
    if _graph_sweep_proc is not None and _graph_sweep_proc.poll() is None:
        return  # already running
    log_fh = open(_SWEEP_LOG, "a", buffering=1)
    _graph_sweep_proc = subprocess.Popen(
        ["python", "-m", "agents.target_selection"],
        cwd=_WORKSPACE,
        stdout=log_fh,
        stderr=log_fh,
    )
    print(f"[graph] target_selection: sweep started as background process "
          f"(pid={_graph_sweep_proc.pid})")


def _wait_for_sweep(
    poll_interval: int = 30,
    max_wait_seconds: int = 4 * 3600,
) -> Optional[list]:
    """
    Wait for the Stage 1 sweep to produce top_candidates.json.

    Rather than running the sweep inline (which blocks this thread for 1-3 hours
    and shows 'WORKING' forever), we ensure the sweep is running as a background
    subprocess and poll for the output file. Progress is printed every interval so
    the caller can see the job is alive.
    """
    _ensure_sweep_running()
    deadline = time.time() + max_wait_seconds
    waited = 0
    while time.time() < deadline:
        rows = _load_json("top_candidates.json")
        if rows:
            print(f"[graph] target_selection: sweep finished — "
                  f"{len(rows)} candidates available (waited ~{waited}s)")
            return rows
        time.sleep(poll_interval)
        waited += poll_interval
        if waited % 300 == 0:  # log every 5 minutes
            pct = 100 * waited / max_wait_seconds
            print(f"[graph] target_selection: waiting for sweep "
                  f"({waited}s elapsed, {pct:.0f}% of max wait)")
    raise RuntimeError(
        f"Stage 1 sweep did not produce top_candidates.json within "
        f"{max_wait_seconds // 3600}h. Check /tmp/sweep_run.log for errors."
    )


def target_selection_node(state: PipelineState) -> dict[str, Any]:
    requested = (state.get("requested_disease") or "").strip()

    if requested:
        # Manual mode: score the requested disease directly. select_for_disease
        # raises DiseaseNotInUniverse / RuntimeError, which surfaces to the caller
        # as a clean job error rather than a silent auto-pick fallback.
        print(f"[graph] target_selection: manual disease request '{requested}'")
        rows = select_for_disease(requested)
        r = rows[0]
    else:
        rows = None if FORCE_RECOMPUTE else _load_json("top_candidates.json")
        if rows is None:
            rows = _wait_for_sweep()
        else:
            print("[graph] target_selection: reusing existing top_candidates.json")
        if not rows:
            raise RuntimeError("Stage 1 produced no candidates")
        r = _pick_unexplored_row(rows)

    target = _target_from_row(r)

    # Clear stale downstream artifacts BEFORE recording the new selection marker.
    _invalidate_downstream_if_target_changed(target)
    _write_json(_ACTIVE_SELECTION, r)

    # Record the pair so future blank runs explore further down the list. Recorded
    # for both modes ("any disease+target pair already used in a prior run").
    try:
        from api import jobs_db
        jobs_db.record_explored(target["disease_name"], target["target_symbol"])
    except Exception as e:  # persistence is best-effort; never fail the run on it
        print(f"[graph] target_selection: WARN could not record explored pair: {e}")

    print(f"[graph] target: {target['target_symbol']} ({target.get('uniprot_id')}) "
          f"for {target['disease_name']} "
          f"[tractability={target.get('tractability_score')}, "
          f"unmet_need={target.get('unmet_need_score')}]")
    return {"target": target}


def biologist_node(state: PipelineState) -> dict[str, Any]:
    existing = None if FORCE_RECOMPUTE else _load_json("biologist_output.json")
    if existing is not None:
        print("[graph] biologist: reusing existing biologist_output.json")
        return {"biologist_output": existing}
    # A fresh Stage 2 pass starts the shared provenance log at the biologist.
    provenance.reset()
    out = run_biologist(state["target"])
    _write_json("biologist_output.json", out)
    print(f"[graph] biologist: {len(out['interacting_genes'])} interactors, "
          f"{len(out['literature_hits'])} literature hits")
    return {"biologist_output": out}


def chemist_node(state: PipelineState) -> dict[str, Any]:
    existing = None if FORCE_RECOMPUTE else _load_json("chemist_output.json")
    if existing is not None:
        print("[graph] chemist: reusing existing chemist_output.json")
        return {"chemist_output": existing}
    out = run_chemist(state["biologist_output"])
    _write_json("chemist_output.json", out)
    print(f"[graph] chemist: {len(out['candidates'])} candidates ranked")
    return {"chemist_output": out}


def reviewer_node(state: PipelineState) -> dict[str, Any]:
    existing = None if FORCE_RECOMPUTE else _load_json("reviewed_candidates.json")
    if existing is not None:
        print("[graph] reviewer: reusing existing reviewed_candidates.json")
        return {"reviewed": existing}
    reviewed = run_reviewer(state["chemist_output"], state.get("biologist_output"))
    payload = {
        "formula": {
            "composite_weights": COMPOSITE_WEIGHTS,
            "lipinski_penalty": LIPINSKI_PENALTY,
            "strong_match_threshold": STRONG_MATCH_THRESHOLD,
            "normalization": "min-max across the candidate set (equal values -> 1.0 if >0)",
        },
        "n_candidates": len(reviewed),
        "n_strong_matches": sum(1 for r in reviewed if r["strong_match"]),
        "candidates": reviewed,
    }
    _write_json("reviewed_candidates.json", payload)
    print(f"[graph] reviewer: {payload['n_strong_matches']} STRONG_MATCH of "
          f"{payload['n_candidates']}")
    return {"reviewed": payload}


def _select_candidates(reviewed: dict[str, Any]) -> list[dict[str, Any]]:
    cands = reviewed.get("candidates", [])
    strong = [c for c in cands if c.get("strong_match")]
    if strong:
        return strong[:MAX_STRUCTURE_CANDIDATES]
    if STRONG_ONLY:
        return []
    # Demonstration fallback: highest-ranked candidate, flagged as below threshold.
    return cands[:1]


def structure_validation_node(state: PipelineState) -> dict[str, Any]:
    reviewed = state["reviewed"]
    target = state.get("target", {})
    uniprot = target.get("uniprot_id")
    selected = _select_candidates(reviewed)

    if not selected:
        print("[graph] structure_validation: no STRONG_MATCH candidates and "
              "STAGE3_STRONG_ONLY=1 — nothing to validate")
        return {"selected": [], "structure_results": {}}

    # AFDB apo pre-check is per-TARGET (all candidates are ligands vs the same target).
    afdb = {"has_structure": False, "mean_plddt": None, "model_url": None}
    if uniprot:
        a = get_structure_confidence(uniprot)
        afdb = {
            "has_structure": a.get("has_structure", False),
            "mean_plddt": a.get("mean_pLDDT"),
            "model_url": a.get("model_url"),
        }
        print(f"[graph] AFDB apo pre-check {uniprot}: has_structure="
              f"{afdb['has_structure']} mean_pLDDT={afdb['mean_plddt']} "
              f"(apo only, no ligand — Boltz still called for the complex)")

    sequence = get_protein_sequence(uniprot) if uniprot else None
    if not sequence:
        print(f"[graph] WARNING: no protein sequence for {uniprot}; "
              f"Boltz complex prediction will be skipped")

    structure_results: dict[str, Any] = {}
    for cand in selected:
        drug = cand.get("drug_name", "unknown")
        smiles = cand.get("smiles")
        print(f"[graph] structure_validation: {drug} (smiles={str(smiles)[:32]})")
        complex_res = boltz_api.predict_complex(sequence, smiles, num_samples=BOLTZ_NUM_SAMPLES) \
            if (sequence and smiles) else {
                "available": False,
                "error": "missing protein sequence or ligand SMILES",
                "structure_confidence": None, "binding_pose_confidence": None,
                "predicted_affinity": None, "pdb_or_cif_url": None,
                "estimated_cost_usd": 0.0, "raw_metrics": {},
            }
        adme_res = boltz_api.predict_adme(smiles) if smiles else {
            "available": False, "error": "missing SMILES",
            "lipophilicity": None, "permeability": None, "solubility": None,
        }
        structure_results[drug] = {
            "afdb": afdb,
            "complex": complex_res,
            "adme": adme_res,
        }

    _write_json("structure_validation.json", structure_results)
    return {"selected": selected, "structure_results": structure_results}


def writer_node(state: PipelineState) -> dict[str, Any]:
    reviewed = state["reviewed"]
    selected = state.get("selected", [])
    structure_results = state.get("structure_results", {})
    if not selected:
        print("[graph] writer: no candidates selected — no reports written")
        return {"reports": []}
    reports = writer.run_writer(
        reviewed, selected, structure_results, state.get("biologist_output"),
        state.get("target"))
    print(f"[graph] writer: wrote {len(reports)} report(s) to output/reports/")
    return {"reports": reports}


def human_review_node(state: PipelineState) -> dict[str, Any]:
    """ONLY node with interrupt(). No API calls precede the interrupt()."""
    reports = state.get("reports", [])
    summary = {
        "message": "Approve, edit, or reject the compiled repurposing report(s).",
        "n_reports": len(reports),
        "reports": reports,
        "how_to_resume": "python resume_review.py <thread_id> <approve|reject|edit> [note]",
    }
    decision = interrupt(summary)  # pauses here until resumed with Command(resume=...)

    if isinstance(decision, dict):
        d = decision
    else:
        d = {"decision": str(decision)}
    review = {
        "decision": d.get("decision"),
        "note": d.get("note", ""),
        "reviewed_reports": [r.get("path") for r in reports],
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _write_json("review_decision.json", review)
    print(f"[graph] human_review: decision={review['decision']}")
    return {"review": review}


# ----------------------------------------------------------------------------- graph


def build_graph():
    g = StateGraph(PipelineState)
    g.add_node("target_selection", target_selection_node)
    g.add_node("biologist", biologist_node)
    g.add_node("chemist", chemist_node)
    g.add_node("reviewer", reviewer_node)
    g.add_node("structure_validation", structure_validation_node)
    g.add_node("writer", writer_node)
    g.add_node("human_review", human_review_node)

    g.add_edge(START, "target_selection")
    g.add_edge("target_selection", "biologist")
    g.add_edge("biologist", "chemist")
    g.add_edge("chemist", "reviewer")
    g.add_edge("reviewer", "structure_validation")
    g.add_edge("structure_validation", "writer")
    g.add_edge("writer", "human_review")
    g.add_edge("human_review", END)

    # On-disk SQLite connection (NOT a context manager) so the saver outlives a
    # single process and resume_review.py can reopen the same checkpoints.
    conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)


def run(thread_id: Optional[str] = None) -> None:
    graph = build_graph()
    thread_id = thread_id or f"run-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    print(f"[graph] starting thread_id={thread_id}")
    result = graph.invoke({}, config=config)

    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]
        print("\n" + "=" * 70)
        print("PIPELINE PAUSED for human review.")
        print(f"thread_id: {thread_id}")
        print(json.dumps(payload, indent=2, default=str))
        print("Resume with:")
        print(f"  python resume_review.py {thread_id} approve")
        print(f"  python resume_review.py {thread_id} reject \"reason\"")
        print(f"  python resume_review.py {thread_id} edit \"note\"")
        print("=" * 70)
    else:
        print("[graph] completed without interrupt; review:", result.get("review"))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
