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
import sys
import time
from typing import Any, Optional, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from agents.target_selection import OUTPUT_DIR, run as run_target_selection
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


def target_selection_node(state: PipelineState) -> dict[str, Any]:
    rows = None if FORCE_RECOMPUTE else _load_json("top_candidates.json")
    if rows is None:
        print("[graph] target_selection: running Stage 1 pipeline (this is slow on a cold cache)")
        run_target_selection()
        rows = _load_json("top_candidates.json")
    else:
        print("[graph] target_selection: reusing existing top_candidates.json")
    if not rows:
        raise RuntimeError("Stage 1 produced no candidates")
    r = rows[0]
    target = {
        "target_symbol": r["target_symbol"],
        "uniprot_id": r.get("uniprot_id"),
        "ensembl_id": r.get("ensembl_id"),
        "disease_name": r["disease_name"],
        "orpha_code": r.get("orpha_code"),
        "ot_association_score": r.get("ot_association_score", 0.0),
    }
    print(f"[graph] target: {target['target_symbol']} ({target.get('uniprot_id')}) "
          f"for {target['disease_name']}")
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
        reviewed, selected, structure_results, state.get("biologist_output"))
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
