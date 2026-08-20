"""
Candidate-list triage: adversarially audit a caller-supplied list of drugs
against the persisted reviewed-candidates pool of one completed case.

This is the "bring your own list" entry of Audit mode. It reuses run_audit()
verbatim — the same resolution, the same cap fields, the same honest statuses —
with LLM narration disabled so a 25-drug list costs no extra model calls and
every verdict is deterministic given the pool.

Every triage run is persisted to Postgres (api/triage_db.py) so the verdict a
caller saw can be retrieved later by run id — the audit trail is the product.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
from typing import Any, Optional

import api.triage_db as triage_db
from api.audit import run_audit
from api.domain_findings import domain_findings_for

MAX_TRIAGE_DRUGS = 25

# Coded flags surfaced per verdict row. These are deliberately mechanical
# mappings of the same candidate fields the dossier writer reads — no new
# judgment layer that could drift from the case dossier.
F_SAFETY_CAP = "SAFETY_CAP"
F_MECHANISM_CAP = "MECHANISM_CAP"
F_UNAPPROVED_CAP = "UNAPPROVED_CAP"
F_BLACK_BOX = "BLACK_BOX_ADVISORY"
F_XLOGP_CAUTION = "XLOGP_CAUTION"
F_XLOGP_UNRESOLVED = "XLOGP_UNRESOLVED"
F_MODALITY_CAUTION = "MODALITY_CAUTION"
F_MODALITY_UNRESOLVED = "MODALITY_UNRESOLVED"
F_EVIDENCE_PARTIAL = "EVIDENCE_PARTIAL"
F_ABSENT = "ABSENT_FROM_POOL"
F_SUPPLIED_ONLY = "AUDITABLE_SUPPLIED_ONLY"
F_MECHANISM_SOURCE_FAILURE = "MECHANISM_SOURCE_FAILURE"
F_UNRESOLVED = "UNRESOLVED_NAME"
F_NO_CASE = "NO_CASE"


def _dedup_preserving_order(drug_names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in drug_names:
        cleaned = " ".join(str(name).split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out


def _verdict(drug_name: str, audit: dict[str, Any]) -> dict[str, Any]:
    """Collapse one run_audit result into a triage verdict row + coded flags."""
    status = audit.get("status")
    flags: list[str] = []
    row: dict[str, Any] = {
        "drug_name": drug_name,
        "status": status,
        "audit_scope_status": audit.get("audit_scope_status"),
        "deterministic_miss_reason": audit.get("deterministic_miss_reason"),
        "resolved_identity_route": audit.get("resolved_identity_route"),
        "resolved_chembl_id": audit.get("resolved_chembl_id"),
        "rank": None,
        "total_candidates": audit.get("total_candidates"),
        "composite_score": None,
        "pre_cap_score": None,
        "strong_match": None,
        "target_symbol": None,
        "cap_reason": None,
        "pubchem_xlogp": None,
        "xlogp_status": None,
        "evidence_weight_coverage": None,
        "black_box_advisory": False,
        "status_badge": None,
        # The exact object produced by run_audit. Triage never reinterprets
        # N1–N4 findings, which guarantees single/batch parity by construction.
        "audit_context": audit.get("audit_context"),
        "mechanism_evidence_status": audit.get("mechanism_evidence_status"),
        "stable_mechanism_identities": audit.get(
            "stable_mechanism_identities") or [],
        "pool_target_overlap": audit.get("pool_target_overlap") or [],
        "target_coverage_ladder": audit.get("target_coverage_ladder") or [],
    }

    if status == "no_case":
        flags.append(F_NO_CASE)
    elif status == "unresolved":
        flags.append(F_UNRESOLVED)
    elif status == "absent":
        flags.append(F_ABSENT)
        if audit.get("audit_scope_status") == "auditable_only_because_supplied":
            flags.append(F_SUPPLIED_ONLY)
        elif audit.get("audit_scope_status") == "source_failure":
            flags.append(F_MECHANISM_SOURCE_FAILURE)
        row["agentbio_selected_target"] = audit.get("agentbio_selected_target")
        row["drug_mechanism_targets"] = audit.get("drug_mechanism_targets")
    elif status == "found":
        cand = audit.get("candidate") or {}
        components = cand.get("score_components") or {}
        coverage = components.get("evidence_weight_coverage")
        row.update({
            "rank": audit.get("rank"),
            "composite_score": cand.get("composite_score"),
            "pre_cap_score": cand.get("pre_cap_score"),
            "strong_match": cand.get("strong_match"),
            "target_symbol": cand.get("target_symbol"),
            "cap_reason": audit.get("cap_reason"),
            "pubchem_xlogp": cand.get("pubchem_xlogp"),
            "xlogp_status": (
                "flagged" if cand.get("high_lipophilicity_flag")
                else ("unresolved" if cand.get("pubchem_xlogp") is None else "clear")
            ),
            "chembl_molecule_type": cand.get("chembl_molecule_type"),
            "chembl_oral": cand.get("chembl_oral"),
            "modality_status": (
                "flagged" if cand.get("nonoral_biologic_flag")
                else ("unresolved" if cand.get("nonoral_biologic_flag") is None
                      else "clear")
            ),
            "evidence_weight_coverage": coverage,
            "black_box_advisory": bool(cand.get("black_box_advisory")),
            "status_badge": cand.get("status_badge"),
        })
        if cand.get("safety_cap_applied"):
            flags.append(F_SAFETY_CAP)
        if cand.get("mechanism_cap_applied"):
            flags.append(F_MECHANISM_CAP)
        if cand.get("unapproved_cap_applied"):
            flags.append(F_UNAPPROVED_CAP)
        if cand.get("black_box_advisory"):
            flags.append(F_BLACK_BOX)
        if cand.get("high_lipophilicity_flag"):
            flags.append(F_XLOGP_CAUTION)
        elif cand.get("pubchem_xlogp") is None:
            flags.append(F_XLOGP_UNRESOLVED)
        if cand.get("nonoral_biologic_flag"):
            flags.append(F_MODALITY_CAUTION)
        elif cand.get("nonoral_biologic_flag") is None:
            flags.append(F_MODALITY_UNRESOLVED)
        if coverage is not None and float(coverage) < 0.999:
            flags.append(F_EVIDENCE_PARTIAL)

    row["flags"] = flags
    return row


def _summary(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    by_audit_scope_status: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for v in verdicts:
        s = str(v.get("status"))
        by_status[s] = by_status.get(s, 0) + 1
        scope = str(v.get("audit_scope_status"))
        by_audit_scope_status[scope] = by_audit_scope_status.get(scope, 0) + 1
        for f in v.get("flags") or []:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    return {
        "total": len(verdicts),
        "by_status": by_status,
        "by_audit_scope_status": by_audit_scope_status,
        "flag_counts": flag_counts,
        "flagged_total": sum(1 for v in verdicts if v.get("flags")),
    }


def run_triage(
    disease_name: str,
    drug_names: list[str],
    *,
    job_id_hint: Optional[str] = None,
    persist: bool = True,
    claim_contexts: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Audit a list of drugs against one case's persisted pool.

    Returns {status, run_id?, disease_name, job_id?, verdicts, summary,
    disclosure}. Per-drug statuses reuse run_audit's honest statuses; a
    no_case/no_candidates pool state short-circuits the whole list rather
    than repeating the same pool-level verdict 25 times.
    """
    drugs = _dedup_preserving_order(drug_names)
    if not drugs:
        return {"status": "empty_list", "verdicts": [], "summary": _summary([])}

    contexts = {
        str(name).casefold(): value
        for name, value in (claim_contexts or {}).items()
        if isinstance(value, dict)
    }
    source_deadline = time.monotonic() + 30.0

    def _audit_one(name: str) -> dict[str, Any]:
        try:
            claim = contexts.get(name.casefold(), {})
            return run_audit(disease_name, name, job_id_hint=job_id_hint,
                             narrate=False,
                             claimed_route=str(claim.get("route") or ""),
                             claimed_dose=str(claim.get("dose") or ""),
                             claimed_modality=str(claim.get("modality") or ""),
                             claimed_context=str(claim.get("context") or ""),
                             source_deadline_monotonic=source_deadline)
        except Exception as exc:  # noqa: BLE001 — per-drug failure must not sink the batch
            return {"status": "error", "drug_name": name, "error": str(exc)[:200]}

    # Small bounded fan-out: each audit is one ChEMBL name resolution plus a
    # local pool lookup. 4 workers keeps source fan-out inside the same
    # throttle budget the rest of the API uses.
    with ThreadPoolExecutor(max_workers=4) as pool:
        audits = list(pool.map(_audit_one, drugs))

    verdicts = [_verdict(name, a) for name, a in zip(drugs, audits)]
    summary = _summary(verdicts)
    # Confirmed research findings for this indication class (base-rate
    # context only — never touch verdicts). Stored inside the persisted
    # summary so a retrieved run reproduces exactly what the caller saw.
    findings = domain_findings_for(disease_name)
    if findings:
        summary["domain_findings"] = findings
    summary["audit_context_contract_version"] = "audit-context-v2"
    summary["audit_context_source_states"] = {
        drug: {
            name: (source or {}).get("status")
            for name, source in (
                ((audit.get("audit_context") or {}).get("sources") or {}).items()
            )
        }
        for drug, audit in zip(drugs, audits)
    }

    pool_status = next(
        (a.get("status") for a in audits
         if a.get("status") in ("no_case", "no_candidates")),
        None,
    )
    job_id = next((a.get("job_id") for a in audits if a.get("job_id")), None)

    result: dict[str, Any] = {
        "status": pool_status or "ok",
        "disease_name": disease_name,
        "job_id": job_id,
        "domain_findings": findings,
        "verdicts": verdicts,
        "summary": summary,
        "disclosure": (
            "Triage verdicts are computed against the persisted candidate pool "
            "of one completed AgentBio case — they re-audit evidence, they do "
            "not re-run discovery. ABSENT_FROM_POOL is a coverage statement, "
            "not a judgment that the drug is a poor candidate. XLogP flags are "
            "caution-only disclosures and never adjust scores."
        ),
    }

    if persist and pool_status is None:
        stored = triage_db.save_triage_run(
            disease_name=disease_name, job_id=job_id, drugs=drugs,
            results=verdicts, summary=summary,
        )
        result["run_id"] = stored["id"]
        result["created_at"] = stored["created_at"].isoformat() if stored.get("created_at") else None

    return result
