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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional, TypedDict

import sweep_manager

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from agents.target_selection import (
    OUTPUT_DIR,
    run as run_target_selection,
    select_for_disease,
    select_source_diverse_targets,
)
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from data_sources.multisource_candidates import merge_chemist_candidates
from agents.reviewer import (
    run_reviewer,
    COMPOSITE_WEIGHTS,
    LIPINSKI_PENALTY,
    STRONG_MATCH_THRESHOLD,
    PCHEMBL_NORM_MIN,
    PCHEMBL_NORM_MAX,
)
from agents.schemas import validate_chemist_handoff, validate_reviewer_handoff
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
# Number of targets to pursue in parallel for each disease (Top-K pursuit).
# When K > 1: biologist + chemist run in parallel for each target; candidates
# are pooled before the reviewer; structure validation hard-caps at 1 Boltz
# call as a cost guardrail.  Set TOP_K_TARGETS=1 to restore single-target mode.
TOP_K_TARGETS = int(os.environ.get("TOP_K_TARGETS", "5"))

# Score-decay-relative cutoff (alternative / complement to fixed TOP_K_TARGETS).
# When TOP_K_FRACTION is set to a value in (0, 1), targets are included only if
# their combined score (tractability + unmet_need) is >= FRACTION × top_score,
# capped at TOP_K_MAX.  The combined score is used rather than the raw OT
# association_score because OT association degenerates to 0 for all
# pharmacological-precedent targets (diseases matched via the ChEMBL mechanism
# table instead of OT EFO associations), making a fractional OT cutoff
# mathematically undefined (0 × anything = 0, so all targets would be included
# regardless of the fraction chosen).  The combined score is always populated.
#
# When TOP_K_FRACTION is unset or 0, the plain TOP_K_TARGETS integer cap applies.
TOP_K_FRACTION = float(os.environ.get("TOP_K_FRACTION", "0.0"))
TOP_K_MAX      = int(os.environ.get("TOP_K_MAX", "10"))


class PipelineState(TypedDict, total=False):
    job_id: Optional[str]
    repurposing_only: bool
    requested_disease: str
    # Primary (top-ranked) target — kept for backwards compat with nodes that
    # only need a single target (structure_validation uses its uniprot_id for
    # AFDB; the marker/invalidation logic uses disease_name + target_symbol).
    target: dict[str, Any]
    # All K targets selected in this run (len >= 1).  When TOP_K_TARGETS = 1
    # this is a one-element list equal to [target].
    targets: list[dict[str, Any]]
    biologist_output: dict[str, Any]   # primary target's output (compat)
    biologist_outputs: list[dict[str, Any]]  # one per target, same order as targets
    k_bio_failed: int                  # count of biologist stubs (K>1 only)
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
        # Carry the real discovery path so chemist.py:237 never silently
        # defaults to "genetic_association" for pharm-precedent / pathway-
        # neighbor primary targets.  Falls back only when the source row
        # genuinely has no method recorded (should not happen in practice).
        "target_discovery_method": r.get("target_discovery_method", "genetic_association"),
        "mechanism_class": r.get("mechanism_class"),
        "therapeutic_role": r.get("therapeutic_role", "disease_modifying"),
        "process_support": r.get("process_support", []),
        "process_query": r.get("process_query"),
        "process_source_status": r.get("process_source_status"),
        "process_ontology_version": r.get("process_ontology_version"),
        "process_target_priority": r.get("process_target_priority"),
        "process_class_priority": r.get("process_class_priority"),
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


def _apply_k_cutoff(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Apply the active K-target cutoff policy to a ranked list of disease rows
    (already sorted best-first, same disease).

    Policy (in priority order):
    1. If TOP_K_FRACTION > 0: include targets whose combined score
       (tractability_score + unmet_need_score) is >= TOP_K_FRACTION × top_score,
       then cap at TOP_K_MAX.  Falls back to plain TOP_K_TARGETS when all scores
       are 0 (degenerate / pharmacological-precedent diseases where OT returns
       no associations and tractability scores collapse to 0).
    2. Otherwise: plain slice of TOP_K_TARGETS rows.

    The combined score is used instead of the raw OT association_score because
    OT association is 0 for pharmacological-precedent targets, making a fractional
    OT cutoff undefined (0 × f = 0 always includes everything).
    """
    if not rows:
        return []

    if TOP_K_FRACTION > 0:
        top_score = (
            (rows[0].get("tractability_score") or 0.0)
            + (rows[0].get("unmet_need_score") or 0.0)
        )
        if top_score > 0:
            cutoff = TOP_K_FRACTION * top_score
            kept = [
                r for r in rows
                if ((r.get("tractability_score") or 0.0)
                    + (r.get("unmet_need_score") or 0.0)) >= cutoff
            ]
            result = select_source_diverse_targets(kept, TOP_K_MAX)
            print(
                f"[graph] target_selection: fraction-cutoff TOP_K_FRACTION={TOP_K_FRACTION} "
                f"top_score={top_score:.3f} cutoff={cutoff:.3f} → "
                f"{len(result)} target(s) (cap={TOP_K_MAX})"
            )
            return result
        # Degenerate case: all scores 0 → fall through to integer cap below.
        print(
            "[graph] target_selection: TOP_K_FRACTION set but top_score=0 "
            "(pharmacological-precedent disease) — falling back to TOP_K_TARGETS"
        )

    return select_source_diverse_targets(rows, TOP_K_TARGETS)


def target_selection_node(state: PipelineState) -> dict[str, Any]:
    requested = (state.get("requested_disease") or "").strip()

    if requested:
        # Manual mode: score the requested disease directly. select_for_disease
        # raises DiseaseNotInUniverse / RuntimeError, which surfaces to the caller
        # as a clean job error rather than a silent auto-pick fallback.
        print(f"[graph] target_selection: manual disease request '{requested}'")
        all_rows = select_for_disease(requested)
        top_rows = _apply_k_cutoff(all_rows)
    else:
        rows = None if FORCE_RECOMPUTE else _load_json("top_candidates.json")
        if rows is None:
            print("[graph] target_selection: top_candidates.json missing — "
                  "waiting for background sweep")
            rows = sweep_manager.wait_for_candidates()
        else:
            print("[graph] target_selection: reusing existing top_candidates.json")
        if not rows:
            raise RuntimeError("Stage 1 produced no candidates")

        # Blank mode: atomically claim the primary (highest-ranked unexplored) pair,
        # then gather the remaining top-K targets for the SAME disease from the
        # pre-ranked list so one run explores multiple targets together.
        primary_row = _pick_unexplored_row(rows)
        primary_disease = primary_row.get("disease_name", "")
        # Gather all rows for the chosen disease (the ranked list is already sorted
        # by combined score within each disease, so slicing preserves that order).
        disease_rows = [r for r in rows
                        if r.get("disease_name") == primary_disease]
        top_rows = _apply_k_cutoff(disease_rows)
        # Make sure the atomically-claimed primary is always first.
        primary_sym = primary_row.get("target_symbol", "")
        reordered = [primary_row] + [
            r for r in top_rows
            if r.get("target_symbol") != primary_sym
        ]
        top_rows = reordered[:max(TOP_K_TARGETS, TOP_K_MAX)]

    # Build target dicts for all K rows.
    targets = [_target_from_row(r) for r in top_rows]
    target = targets[0]   # primary — used by downstream nodes that take one target

    # Clear stale downstream artifacts BEFORE recording the new selection marker.
    # We use the primary target for the invalidation marker (K-pursuit stays on
    # the same disease, so the disease+primary_target pair identifies the run).
    _invalidate_downstream_if_target_changed(target)
    _write_json(_ACTIVE_SELECTION, top_rows[0])

    # Record ALL K pairs as explored so future blank runs skip past them.
    # Recorded for both modes; INSERT OR IGNORE makes duplicate records safe.
    try:
        from api import jobs_db
        for t in targets:
            jobs_db.record_explored(
                t["disease_name"],
                t["target_symbol"],
                job_id=state.get("job_id"),
            )
    except Exception as e:  # persistence is best-effort; never fail the run on it
        print(f"[graph] target_selection: WARN could not record explored pairs: {e}")

    for t in targets:
        print(f"[graph] target: {t['target_symbol']} ({t.get('uniprot_id')}) "
              f"for {t['disease_name']} "
              f"[tractability={t.get('tractability_score')}, "
              f"unmet_need={t.get('unmet_need_score')}]")
    print(f"[graph] target_selection: pursuing {len(targets)} target(s) in parallel "
          f"(TOP_K_TARGETS={TOP_K_TARGETS})")
    return {"target": target, "targets": targets}


def biologist_node(state: PipelineState) -> dict[str, Any]:
    # Live API jobs always carry a job_id; never reuse shared output files across
    # different jobs — two concurrent runs write to the same paths and would
    # silently cross-contaminate. File-cache reuse is retained only for the
    # CLI standalone path (no job_id) where a single sequential user controls it.
    fresh = FORCE_RECOMPUTE or bool(state.get("job_id"))
    targets = state.get("targets") or [state["target"]]

    if len(targets) == 1 and not fresh:
        existing = _load_json("biologist_output.json")
        if existing is not None:
            print("[graph] biologist: reusing existing biologist_output.json")
            return {"biologist_output": existing, "biologist_outputs": [existing]}

    # A fresh Stage 2 pass starts the shared provenance log at the biologist.
    # NOTE: for K > 1 parallel runs each thread calls provenance.log_many()
    # concurrently; the JSON file may contain only the last-writer's entries.
    # This is acceptable since provenance is an audit aid, not scoring-critical.
    provenance.reset()

    if len(targets) == 1:
        out = run_biologist(targets[0])
        _write_json("biologist_output.json", out)
        print(f"[graph] biologist: {len(out['interacting_genes'])} interactors, "
              f"{len(out['literature_hits'])} literature hits")
        return {"biologist_output": out, "biologist_outputs": [out]}

    # K > 1: run all biologist tasks in parallel.
    print(f"[graph] biologist: running {len(targets)} target(s) in parallel")
    outputs: list[Optional[dict[str, Any]]] = [None] * len(targets)
    with ThreadPoolExecutor(max_workers=len(targets)) as exe:
        future_to_idx = {
            exe.submit(run_biologist, t): i for i, t in enumerate(targets)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                outputs[idx] = fut.result()
                t = targets[idx]
                o = outputs[idx]
                print(f"[graph] biologist [{t['target_symbol']}]: "
                      f"{len(o.get('interacting_genes', []))} interactors, "
                      f"{len(o.get('literature_hits', []))} literature hits, "
                      f"{len(o.get('pathway_neighbor_targets', []))} pathway neighbors")
            except Exception as e:
                print(f"[graph] biologist [{targets[idx]['target_symbol']}] "
                      f"FAILED: {e}")
                outputs[idx] = {
                    "target": targets[idx],
                    "interacting_genes": [], "literature_hits": [],
                    "pathway_neighbor_targets": [],
                    "druggability_context": {},
                    "error": str(e),
                }

    bio_outputs: list[dict[str, Any]] = [o for o in outputs if o is not None]
    n_bio_failed = sum(1 for o in bio_outputs if o.get("error"))
    n_bio_ok = len(bio_outputs) - n_bio_failed
    print(f"[graph] biologist: {n_bio_ok}/{len(targets)} target(s) succeeded "
          + (f"({n_bio_failed} failed with empty stubs)" if n_bio_failed else ""))
    primary_bio = bio_outputs[0] if bio_outputs else {}
    return {
        "biologist_output": primary_bio,
        "biologist_outputs": bio_outputs,
        "k_bio_failed": n_bio_failed,
    }


def chemist_node(state: PipelineState) -> dict[str, Any]:
    fresh = FORCE_RECOMPUTE or bool(state.get("job_id"))
    targets = state.get("targets") or [state["target"]]
    bio_outputs = state.get("biologist_outputs") or [state["biologist_output"]]
    # Guard: align lengths (truncate to shorter of the two)
    k = min(len(targets), len(bio_outputs))

    if k == 1 and not fresh:
        existing = _load_json("chemist_output.json")
        if existing is not None:
            print("[graph] chemist: reusing existing chemist_output.json")
            return {"chemist_output": existing}

    # Repurposing-only pool defaults ON for live API jobs (job_id set) and OFF
    # for the CLI/standalone path, unless explicitly overridden in state.
    repurposing_only = state.get("repurposing_only", bool(state.get("job_id")))

    if k == 1:
        out = run_chemist(bio_outputs[0], repurposing_only=repurposing_only)
        # Runtime schema validation: catch field-dropout bugs at the boundary
        # rather than discovering them in a downstream report.
        validate_chemist_handoff(out.get("candidates", []))
        _write_json("chemist_output.json", out)
        print(f"[graph] chemist: {len(out['candidates'])} candidates ranked")
        return {"chemist_output": out}

    # K > 1: run all chemist tasks in parallel, then pool candidates.
    print(f"[graph] chemist: running {k} target(s) in parallel")
    chemist_results: list[Optional[dict[str, Any]]] = [None] * k
    with ThreadPoolExecutor(max_workers=k) as exe:
        future_to_idx = {
            exe.submit(run_chemist, bio_outputs[i],
                       repurposing_only=repurposing_only): i
            for i in range(k)
        }
        for fut in as_completed(future_to_idx):
            idx = future_to_idx[fut]
            try:
                chemist_results[idx] = fut.result()
                sym = targets[idx]["target_symbol"] if idx < len(targets) else str(idx)
                n = len((chemist_results[idx] or {}).get("candidates", []))
                print(f"[graph] chemist [{sym}]: {n} candidate(s)")
            except Exception as e:
                sym = targets[idx]["target_symbol"] if idx < len(targets) else str(idx)
                print(f"[graph] chemist [{sym}] FAILED: {e}")
                chemist_results[idx] = {
                    "target": targets[idx],
                    "candidates": [],
                    "error": str(e),
                }

    # Pool all candidates from all K targets into a single chemist_output.
    all_candidates: list[dict[str, Any]] = []
    has_any_pooled = False
    for res in chemist_results:
        if res:
            cands = res.get("candidates", [])
            all_candidates.extend(cands)
            if res.get("pooled_across_multiple_targets"):
                has_any_pooled = True

    # Active-moiety union across all pursued targets. This collapses salt forms
    # and cross-source/cross-target duplicates by structure and evidence lineage
    # while preserving every target membership in the ledger.
    all_candidates = merge_chemist_candidates(all_candidates)

    # Track which targets succeeded (had no error AND produced a result object).
    n_chem_failed = sum(
        1 for r in chemist_results if r and r.get("error")
    )
    # Biologist failures (empty stubs injected above) also count as partial failures.
    n_bio_failed = state.get("k_bio_failed", 0)
    n_any_failed = max(n_chem_failed, n_bio_failed)
    n_ok = k - n_any_failed
    failed_syms = [
        targets[i].get("target_symbol", str(i))
        for i, r in enumerate(chemist_results)
        if r and r.get("error")
    ]
    k_target_summary = {
        "k_requested": TOP_K_TARGETS,
        "k_pursued": k,
        "k_succeeded": n_ok,
        "k_failed": n_any_failed,
        "failed_targets": failed_syms,
        "note": (
            f"{n_ok} of {k} target(s) successfully evaluated"
            + (f"; {n_any_failed} failed: {failed_syms}." if n_any_failed else ".")
        ),
    }
    print(f"[graph] chemist: {k_target_summary['note']}")

    total_approved_fps = sum(
        r.get("approved_reference_set_size", 0)
        for r in chemist_results if r
    )
    pooled_out = {
        "target": bio_outputs[0]["target"],
        "targets": [bio_outputs[i]["target"] for i in range(k)],
        "candidates": all_candidates,
        "pooled_across_k_targets": True,
        "k_targets": k,
        "k_target_summary": k_target_summary,
        "repurposing_only": repurposing_only,
        "pooled_across_multiple_targets": has_any_pooled,
        "approved_reference_set_size": total_approved_fps,
        "source_status": {
            f"target_{i + 1}_{targets[i].get('target_symbol', i)}": (
                (chemist_results[i] or {}).get("source_status", {})
            )
            for i in range(k)
        },
        "reference_set_note": (
            f"Candidates pooled from {k} targets for the same disease "
            f"(TOP_K_TARGETS={TOP_K_TARGETS}). Pathway-neighbor candidates "
            "tagged target_discovery_method='pathway_neighbor'."
        ),
    }
    # Runtime schema validation across the pooled K-target candidate list.
    validate_chemist_handoff(all_candidates)
    _write_json("chemist_output.json", pooled_out)
    print(f"[graph] chemist: pooled {len(all_candidates)} total candidate(s) "
          f"from {k} target(s)")
    return {"chemist_output": pooled_out}


def reviewer_node(state: PipelineState) -> dict[str, Any]:
    fresh = FORCE_RECOMPUTE or bool(state.get("job_id"))
    existing = None if fresh else _load_json("reviewed_candidates.json")
    if existing is not None:
        print("[graph] reviewer: reusing existing reviewed_candidates.json")
        return {"reviewed": existing}
    reviewed = run_reviewer(state["chemist_output"], state.get("biologist_output"))
    # Runtime schema validation at the reviewer→writer handoff.
    validate_reviewer_handoff(reviewed)
    payload = {
        "formula": {
            "composite_weights": COMPOSITE_WEIGHTS,
            "lipinski_penalty": LIPINSKI_PENALTY,
            "strong_match_threshold": STRONG_MATCH_THRESHOLD,
            "normalization": (
                f"pChEMBL: fixed range [{PCHEMBL_NORM_MIN}, {PCHEMBL_NORM_MAX}] "
                "(pharmacological reference — run-independent); "
                "Tanimoto: direct [0, 1] — no normalization applied; "
                "OT association: direct [0, 1] — no normalization applied"
            ),
            "pchembl_norm_min": PCHEMBL_NORM_MIN,
            "pchembl_norm_max": PCHEMBL_NORM_MAX,
        },
        "n_candidates": len(reviewed),
        "n_strong_matches": sum(1 for r in reviewed if r["strong_match"]),
        "repurposing_only": state["chemist_output"].get("repurposing_only", False),
        "candidates": reviewed,
    }
    # Thread the K-target evaluation summary through to the writer so it can
    # include a visible "N of K targets successfully evaluated" note in reports.
    k_summary = state["chemist_output"].get("k_target_summary")
    if k_summary:
        payload["k_target_summary"] = k_summary
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
    selected = _select_candidates(reviewed)

    if not selected:
        print("[graph] structure_validation: no STRONG_MATCH candidates and "
              "STAGE3_STRONG_ONLY=1 — nothing to validate")
        return {"selected": [], "structure_results": {}}

    # COST GUARDRAIL: when pursuing K > 1 targets, hard-cap at 1 Boltz call
    # (the single top-ranked candidate).  With K=1 the existing MAX_STRUCTURE_CANDIDATES
    # cap applies.  This prevents a K×MAX cost spike on every run.
    targets = state.get("targets") or [state.get("target", {})]
    if len(targets) > 1 and len(selected) > 1:
        print(f"[graph] structure_validation: K={len(targets)} targets — "
              f"applying cost guardrail: capping Boltz predictions to 1 "
              f"(was {len(selected)} candidates)")
        selected = selected[:1]

    # AFDB apo pre-check: use each candidate's own uniprot_id because with K targets
    # pooled candidates may come from different target proteins.
    structure_results: dict[str, Any] = {}
    for cand in selected:
        drug = cand.get("drug_name", "unknown")
        smiles = cand.get("smiles")
        cand_uniprot = cand.get("uniprot_id") or state.get("target", {}).get("uniprot_id")

        afdb: dict[str, Any] = {"has_structure": False, "mean_plddt": None, "model_url": None}
        if cand_uniprot:
            a = get_structure_confidence(cand_uniprot)
            afdb = {
                "has_structure": a.get("has_structure", False),
                "mean_plddt": a.get("mean_pLDDT"),
                "model_url": a.get("model_url"),
            }
            print(f"[graph] AFDB apo pre-check {cand_uniprot} ({cand.get('target_symbol','?')}): "
                  f"has_structure={afdb['has_structure']} mean_pLDDT={afdb['mean_plddt']} "
                  f"(apo only — Boltz still called for complex)")

        sequence = get_protein_sequence(cand_uniprot) if cand_uniprot else None
        if not sequence:
            print(f"[graph] WARNING: no protein sequence for {cand_uniprot}; "
                  f"Boltz complex prediction will be skipped for {drug}")

        print(f"[graph] structure_validation: {drug} (smiles={str(smiles)[:32]}) "
              f"[target={cand.get('target_symbol','?')}, "
              f"disc={cand.get('target_discovery_method','?')}]")
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

    # For K > 1 targets, pass the biologist_outputs keyed by target identity so
    # build_report_markdown receives the matching biologist context per candidate.
    # Matching is UniProt-first (stable identifier), symbol second; the primary
    # biologist_output is the last-resort fallback (e.g. pathway-neighbor
    # candidates with no biologist run) and is ALWAYS logged loudly, because a
    # silent fallback would stamp the wrong target's druggability context into
    # the dossier (F3, validation/target_selection_diagnosis.md).
    bio_outputs_list = state.get("biologist_outputs") or []
    bio_map: dict[str, dict[str, Any]] = {}
    bio_map_uid: dict[str, dict[str, Any]] = {}
    for bio in bio_outputs_list:
        t = bio.get("target") or {}
        sym = t.get("target_symbol")
        uid = t.get("uniprot_id")
        if sym:
            bio_map[sym] = bio
        if uid:
            bio_map_uid[uid] = bio
    primary_bio = state.get("biologist_output")

    def _bio_for(cand: dict) -> Optional[dict]:
        sym = cand.get("target_symbol")
        uid = cand.get("uniprot_id")
        by_sym = bio_map.get(sym) if sym else None
        by_uid = bio_map_uid.get(uid) if uid else None
        if by_sym is not None and by_uid is not None and by_sym is not by_uid:
            # Symbol and UniProt disagree — trust the stable identifier and
            # make the disagreement visible rather than silently picking one.
            print(f"[graph] writer WARNING: biologist-output conflict for "
                  f"candidate {sym}/{uid} — using UniProt match")
            return by_uid
        match = by_uid or by_sym
        if match is None:
            print(f"[graph] writer WARNING: no biologist output matches candidate "
                  f"target {sym}/{uid} — falling back to PRIMARY target context; "
                  f"dossier druggability context may describe the wrong target")
        return match or primary_bio

    reports = writer.run_writer(
        reviewed, selected, structure_results, primary_bio,
        state.get("target"), bio_for_candidate=_bio_for,
        k_target_summary=reviewed.get("k_target_summary"))
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
