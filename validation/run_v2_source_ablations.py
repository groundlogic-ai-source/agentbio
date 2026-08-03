"""
V2 source-ablation control harness — provider-level contribution measurement.

This is a CONTROL HARNESS, not a benchmark and not a production ranking.  It
runs the SAME 13 confirmed small-molecule repurposing cases as
``validation/run_repodb_cases_smallmol.py`` through the UPGRADED production
semantics under four source conditions, holding every ranking weight,
threshold, candidate cap, target cap and Reviewer behavior FIXED across
conditions.  Only the set of enabled candidate sources changes:

    * chembl-only
    * chembl + gtopdb
    * chembl + drugcentral
    * all-three (chembl + gtopdb + drugcentral)

Production semantics reused per case (identical to the engineering acceptance
runner, whose pure helpers are imported here rather than re-implemented):

  * DISEASE-ONLY target selection (`select_for_disease`); the confirmed drug is
    NEVER passed into target selection or any source-collection call.
  * Up to a fixed target cap (default 10) source-diverse target rows are run.
  * Every selected target is run (biologist -> chemist).
  * Candidate union by active moiety across all targets (`merge_chemist_candidates`).
  * ONE final Reviewer ranking over the union.
  * Holdout active for the confirmed drug (indication-precedent sealed, generic
    target pharmacology retained) during the whole run.
  * Post-run active-moiety matching (InChIKey block first, name fallback).

Protocol seals (each unit-tested in validation/test_v2_source_ablations.py):

  * The suite label is ALWAYS ``source_ablation_control``.  It is NEVER
    ``benchmark_v2`` / ``benchmark-freeze-v2``.  Being invoked under a benchmark
    label — or while a ``benchmark-freeze-v2`` git tag exists — is a HARD
    REFUSAL.  This harness NEVER creates a freeze/tag.
  * Per-condition source/config/code FINGERPRINT: a resume is refused when the
    condition set, config, or pipeline source bytes changed (unless --fresh).
  * Holdout SELF-AUDIT of every matched row: ``trials_holdout_redacted`` must be
    True and ``score_components.no_failed_trial`` must be None (the term is
    dropped as a coverage gap, so the redacted drug is neither credited nor
    penalised).  A row failing that is marked invalid/error and NOT counted as
    generated/valid/hit.

Grouping: evaluation is grouped by NORMALIZED DRUG (confirmed active moiety) so
a drug appearing under two diseases (e.g. Imatinib) cannot inflate provider-level
unique-drug conclusions.  Pair-level rows are preserved too.

Candidate precision / false-positive rate: this dataset has NO defensible
candidate-level NEGATIVE labels (a candidate absent from the confirmed set is
not a proven true negative).  Candidate precision / added-false-positive rate is
therefore reported as NOT ESTIMABLE rather than invented.

Usage:
    python3 -m validation.run_v2_source_ablations [--cap N] [--fresh]
                                                  [--conditions a,b,...]

Refuses (exit 2) if invoked as benchmark_v2 or if benchmark-freeze-v2 exists.
Does NOT run benchmark v2 and NEVER creates benchmark-freeze-v2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.target_selection import (
    select_for_disease,
    select_source_diverse_targets,
    DiseaseNotInUniverse,
)
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from agents.reviewer import run_reviewer, STRONG_MATCH_THRESHOLD
from data_sources.multisource_candidates import merge_chemist_candidates
from data_sources import holdout as holdout_mod

# Reuse the vetted pure helpers from the engineering acceptance runner rather
# than re-implementing (and risking drift from) production match / validity /
# health semantics.
from validation.run_v2_engineering_acceptance import (
    _norm_name,
    _inchikey_block,
    _resolve_confirmed_inchikey_block,
    _name_match,
    match_active_moiety,
    classify_mechanistic_validity,
    _row_to_target,
    _source_health_from_reviewed,
    _source_health_from_target_results,
    FORBIDDEN_FREEZE_TAG,
    _benchmark_v2_freeze_exists,
)
# The 13 confirmed small-molecule cases (single source of truth).
from validation.run_repodb_cases_smallmol import TARGET_CASES

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Labels & refusal invariants ──────────────────────────────────────────────
LABEL = "source_ablation_control"
FORBIDDEN_LABELS = frozenset(
    {"benchmark_v2", "benchmark-freeze-v2", "benchmark_v2_run"}
)

RESULTS_JSON = os.path.join(VALIDATION_DIR, "v2_source_ablation_results.json")
RESULTS_MD = os.path.join(VALIDATION_DIR, "v2_source_ablation_report.md")

DEFAULT_TARGET_CAP = 10
TOP_N = 10

# ── Source conditions ────────────────────────────────────────────────────────
# The four conditions.  ChEMBL is present in EVERY condition and is the baseline
# for incremental measurement.  Order is stable and meaningful for the report.
BASELINE_CONDITION = "chembl_only"
CONDITIONS: dict[str, tuple[str, ...]] = {
    "chembl_only": ("chembl",),
    "chembl_gtopdb": ("chembl", "gtopdb"),
    "chembl_drugcentral": ("chembl", "drugcentral"),
    "all_three": ("chembl", "gtopdb", "drugcentral"),
}

# Candidate precision / added-false-positive statement (single source of truth):
# no defensible candidate-level negative labels exist in this dataset.
CANDIDATE_PRECISION_STATEMENT = (
    "NOT ESTIMABLE: this dataset has no defensible candidate-level negative "
    "labels. A candidate not in the confirmed repurposing set is not a proven "
    "true negative, so candidate precision and added-false-positive rate are "
    "intentionally not reported rather than invented."
)

# Source files fingerprinted for the stale-resume guard.
_FINGERPRINT_SOURCES = [
    "agents/target_selection.py",
    "agents/biologist.py",
    "agents/chemist.py",
    "agents/reviewer.py",
    "data_sources/holdout.py",
    "data_sources/evidence_ledger.py",
    "data_sources/multisource_candidates.py",
    "data_sources/gtopdb.py",
    "data_sources/drugcentral_v2.py",
    "data_sources/europepmc_mechanisms.py",
    "data_sources/clinicaltrials.py",
    "data_sources/mechanism_direction.py",
    "data_sources/safety_check.py",
    "data_sources/pubchem.py",
    "data_sources/chembl.py",
    "validation/run_v2_engineering_acceptance.py",
    "validation/run_repodb_cases_smallmol.py",
    os.path.relpath(os.path.abspath(__file__), _REPO_ROOT),
]


def _log(msg: str) -> None:
    print(f"[src-ablation] {msg}", flush=True)


# ── Refusal gate ──────────────────────────────────────────────────────────────

def assert_not_benchmark(label: str) -> None:
    """Refuse under any benchmark label or when the v2 freeze tag exists.

    The source-ablation control must never be reported as (or run under)
    benchmark v2, and must not proceed once the real v2 benchmark is frozen.
    This harness NEVER creates a freeze/tag.  Raises RuntimeError on violation.
    """
    if label != LABEL:
        raise RuntimeError(
            f"REFUSED: source-ablation control runs ONLY under label "
            f"'{LABEL}'; got '{label}'. This harness must never be reported as "
            f"benchmark v2."
        )
    if label in FORBIDDEN_LABELS:  # defensive; unreachable given the check above
        raise RuntimeError(f"REFUSED: '{label}' is a forbidden benchmark label.")
    if _benchmark_v2_freeze_exists():
        raise RuntimeError(
            f"REFUSED: git tag '{FORBIDDEN_FREEZE_TAG}' exists — the v2 "
            f"benchmark is frozen. The source-ablation control must not run "
            f"afterwards. This harness NEVER creates a freeze/tag."
        )


# ── Fingerprint (conditions + config + source code) ──────────────────────────

def config_source_fingerprint(cap: int, conditions: tuple[str, ...]) -> str:
    """Deterministic fingerprint of conditions + runner config + source bytes."""
    h = hashlib.sha256()
    h.update(f"label={LABEL}".encode())
    h.update(f"cap={cap}".encode())
    h.update(f"top_n={TOP_N}".encode())
    h.update(f"strong_match_threshold={STRONG_MATCH_THRESHOLD}".encode())
    h.update(("conditions=" + repr(sorted(conditions))).encode())
    for cond in sorted(conditions):
        h.update(f"\x00cond:{cond}={repr(CONDITIONS[cond])}\x00".encode())
    h.update(("cases=" + repr(TARGET_CASES)).encode())
    for rel in _FINGERPRINT_SOURCES:
        path = os.path.join(_REPO_ROOT, rel)
        h.update(f"\x00{rel}\x00".encode())
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


# ── Holdout self-audit ───────────────────────────────────────────────────────

def holdout_self_audit(matched_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Audit a matched row for holdout integrity.

    Requires ``trials_holdout_redacted`` True AND
    ``score_components.no_failed_trial`` is None.  Any matched row that fails
    this is INVALID and must not be counted as generated/valid/hit.

    None (not 0) is the correct "no credit" value: a redacted trial lookup is
    an unmade observation, so the Reviewer drops the term from BOTH sides of
    the composite instead of scoring it as a failed trial.  A literal 0 here
    would mean the redacted drug was actively penalised for evidence the
    harness deliberately hid, which is the artifact this audit guards against.
    """
    result = {"ok": False, "trials_holdout_redacted": None,
              "no_failed_trial": None, "reason": None}
    if not matched_row:
        result["reason"] = "no matched row to audit"
        return result
    redacted = bool(matched_row.get("trials_holdout_redacted"))
    components = matched_row.get("score_components") or {}
    no_failed = components.get("no_failed_trial")
    result["trials_holdout_redacted"] = redacted
    result["no_failed_trial"] = no_failed
    if not redacted:
        result["reason"] = "trials_holdout_redacted is not True"
        return result
    # An ABSENT key is unverifiable and must fail, which is distinct from an
    # explicit None (the term was correctly dropped as a coverage gap).
    if "no_failed_trial" not in components:
        result["reason"] = "score_components.no_failed_trial is missing"
        return result
    if no_failed is not None:
        result["reason"] = (
            f"score_components.no_failed_trial={no_failed} (expected None — an "
            "unobserved trial lookup must be a coverage gap, never a score)"
        )
        return result
    result["ok"] = True
    result["reason"] = (
        "holdout redacted; trial term dropped as a coverage gap "
        "(no credit given, no penalty imposed)"
    )
    return result


# ── Frozen per-case target selection snapshot ────────────────────────────────
# CRITICAL: target selection must run ONCE per (drug, disease) case, under that
# case's ACTIVE holdout (approved-drug precedent redaction can change selected
# rows), and be reused BYTE-FOR-BYTE across all four source conditions. Each
# case therefore gets a deterministic ``target_input_hash`` over its frozen
# selected rows + cap + disease; the hash is stored once at the case level and
# echoed on every pair-condition row so any drift is detectable.

def _case_key(drug: str, disease: str) -> str:
    """Stable per-case snapshot key (normalized drug + disease)."""
    return f"{_norm_name(drug)}::{_norm_name(disease)}"


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON serialization for hashing (sorted keys, no spaces)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def target_input_hash(disease_name: str, cap: int,
                      selected_rows: list[dict[str, Any]]) -> str:
    """Deterministic hash of the frozen selection input shared by all arms.

    Covers the disease, the cap, and the full frozen selected target rows
    (including process metadata / status). Any change to the selected rows —
    e.g. because target selection was recomputed OUTSIDE the correct holdout —
    yields a different hash, which the resume guard refuses.
    """
    payload = {
        "disease_name": disease_name,
        "cap": cap,
        "selected_rows": selected_rows,
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def freeze_case_targets(drug_name: str, disease_name: str,
                        cap: int) -> dict[str, Any]:
    """Run target selection ONCE for a case, UNDER ITS ACTIVE HOLDOUT, and freeze.

    Returns a snapshot dict persisted at the root JSON level::

        {
          "case_key", "drug_name", "disease_name", "cap",
          "in_universe", "status", "error",
          "selected_rows": [ <full frozen target row>, ... ],
          "target_input_hash",
          "holdout_drugs", "holdout_active",
        }

    The caller MUST have the confirmed drug's holdout active before calling
    this (asserted here) so approved-drug precedent redaction is applied to the
    selected rows exactly once, and the identical frozen rows feed every arm.
    """
    if not holdout_mod.is_active():
        raise RuntimeError(
            "freeze_case_targets called without an active holdout; target "
            "selection MUST run under the case's holdout so approved-drug "
            "precedent redaction affects the frozen rows."
        )
    snapshot: dict[str, Any] = {
        "case_key": _case_key(drug_name, disease_name),
        "drug_name": drug_name,
        "disease_name": disease_name,
        "cap": cap,
        "in_universe": None,
        "status": None,
        "error": None,
        "selected_rows": [],
        "target_input_hash": None,
        "holdout_drugs": list(holdout_mod.drugs()),
        "holdout_active": True,
    }
    try:
        rows = select_for_disease(disease_name)
    except DiseaseNotInUniverse as e:
        snapshot.update(status="out_of_scope", in_universe=False, error=str(e))
        snapshot["target_input_hash"] = target_input_hash(disease_name, cap, [])
        _log(f"  OUT OF SCOPE (frozen): {e}")
        return snapshot
    except Exception as e:  # pragma: no cover - network/runtime variance
        snapshot.update(status="error", in_universe=True, error=str(e))
        snapshot["target_input_hash"] = target_input_hash(disease_name, cap, [])
        _log(f"  ERROR in target_selection (frozen): {e}")
        return snapshot

    # Freeze the exact source-diverse selection (rows + process metadata/status)
    # once. Every arm reuses THIS list.
    selected_rows = select_source_diverse_targets(rows, cap)
    snapshot["in_universe"] = True
    snapshot["status"] = "ok"
    snapshot["selected_rows"] = selected_rows
    snapshot["target_input_hash"] = target_input_hash(
        disease_name, cap, selected_rows)
    _log(f"  FROZEN selection: {len(selected_rows)} row(s) "
         f"hash={snapshot['target_input_hash'][:12]}…")
    return snapshot


def validate_snapshot(snapshot: dict[str, Any], cap: int) -> None:
    """Validate a persisted frozen snapshot; raise RuntimeError if unusable.

    A resume must refuse when the required frozen input is missing/malformed or
    when its recomputed stored hash does not match the persisted one — a stored
    snapshot can never be silently trusted.
    """
    if not isinstance(snapshot, dict):
        raise RuntimeError("frozen snapshot is malformed (not an object)")
    for field in ("disease_name", "target_input_hash", "status"):
        if field not in snapshot:
            raise RuntimeError(
                f"frozen snapshot missing required field '{field}'")
    stored_hash = snapshot.get("target_input_hash")
    if not stored_hash:
        raise RuntimeError("frozen snapshot missing target_input_hash")
    rows = snapshot.get("selected_rows")
    if rows is None or not isinstance(rows, list):
        raise RuntimeError("frozen snapshot has missing/malformed selected_rows")
    recomputed = target_input_hash(
        snapshot.get("disease_name"), cap, rows)
    if recomputed != stored_hash:
        raise RuntimeError(
            "TAMPERED SNAPSHOT REFUSED: recomputed target_input_hash "
            f"({recomputed[:12]}…) does not match stored value "
            f"({str(stored_hash)[:12]}…)."
        )


# ── Per-target execution (disease-driven; drug name NEVER passed in) ─────────

def _run_one_target(row: dict[str, Any], enabled: tuple[str, ...]) -> dict[str, Any]:
    """Run biologist -> chemist for one disease-selected target row.

    The confirmed drug name is NEVER passed in; only the disease-derived target
    drives collection.  ``enabled`` selects the candidate sources for this
    condition and is forwarded to the Chemist.
    """
    target = _row_to_target(row)
    rec: dict[str, Any] = {
        "target_symbol": target["target_symbol"],
        "uniprot_id": target["uniprot_id"],
        "ot_association_score": target["ot_association_score"],
        "target_discovery_method": target["target_discovery_method"],
        "status": None,
        "n_chemist_candidates": 0,
        "error": None,
        "_chemist_candidates": [],
        "_biologist_output": None,
        "source_status": {},
    }
    try:
        bio = run_biologist(target)
        chem = run_chemist(bio, repurposing_only=True,
                           enabled_sources=list(enabled))
    except Exception as e:  # pragma: no cover - network/runtime variance
        rec.update(status="error", error=str(e))
        _log(f"    ERROR pipeline: {e}")
        return rec

    chem_cands = chem.get("candidates", [])
    rec["n_chemist_candidates"] = len(chem_cands)
    rec["_chemist_candidates"] = chem_cands
    rec["_biologist_output"] = bio
    rec["source_status"] = dict(chem.get("source_status") or {})
    process_state = target.get("process_source_status")
    if process_state:
        rec["source_status"]["europepmc"] = {
            "status": process_state,
            "error": None,
            "release": target.get("process_ontology_version"),
        }
    rec["status"] = "ok"
    _log(f"    chemist candidates: {len(chem_cands)}")
    return rec


def run_pair_condition(drug_name: str, disease_name: str, condition: str,
                       cap: int, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Run one (drug, disease) pair under one source condition.

    Uses the full upgraded production semantics.  Target selection is NOT
    performed here — the caller passes the case's FROZEN ``snapshot`` (produced
    once, under the case's active holdout, by :func:`freeze_case_targets`) and
    this function reuses those exact rows for every arm.  The confirmed
    ``drug_name`` is used ONLY (a) to seal holdout (by the caller) and (b) for
    post-run active-moiety matching here — never in source collection.  Disease-
    only semantics are preserved: the drug never reaches selection or collection.

    This function may assume its input was selected under the active holdout.
    """
    enabled = CONDITIONS[condition]
    result: dict[str, Any] = {
        "label": LABEL,
        "condition": condition,
        "enabled_sources": list(enabled),
        "drug_name": drug_name,
        "drug_key": _norm_name(drug_name),
        "disease_name": disease_name,
        "target_cap": cap,
        "target_input_hash": snapshot.get("target_input_hash"),
        "in_universe": None,
        "status": None,
        "n_targets_run": 0,
        "n_union_candidates": None,
        "generated": False,
        "mechanistically_valid": False,
        "mechanistic_validity_detail": None,
        "rank": None,
        "total_candidates": None,
        "top10": False,
        "strong_match": None,
        "match_method": None,
        "generated_by_target": None,
        "trials_holdout_redacted": None,
        "score_components": None,
        "holdout_audit": None,
        "source_providers": [],
        "source_health": {},
        "holdout_active": None,
        "holdout_drugs": None,
        "holdout_unresolved": None,
        "per_target_results": [],
        "error": None,
    }

    # ── 1. Reuse the case's FROZEN target selection (NO select_for_disease) ──
    # The snapshot was produced once, under this case's active holdout, and is
    # reused byte-for-byte by every arm so the ONLY thing that differs between
    # conditions is the enabled source set.
    if snapshot.get("status") == "out_of_scope":
        result.update(status="out_of_scope", in_universe=False,
                      error=snapshot.get("error"))
        _log(f"  [{condition}] OUT OF SCOPE (frozen)")
        return result
    if snapshot.get("status") == "error":
        result.update(status="error", in_universe=True,
                      error=snapshot.get("error"))
        _log(f"  [{condition}] ERROR (frozen selection): {snapshot.get('error')}")
        return result

    result["in_universe"] = True

    # ── 2. Run the frozen source-diverse target rows (already capped) ────────
    run_rows = snapshot.get("selected_rows") or []
    result["n_targets_run"] = len(run_rows)
    _log(f"  [{condition}] running {len(run_rows)} frozen target row(s) "
         f"(cap={cap}, hash={str(snapshot.get('target_input_hash'))[:12]}…)")

    per_target_public: list[dict[str, Any]] = []
    all_chemist: list[dict[str, Any]] = []
    generating_targets: dict[str, dict[str, Any]] = {}
    successful_bio: list[dict[str, Any]] = []

    for k_idx, row in enumerate(run_rows, 1):
        _log(f"  [target {k_idx}/{len(run_rows)}] {row.get('target_symbol')} "
             f"({row.get('uniprot_id')})")
        rec = _run_one_target(row, enabled)
        chem_cands = rec.pop("_chemist_candidates", [])
        bio_output = rec.pop("_biologist_output", None)
        all_chemist.extend(chem_cands)
        if bio_output:
            successful_bio.append(bio_output)

        generated_here = any(
            _name_match(drug_name, cand.get("drug_name", ""))
            for cand in chem_cands
        )
        rec["found"] = generated_here
        rec["match_method"] = "name" if generated_here else None
        per_target_public.append(rec)
        if generated_here:
            for cand in chem_cands:
                if _name_match(drug_name, cand.get("drug_name", "")):
                    identity = (cand.get("_evidence_ledger") or {}).get(
                        "identity") or cand.get("molecule_chembl_id") or \
                        _norm_name(cand.get("drug_name"))
                    generating_targets.setdefault(identity, {
                        "target_symbol": row.get("target_symbol"),
                        "uniprot_id": row.get("uniprot_id"),
                        "target_rank": k_idx,
                        "target_discovery_method": row.get(
                            "target_discovery_method"),
                    })

    result["per_target_results"] = per_target_public

    # ── 3. Active-moiety union + ONE final Reviewer ranking ──────────────────
    pooled_candidates = merge_chemist_candidates(all_chemist)
    result["n_union_candidates"] = len(pooled_candidates)
    pooled_output = {
        "target": _row_to_target(run_rows[0]) if run_rows else {},
        "targets": [_row_to_target(row) for row in run_rows],
        "candidates": pooled_candidates,
        "pooled_across_k_targets": True,
        "k_targets": len(run_rows),
        "repurposing_only": True,
    }
    try:
        final_reviewed = run_reviewer(
            pooled_output, successful_bio[0] if successful_bio else None)
    except Exception as e:  # pragma: no cover - network/runtime variance
        result.update(status="error", error=f"final pooled reviewer failed: {e}")
        return result

    result["source_health"] = _source_health_from_target_results(
        per_target_public)
    for provider, healthy in _source_health_from_reviewed(
            final_reviewed).items():
        result["source_health"][provider] = (
            result["source_health"].get(provider, False) or healthy
        )

    # Post-run confirmed-drug structure resolution ONLY (never during collection).
    confirmed_block = _resolve_confirmed_inchikey_block(drug_name)
    rank, matched, method = match_active_moiety(
        drug_name, pooled_candidates, final_reviewed, confirmed_block)

    if rank is None or matched is None:
        result.update(
            status="miss", generated=False, mechanistically_valid=False,
            mechanistic_validity_detail={
                "reason": "not generated in any target pool"},
        )
        _log(f"  [{condition}] MISS: {drug_name} not generated")
        return result

    # ── 4. Holdout self-audit BEFORE counting the row ────────────────────────
    audit = holdout_self_audit(matched)
    result["holdout_audit"] = audit
    result["trials_holdout_redacted"] = audit["trials_holdout_redacted"]
    result["score_components"] = matched.get("score_components")
    if not audit["ok"]:
        result.update(
            status="error", generated=False, mechanistically_valid=False,
            error=f"holdout self-audit failed: {audit['reason']}",
            mechanistic_validity_detail={
                "reason": f"invalid (holdout audit): {audit['reason']}"},
            rank=rank,
        )
        _log(f"  [{condition}] INVALID (holdout audit failed): {audit['reason']}")
        return result

    validity = classify_mechanistic_validity(matched)
    ledger = matched.get("_evidence_ledger") or {}
    identity = ledger.get("identity") or matched.get(
        "molecule_chembl_id") or _norm_name(matched.get("drug_name"))
    generated_by = generating_targets.get(identity)
    if generated_by is None:
        memberships = ledger.get("target_symbols") or []
        generated_by = {
            "target_symbol": (memberships[0] if memberships
                              else matched.get("target_symbol")),
            "uniprot_id": matched.get("uniprot_id"),
            "target_rank": None,
            "target_discovery_method": matched.get("target_discovery_method"),
        }

    result.update(
        status="hit",
        generated=True,
        mechanistically_valid=bool(validity.get("mechanistically_valid")),
        mechanistic_validity_detail=validity,
        rank=rank,
        total_candidates=len(final_reviewed),
        top10=rank <= TOP_N,
        strong_match=bool(matched.get("strong_match")),
        match_method=method,
        generated_by_target=generated_by,
        source_providers=sorted(ledger.get("providers") or []),
    )
    _log(f"  [{condition}] GENERATED: rank {rank}, "
         f"mech_valid={result['mechanistically_valid']}, "
         f"top10={result['top10']}, strong={result['strong_match']}")
    return result


# ── Aggregation (pair-level + unique-drug grouping) ──────────────────────────

def _counts_valid_hit(row: dict[str, Any]) -> bool:
    """A row is a countable HIT only if it generated AND passed holdout audit."""
    return row.get("status") == "hit" and bool(row.get("generated"))


def summarize_condition(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-condition pair-level + unique-drug summary for one condition."""
    scored = [r for r in rows if r.get("in_universe")]
    n_pairs = len(scored)

    pair_generated = sum(1 for r in scored if _counts_valid_hit(r))
    pair_valid = sum(1 for r in scored
                     if _counts_valid_hit(r) and r.get("mechanistically_valid"))
    pair_top10 = sum(1 for r in scored
                     if _counts_valid_hit(r) and r.get("top10"))
    pair_strong = sum(1 for r in scored
                      if _counts_valid_hit(r) and r.get("strong_match"))
    pair_invalid = sum(1 for r in scored if r.get("status") == "error"
                       and (r.get("holdout_audit") or {}).get("ok") is False)

    # Unique-drug grouping: a drug counts as generated/valid if ANY of its
    # pairs generated/valid — so a drug with two diseases (Imatinib) counts once.
    by_drug: dict[str, list[dict[str, Any]]] = {}
    for r in scored:
        by_drug.setdefault(r["drug_key"], []).append(r)
    unique_drugs = sorted(by_drug)
    drug_generated = sum(
        1 for k in unique_drugs
        if any(_counts_valid_hit(r) for r in by_drug[k]))
    drug_valid = sum(
        1 for k in unique_drugs
        if any(_counts_valid_hit(r) and r.get("mechanistically_valid")
               for r in by_drug[k]))

    # Added candidates: total union candidates across pairs for this condition.
    added_candidates = sum(int(r.get("n_union_candidates") or 0) for r in scored)

    return {
        "n_pairs": n_pairs,
        "n_unique_drugs": len(unique_drugs),
        "pair_generated_recall": pair_generated,
        "pair_mechanistically_valid": pair_valid,
        "pair_top10": pair_top10,
        "pair_strong": pair_strong,
        "pair_invalid_holdout": pair_invalid,
        "unique_drug_generated_recall": drug_generated,
        "unique_drug_mechanistically_valid": drug_valid,
        "total_union_candidates": added_candidates,
        # Sets used by incremental comparison vs baseline.
        "_generated_pair_keys": sorted(
            {(r["drug_key"], _norm_name(r["disease_name"]))
             for r in scored if _counts_valid_hit(r)}),
        "_generated_drug_keys": sorted(
            {k for k in unique_drugs
             if any(_counts_valid_hit(r) for r in by_drug[k])}),
    }


def incremental_vs_baseline(cond_summary: dict[str, Any],
                            baseline_summary: dict[str, Any]) -> dict[str, Any]:
    """Incremental recovered pairs/drugs and added candidates vs ChEMBL-only."""
    base_pairs = set(tuple(x) for x in baseline_summary["_generated_pair_keys"])
    cond_pairs = set(tuple(x) for x in cond_summary["_generated_pair_keys"])
    base_drugs = set(baseline_summary["_generated_drug_keys"])
    cond_drugs = set(cond_summary["_generated_drug_keys"])
    return {
        "incremental_recovered_pairs": sorted(
            f"{d}/{dis}" for d, dis in (cond_pairs - base_pairs)),
        "incremental_recovered_drugs": sorted(cond_drugs - base_drugs),
        "n_incremental_recovered_pairs": len(cond_pairs - base_pairs),
        "n_incremental_recovered_drugs": len(cond_drugs - base_drugs),
        "added_candidates_vs_baseline": (
            cond_summary["total_union_candidates"]
            - baseline_summary["total_union_candidates"]),
        "candidate_precision_added_false_positives": CANDIDATE_PRECISION_STATEMENT,
    }


# ── Persistence (incremental JSON + Markdown, fingerprint-guarded resume) ────

def _row_key(condition: str, drug: str, disease: str) -> tuple[str, str, str]:
    return (condition, _norm_name(drug), _norm_name(disease))


def _load_existing(
    fingerprint: str, cap: int,
) -> tuple[dict[tuple[str, str, str], dict[str, Any]],
           dict[str, dict[str, Any]]]:
    """Load prior rows + frozen snapshots ONLY if the fingerprint matches.

    Returns ``(done_rows, snapshots)``. Refuses (RuntimeError) when the
    fingerprint drifted, or when any persisted frozen snapshot is missing /
    malformed / fails its recomputed-hash check — a resume must never silently
    trust a stored snapshot or recompute selection for an incomplete arm.
    """
    if not os.path.exists(RESULTS_JSON):
        return {}, {}
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}, {}
    if data.get("label") != LABEL:
        _log("  existing results are not source_ablation_control-labelled; "
             "ignoring")
        return {}, {}
    if data.get("fingerprint") != fingerprint:
        raise RuntimeError(
            "STALE RESUME REFUSED: condition/config/source fingerprint changed "
            "since the existing partial run. Re-run with --fresh to start clean."
        )

    # Validate + index every persisted frozen snapshot before any reuse.
    snapshots: dict[str, dict[str, Any]] = {}
    for snap in data.get("target_snapshots", []):
        validate_snapshot(snap, cap)
        snapshots[snap["case_key"]] = snap

    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in data.get("rows", []):
        # A completed row must have a frozen snapshot to have been produced
        # correctly; refuse to trust a completed row without one.
        case_key = _case_key(r["drug_name"], r["disease_name"])
        if case_key not in snapshots:
            raise RuntimeError(
                "RESUME REFUSED: completed row for "
                f"{r['drug_name']}/{r['disease_name']} has no persisted frozen "
                "target snapshot; re-run with --fresh."
            )
        out[_row_key(r["condition"], r["drug_name"], r["disease_name"])] = r
    return out, snapshots


def _strip_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    out["per_target_results"] = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in row.get("per_target_results", [])
    ]
    return out


def _flush(rows: list[dict[str, Any]], snapshots: dict[str, dict[str, Any]],
           fingerprint: str, cap: int, conditions: tuple[str, ...],
           generated_at: str) -> None:
    # Frozen target snapshots are persisted ONCE at the root (not duplicated in
    # every row); each pair row echoes only the target_input_hash.
    payload = {
        "label": LABEL,
        "generated_at": generated_at,
        "fingerprint": fingerprint,
        "target_cap": cap,
        "top_n": TOP_N,
        "strong_match_threshold": STRONG_MATCH_THRESHOLD,
        "conditions": {c: list(CONDITIONS[c]) for c in conditions},
        "baseline_condition": BASELINE_CONDITION,
        "candidate_precision_statement": CANDIDATE_PRECISION_STATEMENT,
        "target_snapshots": [snapshots[k] for k in sorted(snapshots)],
        "rows": [_strip_row(r) for r in rows],
    }
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    _log(f"  saved {len(rows)} row(s), {len(snapshots)} snapshot(s) "
         f"-> {RESULTS_JSON}")


def build_markdown(rows: list[dict[str, Any]], cap: int,
                   conditions: tuple[str, ...], generated_at: str) -> str:
    by_cond: dict[str, list[dict[str, Any]]] = {c: [] for c in conditions}
    for r in rows:
        if r["condition"] in by_cond:
            by_cond[r["condition"]].append(r)

    summaries = {c: summarize_condition(by_cond[c]) for c in conditions}
    baseline = summaries.get(BASELINE_CONDITION)

    lines: list[str] = [
        "# V2 Source-Ablation Control",
        "",
        f"_Label: **{LABEL}** (NOT benchmark v2). Generated: {generated_at}._",
        "",
        "Same 13 confirmed small-molecule cases as "
        "`run_repodb_cases_smallmol.py`, run through upgraded production "
        "semantics (disease-only target selection, source-diverse target cap "
        f"**{cap}**, active-moiety union, one final Reviewer ranking, holdout "
        "active, post-run matching). Only the enabled candidate sources change "
        "between conditions; ranking weights, thresholds, caps and Reviewer "
        "behavior are FIXED.",
        "",
        f"**Candidate precision / added false positives:** "
        f"{CANDIDATE_PRECISION_STATEMENT}",
        "",
        "## Per-condition summary",
        "",
        "| Condition | Sources | Pair gen | Pair valid | Top-10 | Strong | "
        "Drug gen | Drug valid | Invalid(holdout) | +cands vs base | "
        "+pairs vs base | +drugs vs base |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in conditions:
        s = summaries[c]
        srcs = "+".join(CONDITIONS[c])
        if baseline is not None:
            inc = incremental_vs_baseline(s, baseline)
            add_c = inc["added_candidates_vs_baseline"]
            add_p = inc["n_incremental_recovered_pairs"]
            add_d = inc["n_incremental_recovered_drugs"]
        else:
            add_c = add_p = add_d = "—"
        lines.append(
            f"| {c} | {srcs} | "
            f"{s['pair_generated_recall']}/{s['n_pairs']} | "
            f"{s['pair_mechanistically_valid']}/{s['n_pairs']} | "
            f"{s['pair_top10']}/{s['n_pairs']} | "
            f"{s['pair_strong']}/{s['n_pairs']} | "
            f"{s['unique_drug_generated_recall']}/{s['n_unique_drugs']} | "
            f"{s['unique_drug_mechanistically_valid']}/{s['n_unique_drugs']} | "
            f"{s['pair_invalid_holdout']} | {add_c} | {add_p} | {add_d} |")

    lines += ["", "## Incremental detail vs ChEMBL-only", ""]
    for c in conditions:
        if c == BASELINE_CONDITION or baseline is None:
            continue
        inc = incremental_vs_baseline(summaries[c], baseline)
        lines.append(f"### {c}")
        lines.append(f"- recovered pairs vs baseline: "
                     f"{inc['incremental_recovered_pairs'] or '—'}")
        lines.append(f"- recovered drugs vs baseline: "
                     f"{inc['incremental_recovered_drugs'] or '—'}")
        lines.append(f"- added candidates vs baseline: "
                     f"{inc['added_candidates_vs_baseline']}")
        lines.append("")

    lines += ["## Pair-level rows (preserved)", "",
              "| Condition | Drug | Disease | Status | Gen | Valid | Rank | "
              "Top10 | Strong | Holdout OK | By target |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        gen = "✓" if _counts_valid_hit(r) else "—"
        mv = "✓" if (_counts_valid_hit(r) and r.get("mechanistically_valid")) \
            else "—"
        rank = r.get("rank") if r.get("rank") is not None else "—"
        t10 = "✓" if r.get("top10") else "—"
        sm = "✓" if r.get("strong_match") else "—"
        h_ok = "✓" if (r.get("holdout_audit") or {}).get("ok") else (
            "✗" if r.get("holdout_audit") else "—")
        by = (r.get("generated_by_target") or {}).get("target_symbol") or "—"
        lines.append(
            f"| {r['condition']} | {r['drug_name']} | {r['disease_name']} | "
            f"{r.get('status')} | {gen} | {mv} | {rank} | {t10} | {sm} | "
            f"{h_ok} | {by} |")

    lines.append("")
    return "\n".join(lines)


def run_all(cap: int, fresh: bool, conditions: tuple[str, ...],
            generated_at: str, *, label: str = LABEL) -> list[dict[str, Any]]:
    """Run CASE-MAJOR: per case, freeze targets once (under holdout), then run
    every source condition against that exact frozen snapshot.

    Refuses under benchmark label / v2 freeze. On resume, reuses persisted
    frozen snapshots and NEVER recomputes target selection for an incomplete
    arm when a snapshot exists.
    """
    assert_not_benchmark(label)
    fingerprint = config_source_fingerprint(cap, conditions)

    if fresh:
        for path in (RESULTS_JSON, RESULTS_MD):
            if os.path.exists(path):
                os.remove(path)
        done: dict[tuple[str, str, str], dict[str, Any]] = {}
        snapshots: dict[str, dict[str, Any]] = {}
        _log("--fresh: cleared any prior partial results/snapshots")
    else:
        done, snapshots = _load_existing(fingerprint, cap)
        _log(f"resume: {len(done)} fingerprint-matched row(s), "
             f"{len(snapshots)} snapshot(s) already persisted")

    # Preserve report ordering: emit rows condition-major even though we EXECUTE
    # case-major. rows_by_key lets us reassemble in condition-major order.
    rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = dict(done)

    # ── CASE-MAJOR execution ────────────────────────────────────────────────
    for _csv_idx, drug, disease, _csv_disease in TARGET_CASES:
        case_key = _case_key(drug, disease)

        # Freeze target selection ONCE per case, UNDER THIS CASE'S HOLDOUT.
        # On resume, reuse the persisted (already hash-validated) snapshot and
        # never recompute selection for an incomplete arm.
        with holdout_mod.holdout_active([drug]):
            if case_key in snapshots:
                snapshot = snapshots[case_key]
                # Defensive: revalidate the reused snapshot's stored hash.
                validate_snapshot(snapshot, cap)
                _log(f"=== CASE {drug} / {disease}: reusing FROZEN snapshot "
                     f"hash={str(snapshot.get('target_input_hash'))[:12]}… ===")
            else:
                _log(f"=== CASE {drug} / {disease} "
                     f"[holdout seals: {[drug]}] — freezing selection ===")
                snapshot = freeze_case_targets(drug, disease, cap)
                snapshots[case_key] = snapshot
                _flush(list(rows_by_key.values()), snapshots, fingerprint, cap,
                       conditions, generated_at)

            # Run every arm against the EXACT frozen snapshot, still inside the
            # active holdout so collection/scoring see the sealed context.
            for condition in conditions:
                key = _row_key(condition, drug, disease)
                if key in done:
                    _log(f"  SKIP (done): [{condition}] {drug} / {disease}")
                    continue
                _log(f"--- [{condition}] {drug} / {disease} "
                     f"(hash={str(snapshot.get('target_input_hash'))[:12]}…) ---")
                result = run_pair_condition(drug, disease, condition, cap,
                                            snapshot)
                unresolved = holdout_mod.unresolved()
                result["holdout_active"] = holdout_mod.is_active()
                result["holdout_drugs"] = [drug]
                result["holdout_unresolved"] = unresolved or None
                rows_by_key[key] = result
                done[key] = result
                _flush(list(rows_by_key.values()), snapshots, fingerprint, cap,
                       conditions, generated_at)

    # Reassemble in condition-major order for a stable report / return.
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        for _csv_idx, drug, disease, _csv_disease in TARGET_CASES:
            key = _row_key(condition, drug, disease)
            if key in rows_by_key:
                rows.append(rows_by_key[key])

    _flush(rows, snapshots, fingerprint, cap, conditions, generated_at)
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(build_markdown(rows, cap, conditions, generated_at))
    _log(f"done -> {RESULTS_MD}")
    return rows


def _parse_conditions(spec: Optional[str]) -> tuple[str, ...]:
    if not spec:
        return tuple(CONDITIONS)
    requested = [c.strip() for c in spec.split(",") if c.strip()]
    unknown = [c for c in requested if c not in CONDITIONS]
    if unknown:
        raise ValueError(
            f"unknown condition(s): {unknown}; valid: {sorted(CONDITIONS)}")
    # Preserve canonical order.
    return tuple(c for c in CONDITIONS if c in requested)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V2 source-ablation control (13 confirmed cases x 4 "
                    "source conditions).")
    p.add_argument("--cap", type=int, default=DEFAULT_TARGET_CAP,
                   help=f"max target rows per case (default {DEFAULT_TARGET_CAP})")
    p.add_argument("--fresh", action="store_true",
                   help="force a clean rerun (required to discard a stale partial)")
    p.add_argument("--conditions", default=None,
                   help="comma-separated subset of "
                        f"{sorted(CONDITIONS)} (default: all)")
    p.add_argument("--label", default=LABEL, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        assert_not_benchmark(args.label)
    except RuntimeError as e:
        _log(str(e))
        return 2
    conditions = _parse_conditions(args.conditions)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows = run_all(args.cap, args.fresh, conditions, generated_at,
                   label=args.label)
    _log(f"RESULT: {len(rows)} row(s) across {len(conditions)} condition(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
