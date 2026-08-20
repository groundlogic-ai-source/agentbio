"""
Candidate audit: look up where a specific drug stands in AgentBio's
reviewed_candidates pool for a given disease.

Used by:
  • Part A — the user-facing /api/audit endpoint
  • Part B — the validation script (imports run_audit() directly, same code path)

Design: purely functional. No second explanation path that can drift from the
live reports. Cap fields are the exact same fields the writer reads.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

import api.jobs_db as jobs_db
from api.audit_context import build_audit_context
from api.domain_findings import domain_findings_for, modality_finding_for
from agents.reviewer import SAFETY_SCHEMA_VERSION
from data_sources.chembl import (
    _find_molecule_chembl_id,
    get_drug_mechanism_identities_for_audit,
    get_molecule_data,
)

# ── Candidate storage ─────────────────────────────────────────────────────────
_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output",
)
_CANDIDATES_DIR = os.path.join(_OUTPUT_DIR, "candidates")
_SHARED_PATH = os.path.join(_OUTPUT_DIR, "reviewed_candidates.json")


def candidates_path(job_id: str) -> str:
    return os.path.join(_CANDIDATES_DIR, f"{job_id}.json")


def save_job_candidates(job_id: str) -> bool:
    """Copy current reviewed_candidates.json → output/candidates/{job_id}.json.
    Called from _run_graph() when the writer node fires. Returns True on success."""
    os.makedirs(_CANDIDATES_DIR, exist_ok=True)
    if not os.path.exists(_SHARED_PATH):
        return False
    try:
        with open(_SHARED_PATH, encoding="utf-8") as fh:
            payload = json.load(fh)
        with open(candidates_path(job_id), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return True
    except Exception:
        return False


def _load_candidates(job_id: str, canonical_disease: str) -> Optional[list[dict]]:
    """Load candidates for a job.

    Preference order:
    1. Per-job file at output/candidates/{job_id}.json  (always correct)
    2. Shared fallback output/reviewed_candidates.json  (only if disease matches)
    Returns None if neither is available.
    """
    path = candidates_path(job_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        cands = payload.get("candidates", [])
        if payload.get("safety_schema_version") != SAFETY_SCHEMA_VERSION:
            for c in cands:
                c["pool_safety_stale"] = True
        return cands

    if os.path.exists(_SHARED_PATH):
        with open(_SHARED_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        cands = data.get("candidates", [])
        if cands:
            # Safe to use only if the last run was for this same disease
            sample_disease = (cands[0].get("disease_name") or "").lower()
            if sample_disease == canonical_disease.lower():
                if data.get("safety_schema_version") != SAFETY_SCHEMA_VERSION:
                    for c in cands:
                        c["pool_safety_stale"] = True
                return cands

    return None


# ── Name matching ─────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s\-]+", " ", s.strip().lower())


def _find_drug_in_pool(
    candidates: list[dict],
    drug_name: str,
    chembl_id: Optional[str],
) -> tuple[Optional[int], Optional[dict]]:
    """Return (1-based rank, candidate dict) or (None, None)."""
    name_norm = _norm(drug_name)
    for i, c in enumerate(candidates, 1):
        if _norm(c.get("drug_name", "")) == name_norm:
            return i, c
        if chembl_id and c.get("molecule_chembl_id") == chembl_id:
            return i, c
    return None, None


def _resolved_identity_route(
    candidate: Optional[dict[str, Any]],
    drug_name: str,
    chembl_id: Optional[str],
) -> str:
    if candidate is None:
        return "chembl_name_resolution" if chembl_id else "unresolved_name"
    if _norm(candidate.get("drug_name", "")) == _norm(drug_name):
        return "pool_name_match"
    if chembl_id and candidate.get("molecule_chembl_id") == chembl_id:
        return "pool_molecule_chembl_id_match"
    return "pool_match"


def _pool_target_ladder(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unique persisted candidate targets in first-candidate appearance order."""
    seen: set[tuple[str, str, str]] = set()
    ladder: list[dict[str, Any]] = []
    for candidate_rank, candidate in enumerate(candidates, 1):
        symbol = str(candidate.get("target_symbol") or "").strip().upper()
        uniprot = str(candidate.get("uniprot_id") or "").strip().upper()
        target_chembl_id = str(
            candidate.get("target_chembl_id") or "").strip().upper()
        key = (symbol, uniprot, target_chembl_id)
        if key == ("", "", "") or key in seen:
            continue
        seen.add(key)
        ladder.append({
            "target_symbol": symbol or None,
            "uniprot_id": uniprot or None,
            "target_chembl_id": target_chembl_id or None,
            "first_candidate_rank": candidate_rank,
        })
    return ladder


def _mechanism_target_class(target: dict[str, Any]) -> str:
    """Classify a ChEMBL target before using it for stable identity overlap."""
    target_type = str(target.get("target_type") or "").strip().upper()
    organism = str(target.get("organism") or "").strip().casefold()
    tax_id = target.get("tax_id")
    human = tax_id == 9606 or organism == "homo sapiens"
    if not human:
        return "nonhuman"
    if target_type == "SINGLE PROTEIN":
        return "direct_human_protein"
    if "PROTEIN" in target_type:
        return "human_protein_component"
    return "nonprotein"


def _mechanism_identity_keys(target: dict[str, Any]) -> set[tuple[str, str]]:
    keys = {
        ("gene_symbol", str(value).strip().upper())
        for value in target.get("gene_symbols", []) or []
        if str(value).strip()
    }
    keys.update({
        ("uniprot_id", str(value).strip().upper())
        for value in target.get("uniprot_ids", []) or []
        if str(value).strip()
    })
    target_chembl_id = str(target.get("target_chembl_id") or "").strip().upper()
    if target_chembl_id:
        keys.add(("target_chembl_id", target_chembl_id))
    return keys


def build_audit_scope_diagnostics(
    status: str,
    *,
    candidates: Optional[list[dict[str, Any]]] = None,
    mechanism_evidence: Optional[dict[str, Any]] = None,
    molecule_type: Optional[str] = None,
    identity_route: str = "",
) -> dict[str, Any]:
    """Pure supplied-drug audit classification used by product and acceptance.

    It can explain an out-of-pool supplied drug, but it cannot create a
    candidate, discovery rank, score, or rediscovery claim.
    """
    base: dict[str, Any] = {
        "audit_scope_status": "not_assessable",
        "deterministic_miss_reason": None,
        "resolved_identity_route": identity_route or None,
        "diagnostic_effect": "disclosure_only",
    }
    if status == "found":
        base.update({
            "audit_scope_status": "found_by_discovery",
            "deterministic_miss_reason": "FOUND",
        })
        return base
    if status == "unresolved":
        base["deterministic_miss_reason"] = "NAME_RESOLUTION_GAP"
        return base
    if status == "no_case":
        base["deterministic_miss_reason"] = "NO_CASE"
        return base
    if status == "no_candidates":
        base["deterministic_miss_reason"] = "NO_CANDIDATES"
        return base
    if status != "absent":
        return base

    evidence = mechanism_evidence or {
        "status": "unavailable", "targets": [], "provider": "chembl"}
    source_status = str(evidence.get("status") or "unavailable")
    targets = list(evidence.get("targets") or [])
    ladder = _pool_target_ladder(candidates or [])
    direct_mechanism_keys: set[tuple[str, str]] = set()
    component_mechanism_keys: set[tuple[str, str]] = set()
    identity_classes: set[str] = set()
    for target in targets:
        target_class = _mechanism_target_class(target)
        identity_classes.add(target_class)
        keys = _mechanism_identity_keys(target)
        if target_class == "direct_human_protein":
            direct_mechanism_keys.update(keys)
        elif target_class == "human_protein_component":
            component_mechanism_keys.update(keys)

    overlaps: list[dict[str, Any]] = []
    component_overlaps: list[dict[str, Any]] = []
    coverage_ladder: list[dict[str, Any]] = []
    for row in ladder:
        row_keys = set()
        if row.get("target_symbol"):
            row_keys.add(("gene_symbol", str(row["target_symbol"]).upper()))
        if row.get("uniprot_id"):
            row_keys.add(("uniprot_id", str(row["uniprot_id"]).upper()))
        candidate_target_id = str(
            row.get("target_chembl_id") or "").strip().upper()
        if candidate_target_id:
            row_keys.add(("target_chembl_id", candidate_target_id))
        matched = sorted(direct_mechanism_keys & row_keys)
        component_matched = sorted(component_mechanism_keys & row_keys)
        projected = {
            **row,
            "mechanism_identity_overlap": bool(matched),
            "component_identity_overlap": bool(component_matched),
        }
        coverage_ladder.append(projected)
        if matched:
            overlaps.append({
                **row,
                "matched_identities": [
                    {"kind": kind, "value": value}
                    for kind, value in matched
                ],
            })
        if component_matched:
            component_overlaps.append({
                **row,
                "matched_component_identities": [
                    {"kind": kind, "value": value}
                    for kind, value in component_matched
                ],
            })

    base.update({
        "mechanism_evidence_status": source_status,
        "mechanism_evidence_provider": evidence.get("provider") or "chembl",
        "stable_mechanism_identities": targets,
        "pool_target_overlap": overlaps,
        "pool_component_target_overlap": component_overlaps,
        "target_coverage_ladder": coverage_ladder,
        "supplied_drug_discovery_rank": None,
        "supplied_drug_discovery_score": None,
        "supplied_drug_candidate_inserted": False,
    })
    if source_status == "unavailable":
        base["audit_scope_status"] = "source_failure"
        return base

    base["audit_scope_status"] = "auditable_only_because_supplied"
    if direct_mechanism_keys:
        base["stable_identity_status"] = "direct_human_protein"
    elif component_mechanism_keys:
        base["stable_identity_status"] = "component_only"
    elif "nonhuman" in identity_classes:
        base["stable_identity_status"] = "nonhuman_or_nonprotein_only"
    elif "nonprotein" in identity_classes:
        base["stable_identity_status"] = "nonhuman_or_nonprotein_only"
    else:
        base["stable_identity_status"] = "unmapped"

    if source_status == "empty" or not direct_mechanism_keys:
        base["deterministic_miss_reason"] = "NO_MECHANISM_DATA"
    elif overlaps:
        normalized_type = str(molecule_type or "").strip().casefold()
        biologic = any(token in normalized_type for token in (
            "antibody", "protein", "enzyme", "oligosaccharide",
            "oligonucleotide", "cell", "gene",
        ))
        base["deterministic_miss_reason"] = (
            "BIOLOGIC_STRUCTURAL" if biologic else "ASSAY_POOL_GAP"
        )
    else:
        base["deterministic_miss_reason"] = "TARGET_NOT_SELECTED"
    return base


# ── Cap disclosure ─────────────────────────────────────────────────────────────

def _cap_reason(c: dict) -> Optional[str]:
    """Plain-English cap explanation using the exact same cap fields as writer.py."""
    parts: list[str] = []

    if c.get("pool_safety_stale"):
        parts.append(
            "Safety status UNVERIFIED — this pool snapshot predates the "
            "current withdrawal/black-box classifier semantics; re-run or "
            "refresh the pool to verify the badge."
        )

    if c.get("safety_cap_applied"):
        badge = c.get("status_badge") or "safety signal detected"
        parts.append(f"Safety cap (hard gate, max 0.400) — {badge}")

    if c.get("unapproved_cap_applied"):
        parts.append(
            "Unapproved-compound cap (drug not yet FDA-approved, hard gate, max 0.400)"
        )

    if c.get("mechanism_cap_applied"):
        mdir = c.get("mechanism_direction") or {}
        auto_flag = " [ICH S7A/S7B auto-precap]" if mdir.get("auto_precap") else ""
        reason_text = (mdir.get("reason") or "")[:250]
        parts.append(
            f"Mechanism-direction cap (DIRECTIONALLY_INCOMPATIBLE{auto_flag}, max 0.400)"
            + (f" — {reason_text}" if reason_text else "")
        )

    return "; ".join(parts) if parts else None


# ── Main audit function ───────────────────────────────────────────────────────

def _modality_payload(drug_name: str) -> dict[str, Any]:
    """Modality finding fields for a drug, via the cached ChEMBL lookup.

    Applies to the DRUG, not the indication, so it is attached on every audit
    path — including early returns where no case or candidate pool exists.
    Unresolved lookups are stated, never silently "clear". Disclosure only.
    """
    mol = get_molecule_data(drug_name)
    mtype = mol.get("molecule_type")
    oral_raw = mol.get("oral")
    oral = None if oral_raw is None else bool(oral_raw)
    findings = modality_finding_for(mtype, oral)
    return {
        "chembl_molecule_type": mtype,
        "chembl_oral": oral,
        "modality_findings": findings,
        "modality_status": (
            "flagged" if findings
            else ("unresolved" if (mtype is None or oral is None) else "clear")
        ),
    }


def _attach_audit_context(
    result: dict[str, Any],
    drug_name: str,
    *,
    mechanism_symbol: str = "",
    claimed_route: str = "",
    claimed_dose: str = "",
    claimed_modality: str = "",
    claimed_context: str = "",
    source_deadline_monotonic: Optional[float] = None,
) -> dict[str, Any]:
    """Attach the shared source/detector envelope without changing verdicts."""
    result["audit_context"] = build_audit_context(
        drug_name,
        mechanism_symbol=mechanism_symbol,
        claimed_route=claimed_route,
        claimed_dose=claimed_dose,
        claimed_modality=claimed_modality,
        claimed_context=claimed_context,
        deadline_monotonic=source_deadline_monotonic,
    )
    return result


def run_audit(
    disease_name: str,
    drug_name: str,
    *,
    job_id_hint: Optional[str] = None,
    narrate: bool = True,
    claimed_route: str = "",
    claimed_dose: str = "",
    claimed_modality: str = "",
    claimed_context: str = "",
    source_deadline_monotonic: Optional[float] = None,
) -> dict[str, Any]:
    """
    Core audit function. Returns a structured dict. Status values:

      "found"         — drug present in pool; full breakdown included
      "absent"        — drug absent; target-mismatch explanation included
      "unresolved"    — drug name could not be resolved to any ChEMBL molecule
                        (typo / brand name / unindexed) — NOT evidence of absence
      "no_case"       — no completed/awaiting_review job for this disease
      "no_candidates" — job exists but candidates file unavailable (pre-persistence)
    """
    if source_deadline_monotonic is None:
        source_deadline_monotonic = time.monotonic() + 30.0

    # 1. Find an existing completed or awaiting_review job
    if job_id_hint:
        job = jobs_db.get_job(job_id_hint)
    else:
        job = jobs_db.find_completed_job_by_disease(disease_name)

    if job is None:
        result = {
            "status": "no_case",
            "disease_name": disease_name,
            "drug_name": drug_name,
            **build_audit_scope_diagnostics("no_case"),
            **_modality_payload(drug_name),
        }
        return _attach_audit_context(
            result, drug_name,
            claimed_route=claimed_route,
            claimed_dose=claimed_dose,
            claimed_modality=claimed_modality,
            claimed_context=claimed_context,
            source_deadline_monotonic=source_deadline_monotonic,
        )

    job_id = job["job_id"]
    canonical_disease = job.get("disease_name") or disease_name

    # 2. Load candidates
    candidates = _load_candidates(job_id, canonical_disease)
    if candidates is None:
        result = {
            "status": "no_candidates",
            "job_id": job_id,
            "disease_name": canonical_disease,
            "drug_name": drug_name,
            "message": (
                "This job predates per-job candidate persistence. "
                "Re-run the disease to generate a fresh candidates file."
            ),
            **build_audit_scope_diagnostics("no_candidates"),
            **_modality_payload(drug_name),
        }
        return _attach_audit_context(
            result, drug_name,
            claimed_route=claimed_route,
            claimed_dose=claimed_dose,
            claimed_modality=claimed_modality,
            claimed_context=claimed_context,
            source_deadline_monotonic=source_deadline_monotonic,
        )

    # 3. Resolve queried drug via the existing ChEMBL best-match function
    chembl_id: Optional[str] = None
    try:
        chembl_id = _find_molecule_chembl_id(drug_name)
    except Exception:
        pass

    # 4. Look up drug in pool
    rank, cand = _find_drug_in_pool(candidates, drug_name, chembl_id)
    top = candidates[0] if candidates else None
    identity_route = _resolved_identity_route(cand, drug_name, chembl_id)
    modality_payload: Optional[dict[str, Any]] = None

    if rank is not None and cand is not None:
        cap = _cap_reason(cand)
        result: dict[str, Any] = {
            "status": "found",
            "job_id": job_id,
            "disease_name": canonical_disease,
            "drug_name": drug_name,
            "resolved_chembl_id": chembl_id,
            "rank": rank,
            "total_candidates": len(candidates),
            "candidate": cand,
            "top_candidate": top,
            "cap_applied": cap is not None,
            "cap_reason": cap,
            **build_audit_scope_diagnostics(
                "found", identity_route=identity_route),
        }

    elif chembl_id is None:
        # The queried name could not be resolved to any ChEMBL molecule. This
        # is NOT evidence of absence — likely a typo, brand name, or compound
        # ChEMBL doesn't index. Returning "absent" here would be a false
        # authoritative claim, so it gets its own honest status.
        result = {
            "status": "unresolved",
            "job_id": job_id,
            "disease_name": canonical_disease,
            "drug_name": drug_name,
            "resolved_chembl_id": None,
            "total_candidates": len(candidates),
            "top_candidate": top,
            **build_audit_scope_diagnostics(
                "unresolved", identity_route=identity_route),
        }

    else:
        # Drug absent — compare its stable mechanism identity with every target
        # represented in the persisted pool. This is supplied-drug evidence
        # review only: it cannot insert the drug or assign a score/rank.
        selected_target = top.get("target_symbol") if top else None

        try:
            mechanism_evidence = get_drug_mechanism_identities_for_audit(
                drug_name, chembl_id)
        except Exception as exc:  # fail closed as source state, not absence
            mechanism_evidence = {
                "status": "unavailable",
                "provider": "chembl",
                "resolved_molecule_chembl_id": chembl_id,
                "identity_route": identity_route,
                "targets": [],
                "error": f"mechanism_lookup_failed:{type(exc).__name__}",
            }
        drug_moa = [
            moa
            for target in mechanism_evidence.get("targets", []) or []
            for moa in target.get("mechanisms", []) or []
        ]
        modality_payload = _modality_payload(drug_name)
        diagnostics = build_audit_scope_diagnostics(
            "absent",
            candidates=candidates,
            mechanism_evidence=mechanism_evidence,
            molecule_type=modality_payload.get("chembl_molecule_type"),
            identity_route=identity_route,
        )

        result = {
            "status": "absent",
            "job_id": job_id,
            "disease_name": canonical_disease,
            "drug_name": drug_name,
            "resolved_chembl_id": chembl_id,
            "total_candidates": len(candidates),
            "top_candidate": top,
            "agentbio_selected_target": selected_target,
            "drug_mechanism_targets": drug_moa,
            "mechanism_evidence": mechanism_evidence,
            "supplied_drug_safety_disclosure": (
                "Regulatory and literature findings below are a disclosure-only "
                "review of the supplied drug. They are not the reviewed-candidate "
                "safety gate and do not create a score, rank, or discovery hit."
            ),
            **diagnostics,
        }

    # 5. Narrate with Opus 4.8 — strictly the computed numbers, no new claims.
    # Unresolved queries skip the LLM: there are no facts to narrate, and a
    # generated paragraph would only risk implying the name was evaluated.
    if result["status"] == "unresolved":
        result["narration"] = (
            f"'{drug_name}' could not be resolved to any molecule in ChEMBL, so "
            "nothing can be said about its standing in this pool. Check the "
            "spelling, or try the INN/generic name rather than a brand name or "
            "salt form."
        )
    elif narrate:
        result["narration"] = _narrate(result)
    else:
        # Batch callers (triage) skip the LLM: verdicts stay deterministic
        # and a long list costs no extra model calls.
        result["narration"] = None

    # 6. Mandatory disclosure, always present regardless of outcome
    result["disclosure"] = (
        "A low rank or absence from the pool does not mean the queried drug is a poor "
        "candidate — it may reflect a gap in public bioactivity or disease-association "
        "data, not a real biological judgment against it."
    )

    # 7. Confirmed research findings applicable to this indication class
    # (base-rate context — disclosure only, never a score or verdict change).
    result["domain_findings"] = domain_findings_for(canonical_disease)

    # 8. Modality finding: applies to the DRUG, not the indication. Live cached
    # lookup so out-of-pool drugs get the same disclosure as reviewed
    # candidates; unresolved lookups are stated, never silently "clear".
    result.update(modality_payload or _modality_payload(drug_name))

    # 9. Structured regulatory + entity-linked literature context. This uses
    # the already-selected target as the bounded mechanism entity where one is
    # available. It is disclosure-only and cannot modify the result above.
    mechanism_symbol = ""
    if result["status"] == "found":
        mechanism_symbol = str(
            (result.get("candidate") or {}).get("target_symbol") or "")
    elif result["status"] == "absent":
        stable_targets = result.get("stable_mechanism_identities") or []
        mechanism_symbol = next(
            (
                str(symbol)
                for target in stable_targets
                for symbol in (target.get("gene_symbols") or [])
                if symbol
            ),
            "",
        )
    _attach_audit_context(
        result, drug_name,
        mechanism_symbol=mechanism_symbol,
        claimed_route=claimed_route,
        claimed_dose=claimed_dose,
        claimed_modality=claimed_modality,
        claimed_context=claimed_context,
        source_deadline_monotonic=source_deadline_monotonic,
    )

    return result


def candidate_pool(
    disease_name: str,
    *,
    job_id_hint: Optional[str] = None,
    query: str = "",
    safety: Optional[str] = None,
    evidence: Optional[str] = None,
    xlogp: Optional[str] = None,
    modality: Optional[str] = None,
    sort: str = "rank",
    order: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Return a filterable, paginated, review-ready candidate pool.

    This intentionally reuses the persisted reviewed-candidate output rather
    than recomputing a run.  Each returned row is a compact index record; the
    full normalized ledger is exposed only by :func:`candidate_evidence`.
    """
    job = jobs_db.get_job(job_id_hint) if job_id_hint else (
        jobs_db.find_completed_job_by_disease(disease_name)
    )
    if job is None:
        return {"status": "no_case", "disease_name": disease_name, "candidates": []}
    canonical_disease = job.get("disease_name") or disease_name
    rows = _load_candidates(job["job_id"], canonical_disease)
    if rows is None:
        return {
            "status": "no_candidates", "job_id": job["job_id"],
            "disease_name": canonical_disease, "candidates": [],
        }

    needle = query.strip().casefold()
    filtered: list[dict[str, Any]] = []
    for rank, candidate in enumerate(rows, start=1):
        if needle and needle not in " ".join(str(candidate.get(k) or "") for k in (
            "drug_name", "molecule_chembl_id", "target_symbol",
        )).casefold():
            continue
        safety_capped = bool(candidate.get("safety_cap_applied"))
        if safety == "capped" and not safety_capped:
            continue
        if safety == "advisory" and not candidate.get("black_box_advisory"):
            continue
        if safety == "clear" and (safety_capped or candidate.get("black_box_advisory")):
            continue
        coverage = (candidate.get("score_components") or {}).get(
            "evidence_weight_coverage"
        )
        if evidence == "complete" and (coverage is None or float(coverage) < 0.999):
            continue
        if evidence == "partial" and (coverage is None or float(coverage) >= 0.999):
            continue
        if xlogp == "flagged" and not candidate.get("high_lipophilicity_flag"):
            continue
        if xlogp == "unresolved" and candidate.get("pubchem_xlogp") is not None:
            continue
        if modality == "flagged" and not candidate.get("nonoral_biologic_flag"):
            continue
        if modality == "unresolved" and candidate.get("nonoral_biologic_flag") is not None:
            continue

        components = candidate.get("score_components") or {}
        filtered.append({
            "rank": rank,
            "drug_name": candidate.get("drug_name"),
            "molecule_chembl_id": candidate.get("molecule_chembl_id"),
            "target_symbol": candidate.get("target_symbol"),
            "composite_score": candidate.get("composite_score"),
            "pre_cap_score": candidate.get("pre_cap_score"),
            "strong_match": candidate.get("strong_match"),
            "is_approved_drug": candidate.get("is_approved_drug"),
            "pubchem_xlogp": candidate.get("pubchem_xlogp"),
            "high_lipophilicity_flag": candidate.get("high_lipophilicity_flag"),
            "xlogp_status": (
                "flagged" if candidate.get("high_lipophilicity_flag")
                else ("unresolved" if candidate.get("pubchem_xlogp") is None else "clear")
            ),
            "chembl_molecule_type": candidate.get("chembl_molecule_type"),
            "chembl_oral": candidate.get("chembl_oral"),
            "nonoral_biologic_flag": candidate.get("nonoral_biologic_flag"),
            "modality_status": (
                "flagged" if candidate.get("nonoral_biologic_flag")
                else ("unresolved" if candidate.get("nonoral_biologic_flag") is None
                      else "clear")
            ),
            "evidence_weight_coverage": components.get("evidence_weight_coverage"),
            "source_types": candidate.get("source_types") or [],
            "safety_cap_applied": safety_capped,
            "mechanism_cap_applied": bool(candidate.get("mechanism_cap_applied")),
            "unapproved_cap_applied": bool(candidate.get("unapproved_cap_applied")),
            "black_box_advisory": bool(candidate.get("black_box_advisory")),
            "status_badge": candidate.get("status_badge"),
            "target_discovery_method": candidate.get("target_discovery_method"),
        })

    reverse = order.lower() == "desc"
    sort_keys = {
        "rank": lambda row: row["rank"],
        "score": lambda row: row.get("composite_score") or 0.0,
        "coverage": lambda row: row.get("evidence_weight_coverage") or 0.0,
        "drug": lambda row: (row.get("drug_name") or "").casefold(),
        "xlogp": lambda row: row.get("pubchem_xlogp") if row.get("pubchem_xlogp") is not None else -999.0,
    }
    if sort not in sort_keys:
        sort = "rank"
    filtered.sort(key=sort_keys[sort], reverse=reverse)
    page = max(1, page)
    page_size = max(5, min(100, page_size))
    start = (page - 1) * page_size
    return {
        "status": "ok",
        "job_id": job["job_id"],
        "disease_name": canonical_disease,
        "total": len(filtered),
        "page": page,
        "page_size": page_size,
        "candidates": filtered[start:start + page_size],
        "filter_options": {
            "safety": ["clear", "advisory", "capped"],
            "evidence": ["complete", "partial"],
            "xlogp": ["flagged", "unresolved"],
        },
    }


def candidate_evidence(
    disease_name: str,
    drug_name: str,
    *,
    job_id_hint: Optional[str] = None,
) -> dict[str, Any]:
    """Return one candidate with normalized, human-readable evidence cards."""
    job = jobs_db.get_job(job_id_hint) if job_id_hint else (
        jobs_db.find_completed_job_by_disease(disease_name)
    )
    if job is None:
        return {"status": "no_case", "disease_name": disease_name, "drug_name": drug_name}
    canonical_disease = job.get("disease_name") or disease_name
    rows = _load_candidates(job["job_id"], canonical_disease)
    if rows is None:
        return {"status": "no_candidates", "job_id": job["job_id"]}
    rank, candidate = _find_drug_in_pool(rows, drug_name, None)
    if candidate is None:
        return {"status": "not_found", "job_id": job["job_id"], "drug_name": drug_name}

    cards: list[dict[str, Any]] = []
    for record in (candidate.get("_evidence_ledger") or {}).get("records") or []:
        if not isinstance(record, dict):
            continue
        provider = record.get("provider") or "unknown"
        source_id = record.get("source_id") or ""
        publication = record.get("publication_id")
        identifier = publication or record.get("assay_id") or source_id
        url = None
        if publication:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{publication}/"
        elif str(provider).lower() == "chembl" and candidate.get("molecule_chembl_id"):
            url = f"https://www.ebi.ac.uk/chembl/compound_report_card/{candidate['molecule_chembl_id']}/"
        cards.append({
            "source": provider,
            "claim": record.get("evidence_role") or record.get("source_type"),
            "measurement_type": record.get("measurement_type"),
            "measurement_value": record.get("measurement_value"),
            "measurement_unit": record.get("measurement_unit"),
            "identifier": identifier,
            "url": url,
            "retrieved_at": record.get("retrieved_at"),
            "confidence": record.get("qualification_status"),
            "limitations": record.get("context") or (
                "Source-level evidence; it does not independently establish clinical benefit."
            ),
            "action": record.get("action"),
            "direction": record.get("direction"),
        })
    return {
        "status": "ok", "job_id": job["job_id"], "disease_name": canonical_disease,
        "rank": rank, "candidate": candidate, "evidence": cards,
    }


# ── Narration ─────────────────────────────────────────────────────────────────

def _narrate(result: dict) -> str:
    """Single Anthropic Opus call to narrate already-computed numbers."""
    try:
        import os as _os
        from anthropic import Anthropic
        client = Anthropic(
            base_url=_os.environ["AI_INTEGRATIONS_ANTHROPIC_BASE_URL"],
            api_key=_os.environ["AI_INTEGRATIONS_ANTHROPIC_API_KEY"],
        )
        facts = _facts_for_narration(result)
        msg = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=400,
            messages=[{"role": "user", "content": (
                "You are narrating a drug-repurposing audit result for a scientist. "
                "Narrate ONLY the numbered facts below — do not add new claims, do not "
                "speculate about biology, and do not evaluate the drug's candidacy. "
                "Write 2–4 concise sentences.\n\n"
                f"Facts:\n{facts}"
            )}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        return f"[narration unavailable: {exc}]"


def _facts_for_narration(r: dict) -> str:
    lines = [
        f"Disease: {r['disease_name']}",
        f"Queried drug: {r['drug_name']}",
        f"Resolved ChEMBL ID: {r.get('resolved_chembl_id') or 'not found in ChEMBL'}",
    ]
    status = r["status"]

    if status == "found":
        c = r["candidate"]
        lines += [
            f"Rank in reviewed pool: {r['rank']} of {r['total_candidates']}",
            f"Composite score: {c.get('composite_score', 0):.4f}",
            f"Strong match (≥0.70 threshold): {'yes' if c.get('strong_match') else 'no'}",
            f"Target AgentBio evaluated this drug against: {c.get('target_symbol')}",
            f"ChEMBL pChEMBL affinity: {c.get('pchembl_value')}",
            f"Open Targets association score: {c.get('ot_association_score')}",
        ]
        cap = r.get("cap_reason")
        lines.append(f"Score cap applied: {cap or 'none'}")
        top = r.get("top_candidate") or {}
        if top.get("drug_name") != c.get("drug_name"):
            lines.append(
                f"AgentBio's top-ranked candidate for this disease: "
                f"{top.get('drug_name')} (composite {top.get('composite_score', 0):.4f})"
            )

    elif status == "absent":
        lines += [
            f"Drug not found among {r['total_candidates']} reviewed candidates",
            f"AgentBio's selected target for this disease: "
            f"{r.get('agentbio_selected_target') or 'unknown'}",
            f"Drug's ChEMBL mechanism-of-action targets: "
            f"{', '.join(r.get('drug_mechanism_targets', [])) or 'none recorded in ChEMBL'}",
        ]
        top = r.get("top_candidate") or {}
        lines.append(
            f"AgentBio's top-ranked candidate: "
            f"{top.get('drug_name')} (composite {top.get('composite_score', 0):.4f})"
        )

    return "\n".join(lines)
