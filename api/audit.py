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
from typing import Any, Optional

import api.jobs_db as jobs_db
from api.domain_findings import domain_findings_for
from data_sources.chembl import (
    _find_molecule_chembl_id,
    get_drug_mechanism_targets_for_audit,
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
            return json.load(fh).get("candidates", [])

    if os.path.exists(_SHARED_PATH):
        with open(_SHARED_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        cands = data.get("candidates", [])
        if cands:
            # Safe to use only if the last run was for this same disease
            sample_disease = (cands[0].get("disease_name") or "").lower()
            if sample_disease == canonical_disease.lower():
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


# ── Cap disclosure ─────────────────────────────────────────────────────────────

def _cap_reason(c: dict) -> Optional[str]:
    """Plain-English cap explanation using the exact same cap fields as writer.py."""
    parts: list[str] = []

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

def run_audit(
    disease_name: str,
    drug_name: str,
    *,
    job_id_hint: Optional[str] = None,
    narrate: bool = True,
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
    # 1. Find an existing completed or awaiting_review job
    if job_id_hint:
        job = jobs_db.get_job(job_id_hint)
    else:
        job = jobs_db.find_completed_job_by_disease(disease_name)

    if job is None:
        return {
            "status": "no_case",
            "disease_name": disease_name,
            "drug_name": drug_name,
        }

    job_id = job["job_id"]
    canonical_disease = job.get("disease_name") or disease_name

    # 2. Load candidates
    candidates = _load_candidates(job_id, canonical_disease)
    if candidates is None:
        return {
            "status": "no_candidates",
            "job_id": job_id,
            "disease_name": canonical_disease,
            "drug_name": drug_name,
            "message": (
                "This job predates per-job candidate persistence. "
                "Re-run the disease to generate a fresh candidates file."
            ),
        }

    # 3. Resolve queried drug via the existing ChEMBL best-match function
    chembl_id: Optional[str] = None
    try:
        chembl_id = _find_molecule_chembl_id(drug_name)
    except Exception:
        pass

    # 4. Look up drug in pool
    rank, cand = _find_drug_in_pool(candidates, drug_name, chembl_id)
    top = candidates[0] if candidates else None

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
        }

    else:
        # Drug absent — explain why via target comparison
        selected_target = top.get("target_symbol") if top else None

        drug_moa: list[str] = []
        try:
            drug_moa = get_drug_mechanism_targets_for_audit(drug_name, chembl_id)
        except Exception:
            pass

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

    return result


def candidate_pool(
    disease_name: str,
    *,
    job_id_hint: Optional[str] = None,
    query: str = "",
    safety: Optional[str] = None,
    evidence: Optional[str] = None,
    xlogp: Optional[str] = None,
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
