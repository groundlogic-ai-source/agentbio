"""
V2 engineering acceptance runner — the five archived v1 genuine-miss fixtures.

This is a STRICT ENGINEERING HARNESS, not a benchmark and not a production
ranking.  It exercises the upgraded runtime end-to-end on the five archived v1
genuine misses under the protocol in ``v2_upgrade_readiness_audit.md`` and
Amendment 2 of ``benchmark_v2_preregistration.md``.

Hard protocol invariants enforced by this module (each checked at import / run
time and unit-tested in validation/test_v2_acceptance.py):

  * The run is ALWAYS labelled ``engineering_acceptance``.  It is NEVER
    ``benchmark_v2`` / ``benchmark-freeze-v2``.  Being invoked under the
    benchmark label — or while a ``benchmark-freeze-v2`` git tag exists — is a
    HARD REFUSAL (the acceptance test predates, and must never masquerade as,
    the frozen v2 benchmark).  This module NEVER creates a freeze/tag.

  * The confirmed drug name is used ONLY to (a) seal the holdout context and
    (b) match the drug back into the pipeline output AFTER the run.  It is
    NEVER passed into disease-side target selection or any source-collection
    call.  The pipeline is driven by DISEASE INPUT ONLY:
        select_for_disease(disease) -> run_biologist -> run_chemist
                                     -> pooled union -> run_reviewer

  * Holdout scope (see data_sources/holdout.py):
      - SEALED:   the drug's disease-indication precedent leakage
                  (approved-drug name lists, has_approved / unmet-need signal).
      - RETAINED: generic target pharmacology — the target's ChEMBL bioactivity
                  pool.  A drug surfacing in an honestly-selected target's
                  IC50/Ki pool is the REDISCOVERY moment this harness measures.

  * Every candidate target ROW returned by select_for_disease is run, up to a
    configurable cap (default 10).  This is an engineering harness: we do not
    truncate to a production top-k ranking.

  * Active-moiety matching is by PubChem InChIKey connectivity block FIRST,
    then a conservative name match.

  * Per-fixture outcome levels are reported SEPARATELY and never collapsed:
      generated, mechanistically_valid, rank, top10, strong_match,
      source providers/lineages, target that generated it, holdout audit /
      unresolved, and per-lane source health.  ``mechanistically_valid`` is
      decided ONLY on the qualified evidence ledger (efficacy / target /
      mechanism evidence + action-direction compatibility) — NEVER on a bare
      name co-mention.

Incremental output: results are flushed to JSON + Markdown after every fixture
so an interrupted run resumes.  BUT resume is guarded by a fingerprint of the
runner configuration AND the pipeline source code; if either changed, a stale
resume is refused unless --fresh is passed.  --fresh always forces a clean run.

Usage:
    python3 -m validation.run_v2_engineering_acceptance [--cap N] [--fresh]

Refuses (exit 2) if invoked as benchmark_v2 or if benchmark-freeze-v2 exists.
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
from data_sources.pubchem import get_compound_data
from data_sources.multisource_candidates import merge_chemist_candidates
from data_sources import holdout as holdout_mod

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Labels & refusal invariants ──────────────────────────────────────────────
# This run has exactly ONE legitimate label.  Any attempt to relabel it as the
# benchmark is a hard refusal.
LABEL = "engineering_acceptance"
FORBIDDEN_LABELS = frozenset({"benchmark_v2", "benchmark-freeze-v2", "benchmark_v2_run"})
FORBIDDEN_FREEZE_TAG = "benchmark-freeze-v2"

RESULTS_JSON = os.path.join(VALIDATION_DIR, "engineering_acceptance_results.json")
RESULTS_MD = os.path.join(VALIDATION_DIR, "engineering_acceptance_results.md")

# Default cap on candidate target rows to run per fixture (engineering harness,
# NOT a production ranking cut).
DEFAULT_TARGET_CAP = 10

# Top-N ranking window used only to REPORT the separate `top10` outcome level.
TOP_N = 10

# Offline diagnostic context.  This is deliberately a report-only snapshot:
# it does not change Reviewer scoring, gates, or the acceptance outcome.  Keep
# both the global leaders and the local neighborhood immediately above a known
# drug so a rank such as 217/223 remains diagnosable without another run.
RANK_CONTEXT_LIMIT = 20

# The five archived v1 genuine-miss fixtures (from benchmark_v1_partial_report.md
# and v2_upgrade_readiness_audit.md).  These are regression fixtures, exactly as
# archived — never the design population, never tuned against.
#   (drug_name, disease_name)
FIXTURE_CASES: list[tuple[str, str]] = [
    ("Phenobarbital", "Lennox-Gastaut syndrome"),
    ("Lamotrigine", "Lennox-Gastaut syndrome"),
    ("Mercaptopurine", "Acute Promyelocytic Leukemia"),
    ("Vincristine", "Rhabdomyosarcoma"),
    ("Promazine", "Acute intermittent porphyria"),
]

# The acceptance harness measures target-based, disease-modifying repurposing.
# These archived known-drug pairs are retained for transparent reporting but are
# not comparable to a molecular rediscovery when their established use is
# cytotoxic chemotherapy or symptomatic/supportive care.  The classifier is
# deliberately narrow and exact-pair based until a validated indication-level
# source is available; it never changes production scores or candidate pools.
_SCOPE_LIMITATIONS: dict[tuple[str, str], dict[str, str]] = {
    ("vincristine", "rhabdomyosarcoma"): {
        "classification": "cytotoxic_chemo",
        "reason": (
            "Vincristine is an antimitotic cytotoxic regimen component; tubulin "
            "is a shared structural target rather than a disease-causal node."
        ),
    },
    ("promazine", "acuteintermittentporphyria"): {
        "classification": "symptomatic",
        "reason": (
            "Promazine use is symptomatic/supportive rather than a "
            "disease-modifying intervention for heme-pathway dysfunction."
        ),
    },
}


def classify_scope_limitation(drug_name: str, disease_name: str) -> dict[str, Any]:
    """Classify known pair scope for acceptance reporting without excluding it."""
    scope = _SCOPE_LIMITATIONS.get((_norm_name(drug_name), _norm_name(disease_name)))
    if scope:
        return {"in_expected_scope": False, **scope}
    return {
        "in_expected_scope": True,
        "classification": "mechanism_driven",
        "reason": "No pre-registered cytotoxic or symptomatic scope limitation.",
    }


# Source files whose bytes are fingerprinted for the stale-resume guard.  If any
# runtime module (or this harness) changed, a resume is refused without --fresh.
_FINGERPRINT_SOURCES = [
    "agents/target_selection.py",
    "agents/biologist.py",
    "agents/chemist.py",
    "agents/reviewer.py",
    "data_sources/holdout.py",
    "data_sources/evidence_ledger.py",
    "data_sources/multisource_candidates.py",
    "data_sources/europepmc_mechanisms.py",
    "data_sources/clinicaltrials.py",
    "data_sources/mechanism_direction.py",
    "data_sources/safety_check.py",
    "data_sources/pubchem.py",
    "data_sources/chembl.py",
    os.path.relpath(os.path.abspath(__file__), _REPO_ROOT),
]


def _log(msg: str) -> None:
    print(f"[eng-accept] {msg}", flush=True)


# ── Refusal gate ──────────────────────────────────────────────────────────────

def assert_not_benchmark(label: str) -> None:
    """Refuse to run under any benchmark label or when the v2 freeze tag exists.

    This is the core protocol seal: the engineering acceptance test must never
    be reported as (or run under) benchmark v2, and it must not proceed once the
    real v2 benchmark has been frozen.  Raises RuntimeError on violation.
    """
    if label != LABEL:
        raise RuntimeError(
            f"REFUSED: engineering acceptance runs ONLY under label "
            f"'{LABEL}'; got '{label}'. This harness must never be reported "
            f"as benchmark v2."
        )
    if label in FORBIDDEN_LABELS:  # defensive; unreachable given the check above
        raise RuntimeError(f"REFUSED: '{label}' is a forbidden benchmark label.")
    if _benchmark_v2_freeze_exists():
        raise RuntimeError(
            f"REFUSED: git tag '{FORBIDDEN_FREEZE_TAG}' exists — the v2 benchmark "
            f"is frozen. The engineering acceptance test predates the freeze and "
            f"must not run afterwards. This harness NEVER creates a freeze/tag."
        )


def _benchmark_v2_freeze_exists() -> bool:
    """True if a ``benchmark-freeze-v2`` git tag exists (checked read-only)."""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "tag", "-l", FORBIDDEN_FREEZE_TAG],
            capture_output=True, text=True, cwd=_REPO_ROOT, timeout=15,
        )
        return FORBIDDEN_FREEZE_TAG in out.stdout.split()
    except Exception:
        # Absence of git information is not a proof the freeze exists; do not
        # block the engineering test on a missing/unavailable git.
        return False


# ── Fingerprint (config + source code) for stale-resume protection ───────────

def config_source_fingerprint(cap: int) -> str:
    """Deterministic fingerprint of runner config + pipeline source bytes.

    A resume is only honored when this fingerprint matches the one stored in the
    incremental JSON.  Editing any runtime module (or this runner) or changing
    the cap invalidates the resume, so a stale partial run can never be silently
    continued.
    """
    h = hashlib.sha256()
    h.update(f"label={LABEL}".encode())
    h.update(f"cap={cap}".encode())
    h.update(f"top_n={TOP_N}".encode())
    h.update(f"strong_match_threshold={STRONG_MATCH_THRESHOLD}".encode())
    h.update(("fixtures=" + repr(FIXTURE_CASES)).encode())
    for rel in _FINGERPRINT_SOURCES:
        path = os.path.join(_REPO_ROOT, rel)
        h.update(f"\x00{rel}\x00".encode())
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


# ── Identity helpers ─────────────────────────────────────────────────────────

def _norm_name(s: Any) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _inchikey_block(ik: Optional[str]) -> Optional[str]:
    if not ik:
        return None
    return str(ik).split("-")[0].strip().upper() or None


def _resolve_confirmed_inchikey_block(drug_name: str) -> Optional[str]:
    """Resolve the confirmed drug's InChIKey block via PubChem (post-match use)."""
    try:
        pc = get_compound_data(drug_name)
        return _inchikey_block(pc.get("inchikey"))
    except Exception as e:  # pragma: no cover - network variance
        _log(f"  WARN could not resolve InChIKey for '{drug_name}': {e}")
        return None


def _name_match(a: str, b: str) -> bool:
    """Fallback identity match: exact normalized name only.

    Substring matching is unsafe for drug families: ``promazine`` is contained
    in ``triflupromazine`` even though they are distinct active moieties.
    Salt/ester/hydrate variants must match by InChIKey connectivity block.
    """
    na, nb = _norm_name(a), _norm_name(b)
    return bool(na and na == nb)


def match_active_moiety(
    drug_name: str,
    chemist_candidates: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
    confirmed_block: Optional[str],
) -> tuple[Optional[int], Optional[dict[str, Any]], Optional[str]]:
    """Match the confirmed drug into the pipeline output — POST-RUN only.

    InChIKey connectivity block FIRST (collapses salt/ester/hydrate forms),
    then a conservative name fallback.  Chemist candidates carry the structural
    inchikey; reviewed rows are keyed back by molecule_chembl_id.
    """
    matched_ids: set[str] = set()
    ik_matched_ids: set[str] = set()
    for c in chemist_candidates:
        cblock = _inchikey_block(c.get("inchikey"))
        ik_hit = bool(confirmed_block and cblock and confirmed_block == cblock)
        nm_hit = _name_match(drug_name, c.get("drug_name", ""))
        if ik_hit or nm_hit:
            mid = c.get("molecule_chembl_id")
            if mid:
                matched_ids.add(mid)
                if ik_hit:
                    ik_matched_ids.add(mid)
    for i, r in enumerate(reviewed, 1):
        mid = r.get("molecule_chembl_id")
        id_hit = mid in matched_ids
        nm_hit = _name_match(drug_name, r.get("drug_name", ""))
        if id_hit or nm_hit:
            method = "inchikey_block" if (mid in ik_matched_ids) else "name"
            return i, r, method
    return None, None, None


# ── Mechanistic validity classifier (ledger evidence ONLY) ───────────────────

# Action words considered directionally MEANINGFUL (a therapeutic action).
_DIRECTIONAL_ACTIONS = {
    "agonist", "antagonist", "inhibitor", "activator", "modulator",
    "blocker", "opener", "partial agonist", "inverse agonist",
    "positive allosteric modulator", "negative allosteric modulator",
}

# Evidence roles that can carry an efficacy / target / mechanism signal.
_VALID_ROLES = {"efficacy", "target_link", "disease_link"}


def classify_mechanistic_validity(matched_row: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Decide `mechanistically_valid` from QUALIFIED ledger evidence ONLY.

    Validity requires ALL of:
      1. At least one QUALIFIED evidence record whose role is efficacy /
         target_link / disease_link / approval (NOT a bare name mention).
      2. Positive efficacy confidence (or a qualified mechanism/approval record).
      3. Direction compatibility: the drug's action on the target is either an
         explicit therapeutic action (agonist/antagonist/inhibitor/…) OR the
         evidence is not directionally CONTRADICTED and not capped by the
         reviewer's mechanism-direction gate.

    A candidate that appears only via a name co-mention, or whose evidence is
    entirely unqualified/contradicted, or whose mechanism-direction check marks
    it DIRECTIONALLY_INCOMPATIBLE, is NOT mechanistically valid.
    """
    result: dict[str, Any] = {
        "mechanistically_valid": False,
        "reason": None,
        "qualified_evidence_count": 0,
        "efficacy_confidence": None,
        "direction_compatible": None,
        "name_mention_only": None,
    }
    if not matched_row:
        result["reason"] = "no matched candidate row"
        return result

    ledger = matched_row.get("_evidence_ledger") or {}
    records = ledger.get("records") or []

    # 1. Count QUALIFIED, role-bearing evidence (never a bare name mention).
    qualified_records = [
        rec for rec in records
        if str(rec.get("qualification_status", "")).lower() == "qualified"
        and str(rec.get("evidence_role", "")).lower() in _VALID_ROLES
    ]
    result["qualified_evidence_count"] = len(qualified_records)

    # A record that is ONLY a structure/other role with no efficacy/target link
    # is treated as a name-level co-mention for validity purposes.
    only_nonmechanistic = bool(records) and not qualified_records
    result["name_mention_only"] = only_nonmechanistic

    # 2. Efficacy confidence from the ledger (0..1) or None when not applicable.
    eff = matched_row.get("efficacy_confidence")
    if eff is None:
        eff = ledger.get("efficacy_confidence")
    result["efficacy_confidence"] = eff

    # 3. Direction compatibility.
    #    (a) reviewer mechanism-direction gate must not have flagged incompatible.
    mech_dir = matched_row.get("mechanism_direction") or {}
    dir_incompatible = bool(mech_dir.get("incompatible")) or (
        str(mech_dir.get("verdict", "")).upper() == "DIRECTIONALLY_INCOMPATIBLE"
    )
    mech_capped = bool(matched_row.get("mechanism_cap_applied"))
    #    (b) any qualified record carries a therapeutic action / non-unknown dir.
    has_directional_action = any(
        str(rec.get("action", "")).strip().lower() in _DIRECTIONAL_ACTIONS
        or str(rec.get("direction", "")).strip().lower() not in ("", "unknown")
        for rec in qualified_records
    )
    #    (c) no qualified record is a CONTRADICTED disagreement.
    any_contradicted = any(
        str(rec.get("contradiction_status", "")).lower() == "contradicted"
        for rec in qualified_records
    )
    direction_compatible = (
        not dir_incompatible and not mech_capped and not any_contradicted
    )
    result["direction_compatible"] = direction_compatible

    if not qualified_records:
        result["reason"] = (
            "no qualified efficacy/target/mechanism evidence "
            "(name co-mention does not qualify)"
        )
        return result

    has_positive_efficacy = (eff is not None and float(eff) > 0.0)
    # A qualified target/mechanism record can establish pharmacologic relevance
    # when the modality has no numeric potency. Generic approval cannot: prior
    # approval is repurposing/safety context, never efficacy for this disease.
    has_mechanism = any(
        str(rec.get("evidence_role", "")).lower() == "target_link"
        for rec in qualified_records
    )
    if not (has_positive_efficacy or has_mechanism):
        result["reason"] = "qualified evidence present but no positive efficacy/mechanism signal"
        return result

    if not direction_compatible:
        if dir_incompatible or mech_capped:
            result["reason"] = "mechanism-direction gate marked drug directionally incompatible"
        else:
            result["reason"] = "qualified evidence is contradicted"
        return result

    result["mechanistically_valid"] = True
    result["reason"] = (
        f"{len(qualified_records)} qualified record(s); "
        f"efficacy_confidence={eff}; direction compatible"
        + ("; explicit therapeutic action" if has_directional_action else "")
    )
    return result


# ── Per-target execution (disease-driven; drug name NEVER passed in) ─────────

def _row_to_target(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_symbol": row["target_symbol"],
        "uniprot_id": row.get("uniprot_id"),
        "ensembl_id": row.get("ensembl_id"),
        "disease_name": row["disease_name"],
        "orpha_code": row.get("orpha_code"),
        "ot_association_score": row.get("ot_association_score", 0.0),
        "tractability_score": row.get("tractability_score"),
        "unmet_need_score": row.get("unmet_need_score"),
        "target_discovery_method": row.get("target_discovery_method",
                                           "genetic_association"),
        "mechanism_class": row.get("mechanism_class"),
        "therapeutic_role": row.get("therapeutic_role", "disease_modifying"),
        "process_support": row.get("process_support", []),
        "process_query": row.get("process_query"),
        "process_source_status": row.get("process_source_status"),
        "process_ontology_version": row.get("process_ontology_version"),
        "process_target_priority": row.get("process_target_priority"),
        "process_class_priority": row.get("process_class_priority"),
    }


def _source_health_from_reviewed(reviewed: list[dict[str, Any]]) -> dict[str, bool]:
    """Aggregate per-lane source health across reviewed candidates."""
    health: dict[str, bool] = {}
    for r in reviewed:
        for prov, ok in (r.get("source_health") or {}).items():
            health[prov] = health.get(prov, False) or bool(ok)
        ledger = r.get("_evidence_ledger") or {}
        for prov, ok in (ledger.get("source_health") or {}).items():
            health[prov] = health.get(prov, False) or bool(ok)
    return health


def _source_health_from_target_results(
    target_results: list[dict[str, Any]],
) -> dict[str, bool]:
    """Include adapters that yielded zero candidates or were unavailable."""
    health: dict[str, bool] = {}
    for target in target_results:
        for provider, state in (target.get("source_status") or {}).items():
            status = str((state or {}).get("status") or "")
            healthy = status in {"ok", "empty"}
            health[provider] = health.get(provider, False) or healthy
    return health


def _rank_context_ledger(ledger: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, JSON-safe evidence summary for offline diagnostics.

    Raw provider payloads are intentionally excluded.  The summary preserves
    the fields needed to distinguish qualified mechanism evidence from
    unqualified/name-only records without making another source request.
    """
    records = ledger.get("records") or []
    public_records = []
    for rec in records:
        public_records.append({
            key: rec.get(key)
            for key in (
                "provider", "source_type", "source_id", "lineage_id",
                "evidence_role", "target_symbol", "action", "direction",
                "measurement_type", "measurement_value",
                "qualification_status", "contradiction_status",
            )
            if rec.get(key) is not None
        })
    return {
        "identity": ledger.get("identity"),
        "providers": sorted(set(ledger.get("providers") or [])),
        "target_symbols": sorted(set(ledger.get("target_symbols") or [])),
        "source_health": dict(ledger.get("source_health") or {}),
        "efficacy_confidence": ledger.get("efficacy_confidence"),
        "safety_confidence": ledger.get("safety_confidence"),
        "record_count": len(records),
        "records": public_records,
    }


def _public_rank_context_row(row: dict[str, Any], rank: int) -> dict[str, Any]:
    """Make one final Reviewer row safe and useful for offline analysis."""
    ledger = row.get("_evidence_ledger") or {}
    fields = (
        "drug_name", "molecule_chembl_id", "target_symbol", "disease_name",
        "pchembl_value", "confidence_score", "efficacy_confidence",
        "ot_association_score", "tanimoto_score", "is_approved_drug",
        "most_similar_approved_drug", "rationale", "descriptors",
        "pubchem_xlogp", "high_lipophilicity_flag", "strong_match",
        "composite_score", "pre_cap_score", "score_components",
        "unapproved_cap_applied",
        "lipinski_penalty_applied", "trials_query_failed",
        "trials_holdout_redacted", "mutation_specificity",
        "target_discovery_method", "mechanism_class", "therapeutic_role",
        "process_memberships", "mechanism_direction",
        "mechanism_cap_applied", "safety_cap_applied", "black_box_advisory",
        "source_chembl_ids", "source_activity_ids", "source_types",
        "target_memberships", "uniprot_id",
    )
    public = {"rank": rank}
    for key in fields:
        if key in row:
            public[key] = row[key]
    public["evidence_ledger"] = _rank_context_ledger(ledger)
    return public


def _rank_context_snapshot(
    reviewed: list[dict[str, Any]],
    known_rank: Optional[int],
) -> list[dict[str, Any]]:
    """Capture global leaders plus the local window above the known drug.

    The known row is retained as the anchor for score deltas; the local window
    contains exactly ``RANK_CONTEXT_LIMIT`` rows strictly above it.
    """
    if not reviewed:
        return []
    keep: set[int] = set(range(min(RANK_CONTEXT_LIMIT, len(reviewed))))
    if known_rank is not None:
        start = max(1, known_rank - RANK_CONTEXT_LIMIT)
        keep.update(range(start - 1, min(known_rank, len(reviewed) - 1)))
        if known_rank <= len(reviewed):
            keep.add(known_rank - 1)
    return [
        _public_rank_context_row(row, index + 1)
        for index, row in enumerate(reviewed)
        if index in keep
    ]


def _run_one_target(row: dict[str, Any]) -> dict[str, Any]:
    """Run biologist -> chemist for one disease-selected target row.

    The confirmed drug name is NEVER passed into any of these calls; only the
    disease-derived target drives collection. Reviewer runs once after the
    cross-target active-moiety union, matching the production graph.
    """
    target = _row_to_target(row)
    rec: dict[str, Any] = {
        "target_symbol": target["target_symbol"],
        "uniprot_id": target["uniprot_id"],
        "ot_association_score": target["ot_association_score"],
        "target_discovery_method": target["target_discovery_method"],
        "status": None,
        "n_chemist_candidates": 0,
        "n_reviewed": 0,
        "error": None,
        "_chemist_candidates": [],
        "_biologist_output": None,
        "source_status": {},
    }
    try:
        bio = run_biologist(target)
        # Frozen v2 engineering source set: this suite verifies the v2
        # fix-set, so post-v2 lanes (e.g. bindingdb) stay out of it.
        chem = run_chemist(bio, repurposing_only=True,
                           enabled_sources=("chembl", "gtopdb",
                                            "drugcentral"))
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


def run_fixture(drug_name: str, disease_name: str, cap: int) -> dict[str, Any]:
    """Run one fixture: disease-driven pipeline over up to `cap` target rows.

    The confirmed `drug_name` is used ONLY (a) to seal holdout (by the caller)
    and (b) for post-run active-moiety matching here — never in source
    collection.
    """
    result: dict[str, Any] = {
        "label": LABEL,
        "drug_name": drug_name,
        "disease_name": disease_name,
        "target_cap": cap,
        "in_universe": None,
        "status": None,
        "candidate_targets_considered": [],
        "n_targets_run": 0,
        # separate outcome levels (never collapsed)
        "generated": False,
        "mechanistically_valid": False,
        "mechanistic_validity_detail": None,
        "rank": None,
        "total_candidates": None,
        "top10": False,
        "strong_match": None,
        "match_method": None,
        "generated_by_target": None,
        "therapeutic_role": None,
        "mechanism_class": None,
        "scope_classification": classify_scope_limitation(drug_name, disease_name),
        "process_memberships": [],
        "trials_holdout_redacted": None,
        "score_components": None,
        # provenance
        "source_providers": [],
        "source_lineages": [],
        "source_health": {},
        # Populated after the final Reviewer run.  This is a report-only
        # snapshot; private reviewer payloads never leave the process.
        "rank_context": [],
        # holdout audit
        "holdout_active": None,
        "holdout_drugs": None,
        "holdout_unresolved": None,
        "per_target_results": [],
        "error": None,
    }

    # ── 1. Target selection (DISEASE ONLY) ───────────────────────────────────
    try:
        rows = select_for_disease(disease_name)
    except DiseaseNotInUniverse as e:
        result.update(status="out_of_scope", in_universe=False, error=str(e))
        _log(f"  OUT OF SCOPE: {e}")
        return result
    except Exception as e:  # pragma: no cover - network/runtime variance
        result.update(status="error", in_universe=True, error=str(e))
        _log(f"  ERROR in target_selection: {e}")
        return result

    result["in_universe"] = True
    result["candidate_targets_considered"] = [
        {"target_symbol": r.get("target_symbol"),
         "uniprot_id": r.get("uniprot_id"),
         "ot_association_score": r.get("ot_association_score"),
         "target_discovery_method": r.get("target_discovery_method")}
        for r in rows
    ]

    # ── 2. Run EVERY candidate target row up to the cap (engineering harness) ─
    run_rows = select_source_diverse_targets(rows, cap)
    result["n_targets_run"] = len(run_rows)
    _log(f"  running {len(run_rows)}/{len(rows)} candidate target row(s) (cap={cap})")

    per_target_public: list[dict[str, Any]] = []
    all_chemist: list[dict[str, Any]] = []
    generating_targets: dict[str, dict[str, Any]] = {}
    successful_bio: list[dict[str, Any]] = []

    for k_idx, row in enumerate(run_rows, 1):
        _log(f"  [target {k_idx}/{len(run_rows)}] {row.get('target_symbol')} "
             f"({row.get('uniprot_id')}) OT={row.get('ot_association_score', 0) or 0:.3f}")
        rec = _run_one_target(row)
        chem_cands = rec.pop("_chemist_candidates", [])
        bio_output = rec.pop("_biologist_output", None)
        all_chemist.extend(chem_cands)
        if bio_output:
            successful_bio.append(bio_output)

        # Target-level diagnostics use exact normalized-name identity only.
        # Structural active-moiety matching is intentionally deferred until
        # AFTER the complete Reviewer run, so the confirmed drug identity can
        # never influence collection, union, scoring, or caps.
        generated_here = any(
            _name_match(drug_name, cand.get("drug_name", ""))
            for cand in chem_cands
        )
        method_here = None
        if generated_here:
            method_here = "name"
        rec["found"] = generated_here
        rec["rank_in_target"] = None
        rec["match_method"] = method_here
        per_target_public.append(rec)
        if generated_here:
            for cand in chem_cands:
                if _name_match(drug_name, cand.get("drug_name", "")):
                    identity = (cand.get("_evidence_ledger") or {}).get(
                        "identity") or cand.get("molecule_chembl_id") or _norm_name(
                            cand.get("drug_name"))
                    generating_targets.setdefault(identity, {
                        "target_symbol": row.get("target_symbol"),
                        "uniprot_id": row.get("uniprot_id"),
                        "target_rank": k_idx,
                        "target_discovery_method": row.get("target_discovery_method"),
                    })

    result["per_target_results"] = per_target_public

    # Mirror production graph semantics: one active-moiety union across every
    # pursued target, followed by one final Reviewer ranking.
    pooled_candidates = merge_chemist_candidates(all_chemist)
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
    # Confirmed-drug structure resolution is post-run matching only. It occurs
    # after target selection, collection, union, Reviewer scoring, and all
    # gates, so it cannot influence what the pipeline generated or ranked.
    confirmed_block = _resolve_confirmed_inchikey_block(drug_name)
    rank, matched, method = match_active_moiety(
        drug_name, pooled_candidates, final_reviewed, confirmed_block)
    # Preserve enough of the already-computed final ranking to answer the
    # inexpensive diagnostic question later.  No Reviewer/API call occurs
    # here; the known drug is used only to choose which already-ranked rows
    # are retained for context.
    result["rank_context"] = _rank_context_snapshot(final_reviewed, rank)

    # ── 3. Record outcome levels (each SEPARATE) ─────────────────────────────
    if rank is None or matched is None:
        result.update(
            status="miss", generated=False, mechanistically_valid=False,
            mechanistic_validity_detail={"reason": "not generated in any target pool"},
        )
        _log(f"  MISS: {drug_name} not generated in any of {len(run_rows)} pools")
        return result

    validity = classify_mechanistic_validity(matched)
    ledger = matched.get("_evidence_ledger") or {}
    lineages = sorted({
        rec.get("lineage_id") for rec in (ledger.get("records") or [])
        if rec.get("lineage_id")
    })

    identity = ledger.get("identity") or matched.get("molecule_chembl_id") or _norm_name(
        matched.get("drug_name"))
    generated_by = generating_targets.get(identity)
    if generated_by is None:
        memberships = ledger.get("target_symbols") or []
        generated_by = {
            "target_symbol": memberships[0] if memberships else matched.get("target_symbol"),
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
        therapeutic_role=matched.get("therapeutic_role"),
        mechanism_class=matched.get("mechanism_class"),
        process_memberships=matched.get("process_memberships", []),
        trials_holdout_redacted=bool(
            matched.get("trials_holdout_redacted")
        ),
        score_components=matched.get("score_components"),
        source_providers=sorted(ledger.get("providers") or []),
        source_lineages=lineages,
    )
    _log(f"  GENERATED: {drug_name} via target-rank {generated_by.get('target_rank')} "
         f"({generated_by.get('target_symbol')}), final pooled rank {rank}, "
         f"mechanistically_valid={result['mechanistically_valid']}, "
         f"top10={result['top10']}, strong_match={result['strong_match']}")
    return result


# ── Persistence (incremental JSON + Markdown, fingerprint-guarded resume) ────

def select_fixture_cases(only: Optional[str]) -> list[tuple[str, str]]:
    """Filter the frozen fixture list for a single-case diagnostic run.

    ``only`` matches a normalized substring of the drug OR disease name.  The
    fixture list itself is never edited or reordered: this selects from the
    archived set, it does not redefine it.
    """
    if not only:
        return list(FIXTURE_CASES)
    needle = _norm_name(only)
    picked = [
        (drug, disease) for drug, disease in FIXTURE_CASES
        if needle in _norm_name(drug) or needle in _norm_name(disease)
    ]
    if not picked:
        raise RuntimeError(
            f"--only {only!r} matched none of the {len(FIXTURE_CASES)} archived "
            f"fixtures: {[d for d, _ in FIXTURE_CASES]}"
        )
    return picked


def diagnostic_result_paths(only: Optional[str]) -> tuple[str, str]:
    """Result paths for a run, isolated when it is a partial selection.

    A ``--only`` run measures a SUBSET and must never overwrite the canonical
    acceptance artifacts, otherwise a one-case diagnostic could later be read
    as a completed five-fixture acceptance result.
    """
    if not only:
        # Read at call time so tests and callers can rebind the globals.
        return RESULTS_JSON, RESULTS_MD
    slug = _norm_name(only) or "subset"
    base = os.path.join(VALIDATION_DIR, f"engineering_acceptance_only_{slug}")
    return f"{base}.json", f"{base}.md"


def _load_existing(fingerprint: str,
                   results_json: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Load prior results ONLY if the fingerprint matches; else refuse resume."""
    # Resolved at CALL time, not bound as a default: RESULTS_JSON is patched by
    # tests and rebound for --only runs.
    results_json = results_json or RESULTS_JSON
    if not os.path.exists(results_json):
        return {}
    try:
        with open(results_json, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if data.get("label") != LABEL:
        _log("  existing results are not engineering_acceptance-labelled; ignoring")
        return {}
    if data.get("fingerprint") != fingerprint:
        raise RuntimeError(
            "STALE RESUME REFUSED: config/source fingerprint changed since the "
            "existing partial run. Re-run with --fresh to start clean "
            "(the runner never silently resumes across code changes)."
        )
    return {
        (_norm_name(c["drug_name"]), _norm_name(c["disease_name"])): c
        for c in data.get("cases", [])
    }


def _strip_case(case: dict[str, Any]) -> dict[str, Any]:
    """Drop private (underscore-prefixed) keys before persistence."""
    out = {k: v for k, v in case.items() if not k.startswith("_")}
    out["per_target_results"] = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in case.get("per_target_results", [])
    ]
    return out


def _flush(cases: list[dict[str, Any]], fingerprint: str, cap: int,
           generated_at: str, results_json: Optional[str] = None,
           only: Optional[str] = None) -> None:
    # Resolved at CALL time — see _load_existing.
    results_json = results_json or RESULTS_JSON
    payload = {
        "label": LABEL,
        "generated_at": generated_at,
        "fingerprint": fingerprint,
        "target_cap": cap,
        "top_n": TOP_N,
        "strong_match_threshold": STRONG_MATCH_THRESHOLD,
        # Selection provenance: a partial run is self-describing so it can
        # never be mistaken for a completed five-fixture acceptance result.
        "selection": only or "all_fixtures",
        "is_partial_selection": bool(only),
        "fixture_count_total": len(FIXTURE_CASES),
        "fixture_count_run": len(cases),
        "cases": [_strip_case(c) for c in cases],
    }
    with open(results_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    _log(f"  saved {len(cases)} case(s) -> {results_json}")


def build_markdown(cases: list[dict[str, Any]], cap: int, generated_at: str) -> str:
    scored = [c for c in cases if c.get("in_universe")]
    generated = sum(1 for c in scored if c.get("generated"))
    valid = sum(1 for c in scored if c.get("mechanistically_valid"))
    top10 = sum(1 for c in scored if c.get("top10"))
    strong = sum(1 for c in scored if c.get("strong_match"))

    lines: list[str] = [
        "# V2 Engineering Acceptance — Five Archived v1 Genuine Misses",
        "",
        f"_Label: **{LABEL}** (NOT benchmark v2). Generated: {generated_at}._",
        "",
        f"Disease-input pipeline only "
        f"(`select_for_disease` -> biologist -> chemist -> pooled union -> reviewer); "
        f"the confirmed drug is held out and used only for post-run matching. "
        f"Every candidate target row is run up to a cap of **{cap}** "
        f"(engineering harness, not a production ranking).",
        "",
        "## Summary (outcome levels reported separately)",
        "",
        f"- Generated: **{generated}/{len(scored)}**",
        f"- Mechanistically valid: **{valid}/{len(scored)}** "
        f"(qualified evidence + direction compatibility only)",
        f"- Top-10: {top10}/{len(scored)}  |  STRONG_MATCH: {strong}/{len(scored)}",
        "",
        "## Per-fixture",
        "",
        "| # | Drug | Disease | Generated | Mech-valid | Rank | Top10 | Strong | "
        "By target | Match | Providers |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n, c in enumerate(scored, 1):
        gen = "✓" if c.get("generated") else "—"
        mv = "✓" if c.get("mechanistically_valid") else "—"
        rank = c.get("rank") if c.get("rank") is not None else "—"
        t10 = "✓" if c.get("top10") else "—"
        sm = "✓" if c.get("strong_match") else "—"
        by = (c.get("generated_by_target") or {}).get("target_symbol") or "—"
        method = c.get("match_method") or "—"
        provs = ", ".join(c.get("source_providers") or []) or "—"
        lines.append(
            f"| {n} | {c['drug_name']} | {c['disease_name']} | {gen} | {mv} | "
            f"{rank} | {t10} | {sm} | {by} | {method} | {provs} |"
        )

    lines += ["", "## Holdout audit & source health", ""]
    for c in scored:
        lines.append(f"### {c['drug_name']} / {c['disease_name']}")
        lines.append(f"- holdout active: {c.get('holdout_active')} "
                     f"· drugs: {c.get('holdout_drugs')}")
        lines.append(f"- holdout unresolved: {c.get('holdout_unresolved') or '—'}")
        detail = c.get("mechanistic_validity_detail") or {}
        lines.append(f"- validity: {detail.get('reason')}")
        lines.append(f"- source lineages: {len(c.get('source_lineages') or [])}")
        lines.append(f"- source health: {c.get('source_health') or {}}")
        lines.append("")
    oos_cases = [c for c in cases if not c.get("in_universe")]
    if oos_cases:
        lines += ["## Out-of-scope / errored fixtures", ""]
        for c in oos_cases:
            lines.append(f"- {c['drug_name']} / {c['disease_name']}: "
                         f"{c.get('status')} — {c.get('error')}")
        lines.append("")
    return "\n".join(lines)


def run_all(cap: int, fresh: bool, generated_at: str,
            *, label: str = LABEL,
            only: Optional[str] = None) -> list[dict[str, Any]]:
    """Run the archived fixtures. Refuses under any benchmark label / v2 freeze.

    ``only`` restricts the run to a matching archived fixture and redirects
    output to an isolated diagnostic artifact.
    """
    assert_not_benchmark(label)
    fingerprint = config_source_fingerprint(cap)
    selected = select_fixture_cases(only)
    results_json, results_md = diagnostic_result_paths(only)
    if only:
        _log(f"--only {only!r}: running {len(selected)}/{len(FIXTURE_CASES)} "
             f"archived fixture(s) -> {os.path.basename(results_json)} "
             "(canonical acceptance artifacts untouched)")

    if fresh:
        for path in (results_json, results_md):
            if os.path.exists(path):
                os.remove(path)
        done: dict[str, dict[str, Any]] = {}
        _log("--fresh: cleared any prior partial results")
    else:
        done = _load_existing(fingerprint, results_json)
        _log(f"resume: {len(done)} fingerprint-matched case(s) already done")

    cases: list[dict[str, Any]] = []
    for drug, disease in selected:
        key = (_norm_name(drug), _norm_name(disease))
        if key in done:
            _log(f"  SKIP (fingerprint-matched, done): {drug} / {disease}")
            cases.append(done[key])
            continue

        _log(f"=== FIXTURE: {drug} / {disease} [holdout seals: {[drug]}] ===")
        # Holdout: seal the confirmed drug's disease-indication precedent; the
        # generic target pharmacology pool is deliberately RETAINED so a real
        # rediscovery can occur.
        with holdout_mod.holdout_active([drug]):
            result = run_fixture(drug, disease, cap)
            unresolved = holdout_mod.unresolved()
            result["holdout_active"] = holdout_mod.is_active()
        result["holdout_drugs"] = [drug]
        result["holdout_unresolved"] = unresolved or None
        cases.append(result)
        done[key] = result
        _flush(cases, fingerprint, cap, generated_at, results_json, only)

    _flush(cases, fingerprint, cap, generated_at, results_json, only)
    with open(results_md, "w", encoding="utf-8") as f:
        f.write(build_markdown(cases, cap, generated_at))
    _log(f"done -> {results_md}")
    return cases


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="V2 engineering acceptance runner (five archived v1 misses).")
    p.add_argument("--cap", type=int, default=DEFAULT_TARGET_CAP,
                   help=f"max candidate target rows per fixture "
                        f"(default {DEFAULT_TARGET_CAP})")
    p.add_argument("--fresh", action="store_true",
                   help="force a clean rerun (required to discard a stale partial)")
    p.add_argument("--only", default=None,
                   help="run ONLY the archived fixture(s) whose drug or disease "
                        "name contains this text (diagnostic subset run; writes "
                        "to an isolated artifact, never the canonical results)")
    p.add_argument("--label", default=LABEL,
                   help=argparse.SUPPRESS)  # only 'engineering_acceptance' allowed
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        assert_not_benchmark(args.label)
    except RuntimeError as e:
        _log(str(e))
        return 2
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        cases = run_all(args.cap, args.fresh, generated_at,
                        label=args.label, only=args.only)
    except RuntimeError as e:
        _log(str(e))
        return 2
    scored = [c for c in cases if c.get("in_universe")]
    gen = sum(1 for c in scored if c.get("generated"))
    valid = sum(1 for c in scored if c.get("mechanistically_valid"))
    _log(f"RESULT: generated {gen}/{len(scored)}; "
         f"mechanistically valid {valid}/{len(scored)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
