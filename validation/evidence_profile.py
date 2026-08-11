"""Deterministic per-pair evidence profile for the triage discrimination study.

The audit layer emits disclosure-only findings, and triage collapses them
into coded flags. Neither is a *comparable* per-pair output: there is no
single object that says "here is what the layer concluded about this
drug for this disease" in a form two pairs can be ranked against. Without
one, "compare the confirmed drug against the pipeline's top pick" has
nothing to compare.

This module defines that object. Three rules govern it:

1. **It measures the shipped instrument.** The profile is derived from
   `api.triage._verdict()` over a `run_audit()` result — the exact code
   path the product runs — not a reimplementation. If triage changes, the
   profile changes with it, which is the point: the study should score
   what ships.
2. **No judgment in the scored path.** Every dimension is a mechanical
   read of a field or finding status. No LLM, no heuristics tuned on
   outcomes.
3. **Unresolved is not negative.** Every dimension separates "the layer
   found a problem" from "the layer could not tell". Collapsing those two
   is what makes an audit instrument look decisive when it is merely
   uninformed, and it is the specific failure audit claim-set v1 made.

The disposition rule below is the study's pre-registered scoring
function. It is fixed before any case is built and must never be tuned
against results; `RULE_FINGERPRINT` binds it so the freeze can prove it
did not move.

DISCLOSURE-ONLY IS PRESERVED. Nothing here feeds a score, rank, or cap.
The profile exists only inside the validation harness.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.triage import _verdict  # noqa: E402

PROFILE_CONTRACT = "evidence-profile-v1"

# --- Dimension vocabularies -------------------------------------------------
# Pool state. ABSENT is a statement about pipeline COVERAGE, not about the
# hypothesis: the pipeline never generated the drug, so the audit layer was
# never given the chance to judge it. It is therefore never scored as a
# disqualification.
POOL_IN = "IN_POOL"
POOL_ABSENT = "ABSENT_FROM_POOL"
POOL_UNRESOLVED = "UNRESOLVED_NAME"
POOL_NO_CASE = "NO_CASE"

COMPATIBLE, INCOMPATIBLE, UNRESOLVED, CLEAR, FLAGGED, REVIEW = (
    "COMPATIBLE", "INCOMPATIBLE", "UNRESOLVED", "CLEAR", "FLAGGED", "REVIEW")
HUMAN_PRESENT, PRECLINICAL_ONLY = "HUMAN_PRESENT", "PRECLINICAL_ONLY"
CAPPED, ADVISORY = "CAPPED", "ADVISORY"

# --- Pre-registered disposition rule ---------------------------------------
# A HARD disqualifier is a finding a reviewer would reasonably treat as
# reason to drop the hypothesis outright. A SOFT caution is a finding that
# qualifies the hypothesis without killing it.
#
# Deliberately NOT hard disqualifiers:
#   * ABSENT_FROM_POOL  - a coverage gap, not a negative judgment.
#   * any UNRESOLVED    - absence of evidence is not evidence of absence.
#   * BLACK_BOX_ADVISORY- an approved-and-marketed drug carries a black box;
#                         treating it as disqualifying would fail most real
#                         repurposings by construction.
HARD_DISQUALIFIERS = (
    ("mechanism_direction", INCOMPATIBLE),
    ("safety", CAPPED),
    ("modality_feasibility", FLAGGED),
    ("human_mechanism_evidence", PRECLINICAL_ONLY),
)
SOFT_CAUTIONS = (
    ("route_feasibility", FLAGGED),  # Amendment 1: detected route contradiction
    ("route_feasibility", REVIEW),
    ("lipophilicity", FLAGGED),
    ("safety", ADVISORY),
    ("evidence_coverage", "PARTIAL"),
)

DISQUALIFIED = "DISQUALIFIED"
QUALIFIED = "QUALIFIED"
SUPPORTED = "SUPPORTED"
NOT_ASSESSABLE = "NOT_ASSESSABLE"

# --- Dimension dependency split --------------------------------------------
# The self-critique (validation/audit-discrimination-critique.md) found that
# the persisted pool is NOT disease-blind: agents/reviewer.py computes
# composite_score/rank from disease-linked OpenTargets and trial data, and
# check_mechanism_direction(..., disease, ...) takes the disease name. Any
# dimension read from the pool therefore inherits the disease-side signal the
# study claims to hold out, which would make a scored claim circular.
#
# DISEASE_INDEPENDENT dimensions come only from build_audit_context (label and
# literature lanes, redacted and proven blind in Step 1) or from drug-side
# attributes. They need NO pool and NO pipeline run, so the PRIMARY
# false-disqualification metric reads only these.
#
# DISEASE_DEPENDENT dimensions are read from the persisted pool and so are NOT
# provably blind. They are reported descriptively only and are NEVER scored
# into the primary claim.
DISEASE_INDEPENDENT_DIMS = frozenset({
    "modality_feasibility",     # N2 finding (redacted label lane)
    "route_feasibility",        # N4 finding (redacted label lane)
    "human_mechanism_evidence", # N3 finding (redacted literature lane)
    "lipophilicity",            # drug-side PubChem XLogP attribute
    "combination_product",      # N1 finding (redacted label lane)
})
DISEASE_DEPENDENT_DIMS = frozenset({
    "pool_presence",            # needs a persisted pool
    "rank",                     # pool sort over disease-linked composite
    "rank_fraction",            # derived from rank
    "total_candidates",         # pool size
    "mechanism_direction",      # check_mechanism_direction(..., disease, ...)
    "safety",                   # safety_cap read from pool candidate
    "evidence_coverage",        # pool score-component coverage
})


def _finding_statuses(audit_context: Any) -> dict[str, set[str]]:
    """Map finding code -> set of statuses emitted for it."""
    out: dict[str, set[str]] = {}
    for finding in (audit_context or {}).get("findings") or []:
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "")
        out.setdefault(code, set()).add(str(finding.get("status") or ""))
    return out


def build_profile(drug_name: str, audit: dict[str, Any]) -> dict[str, Any]:
    """Collapse one `run_audit()` result into the comparable profile.

    `audit` must have been produced with the disease-side holdout active,
    so the audit lanes are disease-blind (see
    `validation/audit_lane_holdout_gate.md`). This function does not
    enforce that -- the harness does, at freeze time -- because the
    profile is also used to describe control pairs that are not held out.
    """
    verdict = _verdict(drug_name, audit)
    flags = set(verdict.get("flags") or [])
    context = verdict.get("audit_context") or {}
    findings = _finding_statuses(context)
    status = verdict.get("status")

    if status == "no_case" or status == "no_candidates":
        pool = POOL_NO_CASE
    elif status == "unresolved":
        pool = POOL_UNRESOLVED
    elif status == "absent":
        pool = POOL_ABSENT
    else:
        pool = POOL_IN

    rank = verdict.get("rank")
    total = verdict.get("total_candidates")
    rank_fraction = (
        round(float(rank) / float(total), 6)
        if pool == POOL_IN and rank and total else None)

    n1 = findings.get("N1", set())
    n2 = findings.get("N2", set())
    n3 = findings.get("N3", set())
    n4 = findings.get("N4", set())

    dimensions = {
        "pool_presence": pool,
        "rank": rank if pool == POOL_IN else None,
        "total_candidates": total,
        "rank_fraction": rank_fraction,
        "mechanism_direction": (
            INCOMPATIBLE if "MECHANISM_CAP" in flags
            else (COMPATIBLE if pool == POOL_IN else UNRESOLVED)),
        "human_mechanism_evidence": (
            PRECLINICAL_ONLY if "flagged" in n3
            else (UNRESOLVED if "unresolved" in n3 else HUMAN_PRESENT)),
        # Amendment 2: every emitted N1/N2 status has an explicit mapping.
        # N2 "review" (biologic without a claimed modality) surfaces as
        # REVIEW — biologics are legitimate repurposings, so it is
        # deliberately NOT a caution. N1 (combination product) is a
        # claim-framing fact, not a candidate defect: descriptive only.
        "modality_feasibility": (
            FLAGGED if ("MODALITY_CAUTION" in flags or "flagged" in n2)
            else (REVIEW if "review" in n2
                  else (UNRESOLVED if ("MODALITY_UNRESOLVED" in flags
                                       or "unresolved" in n2) else CLEAR))),
        "combination_product": (
            FLAGGED if "flagged" in n1
            else (UNRESOLVED if "unresolved" in n1 else CLEAR)),
        "route_feasibility": (
            # Amendment 1: N4 "flagged" (claimed route not among approved
            # label routes) must surface as FLAGGED. The original mapping
            # let it fall through to CLEAR, which silently hid every
            # detected route contradiction (found by NC2 post-freeze).
            FLAGGED if "flagged" in n4
            else (REVIEW if "review" in n4
                  else (UNRESOLVED if "unresolved" in n4 else CLEAR))),
        "safety": (
            CAPPED if "SAFETY_CAP" in flags
            else (ADVISORY if "BLACK_BOX_ADVISORY" in flags else CLEAR)),
        "lipophilicity": (
            FLAGGED if "XLOGP_CAUTION" in flags
            else (UNRESOLVED if "XLOGP_UNRESOLVED" in flags else CLEAR)),
        "evidence_coverage": (
            "PARTIAL" if "EVIDENCE_PARTIAL" in flags else "COMPLETE"),
    }

    if pool in (POOL_NO_CASE, POOL_UNRESOLVED):
        disposition = NOT_ASSESSABLE
        fired_hard: list[str] = []
        fired_soft: list[str] = []
    else:
        fired_hard = [
            f"{dim}={value}" for dim, value in HARD_DISQUALIFIERS
            if dimensions.get(dim) == value]
        fired_soft = [
            f"{dim}={value}" for dim, value in SOFT_CAUTIONS
            if dimensions.get(dim) == value]
        if fired_hard:
            disposition = DISQUALIFIED
        elif fired_soft:
            disposition = QUALIFIED
        else:
            disposition = SUPPORTED

    # Primary disposition: scored ONLY on the disease-independent dimensions,
    # so it needs no pool and stays on provably disease-blind data. A pair can
    # carry a primary disposition even when the overall disposition is
    # NOT_ASSESSABLE (no case / unresolved name), because the primary claim
    # never touches the pool.
    primary_hard = [
        f"{dim}={value}" for dim, value in HARD_DISQUALIFIERS
        if dim in DISEASE_INDEPENDENT_DIMS and dimensions.get(dim) == value]
    primary_soft = [
        f"{dim}={value}" for dim, value in SOFT_CAUTIONS
        if dim in DISEASE_INDEPENDENT_DIMS and dimensions.get(dim) == value]
    if primary_hard:
        primary_disposition = DISQUALIFIED
    elif primary_soft:
        primary_disposition = QUALIFIED
    else:
        primary_disposition = SUPPORTED

    return {
        "contract": PROFILE_CONTRACT,
        "rule_fingerprint": RULE_FINGERPRINT,
        "drug_name": drug_name,
        "audit_status": status,
        "dimensions": dimensions,
        "dimension_dependency": {
            "disease_independent": sorted(DISEASE_INDEPENDENT_DIMS),
            "disease_dependent": sorted(DISEASE_DEPENDENT_DIMS),
        },
        "hard_disqualifiers_fired": fired_hard,
        "soft_cautions_fired": fired_soft,
        "disposition": disposition,
        "primary_disposition": primary_disposition,
        "primary_hard_fired": primary_hard,
        "primary_soft_fired": primary_soft,
        "flags": sorted(flags),
        "holdout_redaction_applied": bool(
            (context.get("holdout_redaction") or {}).get("applied")),
    }


def _fingerprint() -> str:
    """Bind the scoring rule so a freeze can prove it did not move."""
    payload = json.dumps({
        "contract": PROFILE_CONTRACT,
        "hard": [list(item) for item in HARD_DISQUALIFIERS],
        "soft": [list(item) for item in SOFT_CAUTIONS],
        "dispositions": [DISQUALIFIED, QUALIFIED, SUPPORTED, NOT_ASSESSABLE],
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


RULE_FINGERPRINT = _fingerprint()


if __name__ == "__main__":
    print(f"{PROFILE_CONTRACT} rule fingerprint: {RULE_FINGERPRINT}")
