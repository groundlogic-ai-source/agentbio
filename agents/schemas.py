"""
Stage-handoff schemas for the drug-repurposing pipeline.

Defines TypedDict types for the data objects passed between pipeline stages
and a runtime validation function called after each handoff.  The goal is to
catch field-dropout bugs (like the uniprot_id/target_discovery_method pattern)
at the handoff boundary rather than discovering them in a downstream report.

Background: three confirmed field-dropout bugs were found before this module
existed — all caused by a reviewed.append() / chemist output dict that did not
explicitly include the field.  This module converts "found by accident, three
times" into "caught automatically, always."

Validation is WARN-only (never raises) in production to avoid crashing the
pipeline on a missing optional field.  Set STRICT_VALIDATION=true in the
environment to make validation errors hard-fail (useful in testing).
"""

import os
from typing import Any, Optional
from typing_extensions import TypedDict, Required

STRICT_VALIDATION = os.environ.get("STRICT_VALIDATION", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# TypedDict definitions
# ---------------------------------------------------------------------------

class ChemistCandidate(TypedDict, total=False):
    """Fields the Chemist must produce for every candidate passed to the Reviewer."""
    drug_name: Required[str]
    molecule_chembl_id: Optional[str]
    target_symbol: Required[str]
    smiles: Optional[str]
    pchembl_value: Optional[float]
    confidence_score: Optional[int]
    efficacy_confidence: Optional[float]
    ot_association_score: Optional[float]
    tanimoto_score: Optional[float]
    most_similar_approved_drug: Optional[str]
    is_approved_drug: Optional[bool]
    rationale: Optional[str]
    source_activity_ids: list
    source_chembl_ids: list
    # REQUIRED for correct Boltz folding in structure_validation_node.
    # Every pathway_neighbor candidate must carry its own UniProt accession,
    # distinct from the primary target's accession.
    uniprot_id: Required[Optional[str]]
    # REQUIRED so the report writer can disclose HOW the target was found.
    target_discovery_method: Required[str]
    mutation_specificity: Optional[dict]
    source_types: list[str]
    source_health: dict
    target_memberships: list[dict]
    _evidence_ledger: dict
    mechanism_class: Optional[str]
    therapeutic_role: str
    process_support: list[dict]
    process_source_status: Optional[str]


class ReviewerCandidate(TypedDict, total=False):
    """Fields the Reviewer must produce for every candidate passed to the Writer."""
    drug_name: Required[str]
    molecule_chembl_id: Optional[str]
    target_symbol: Required[str]
    disease_name: Required[str]
    smiles: Optional[str]
    pchembl_value: Optional[float]
    confidence_score: Optional[int]
    efficacy_confidence: Optional[float]
    ot_association_score: Optional[float]
    tanimoto_score: Optional[float]
    composite_score: Required[float]
    # Composite BEFORE any cap (unapproved/mechanism/DILI/safety).  Secondary
    # sort key so strong-but-capped candidates outrank weak ones at the same
    # cap floor; never used for STRONG_MATCH gating.
    pre_cap_score: Optional[float]
    strong_match: Required[bool]
    is_approved_drug: Optional[bool]
    unapproved_cap_applied: Required[bool]
    mechanism_cap_applied: Required[bool]
    mechanism_direction: Optional[dict]
    safety_cap_applied: Required[bool]
    black_box_advisory: Optional[bool]   # BBW present but drug NOT withdrawn
    trials_query_failed: Required[bool]
    prior_trial_count: int
    # REQUIRED: must not be dropped by reviewer.append() or structure_validation
    # falls back to the PRIMARY target's UniProt for all pathway_neighbor candidates.
    uniprot_id: Required[Optional[str]]
    # REQUIRED: must be carried through every stage handoff.
    target_discovery_method: Required[str]
    # High-lipophilicity disclosure (XLogP >= 5 from PubChem). Disclosure only.
    pubchem_xlogp: Optional[float]
    high_lipophilicity_flag: Optional[bool]
    source_types: list[str]
    source_health: dict
    target_memberships: list[dict]
    _evidence_ledger: dict
    mechanism_class: Optional[str]
    therapeutic_role: str
    process_support: list[dict]
    process_source_status: Optional[str]


# ---------------------------------------------------------------------------
# Field specs for runtime validation
# ---------------------------------------------------------------------------

# (field_name, severity)
# severity "error" → logged as ERROR (hard-fail if STRICT_VALIDATION=true)
# severity "warn"  → logged as WARNING only
_CHEMIST_REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("drug_name",               "error"),
    ("target_symbol",           "error"),
    ("uniprot_id",              "error"),   # None is OK; key must be present
    ("target_discovery_method", "error"),
    ("_evidence_ledger",        "error"),
    ("source_health",           "warn"),
    ("smiles",                  "warn"),    # None is OK but absence is suspicious
    ("is_approved_drug",        "warn"),
]

_REVIEWER_REQUIRED_FIELDS: list[tuple[str, str]] = [
    ("drug_name",               "error"),
    ("target_symbol",           "error"),
    ("disease_name",            "error"),
    ("composite_score",         "error"),
    ("strong_match",            "error"),
    ("unapproved_cap_applied",  "error"),
    ("mechanism_cap_applied",   "error"),
    ("safety_cap_applied",      "error"),
    ("trials_query_failed",     "error"),
    ("uniprot_id",              "error"),   # None is OK; key must be present
    ("target_discovery_method", "error"),
    ("_evidence_ledger",        "error"),
    # warn-level: old persisted reviewer rows legitimately lack these; a NEW
    # run dropping them would silently lose the cap-floor tie-break ordering
    # or the boxed-warning disclosure.
    ("pre_cap_score",           "warn"),
    ("black_box_advisory",      "warn"),
]


# ---------------------------------------------------------------------------
# Runtime validation
# ---------------------------------------------------------------------------

def validate_handoff(
    candidates: list[dict[str, Any]],
    stage: str,
    field_specs: list[tuple[str, str]],
) -> list[str]:
    """
    Validate a list of candidate dicts against a field spec.

    Returns a list of human-readable problem strings (empty = all OK).
    If STRICT_VALIDATION=true, also raises RuntimeError on the first 'error'
    severity problem.

    Args:
        candidates: the list of candidate dicts to check
        stage: human-readable label for the stage (e.g. "chemist→reviewer")
        field_specs: list of (field_name, severity) tuples
    """
    problems: list[str] = []
    for i, cand in enumerate(candidates):
        drug = cand.get("drug_name", f"candidate[{i}]")
        for field, severity in field_specs:
            if field not in cand:
                msg = (
                    f"[schemas] {severity.upper()} at {stage} handoff: "
                    f"'{drug}' is missing field '{field}' entirely. "
                    f"This field was dropped somewhere before this stage."
                )
                problems.append(msg)
                print(msg)
                if severity == "error" and STRICT_VALIDATION:
                    raise RuntimeError(msg)
    return problems


def validate_chemist_handoff(candidates: list[dict[str, Any]]) -> list[str]:
    """Validate candidates produced by the Chemist before the Reviewer sees them."""
    return validate_handoff(candidates, "chemist→reviewer", _CHEMIST_REQUIRED_FIELDS)


def validate_reviewer_handoff(candidates: list[dict[str, Any]]) -> list[str]:
    """Validate candidates produced by the Reviewer before Writer/Validator sees them."""
    return validate_handoff(candidates, "reviewer→writer", _REVIEWER_REQUIRED_FIELDS)
