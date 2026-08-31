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
    approval_basis: Required[str]
    approval_evidence_providers: list[str]
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
    # Fraction of fixed score weights observed for this candidate.  A missing
    # structural similarity measurement is omitted (not treated as zero), so
    # downstream reports must expose the resulting coverage.
    evidence_weight_coverage: Optional[float]
    strong_match: Required[bool]
    is_approved_drug: Optional[bool]
    approval_basis: Optional[str]
    approval_evidence_providers: list[str]
    target_tier: Optional[str]
    exploratory_rank_demoted: Optional[bool]
    causal_anchor: Optional[dict]
    unapproved_cap_applied: Required[bool]
    mechanism_cap_applied: Required[bool]
    mechanism_direction: Optional[dict]
    # 0.05 only after a persisted, citation-bearing compatible direction audit.
    qualified_directional_bonus: Optional[float]
    safety_cap_applied: Required[bool]
    black_box_advisory: Optional[bool]   # BBW present but drug NOT withdrawn
    # Structured-vs-independent withdrawal disagreement; never silently
    # suppresses a cap without a visible audit object.
    safety_reconciliation: Optional[dict]
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
    # Modality disclosure (ChEMBL molecule_type + oral route). Disclosure only.
    chembl_molecule_type: Optional[str]
    chembl_oral: Optional[bool]
    nonoral_biologic_flag: Optional[bool]
    source_types: list[str]
    source_health: dict
    target_memberships: list[dict]
    _evidence_ledger: dict
    mechanism_class: Optional[str]
    therapeutic_role: str
    process_support: list[dict]
    process_source_status: Optional[str]
    # Versioned, explicit-unknown evidence handoff used by flagship dossiers.
    dossier_evidence_contract: dict
    # Present for CACNA1C Timothy syndrome dossiers; structured TS1 cardiac
    # validation scope is explicitly future/unperformed.
    timothy_syndrome_cardiac_scope: Optional[dict]
    trial_audit: dict


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
    # Stamped by the approval gate on every candidate it inspects. Absence means
    # a pool bypassed the gate entirely, which is exactly how an unapproved
    # research compound once reached a repurposing dossier.
    ("approval_basis",          "error"),
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
    ("evidence_weight_coverage", "warn"),
    ("safety_reconciliation",   "warn"),
    # Historical rows predate these fields, so replay remains warn-only.
    # Every new reviewer row carries them; warnings expose future dropouts.
    ("approval_basis",          "warn"),
    ("approval_evidence_providers", "warn"),
    ("target_tier",             "warn"),
    ("exploratory_rank_demoted", "warn"),
    ("causal_anchor",           "warn"),
]


# Fields whose VALUE must be non-blank, not merely present.
#
# Background: the presence-only check above passes an empty string, so a
# candidate that reaches the dossier with target_discovery_method="" renders a
# blank "how this target was found" row while every validator reports OK.  A
# ledger-native candidate (one that never came from a legacy ChEMBL row) hits
# exactly this path, because the ledger owns the field and the passthrough
# overlay will not backfill an authoritative field.
_CHEMIST_VALUE_FIELDS: list[tuple[str, str]] = [
    ("drug_name",               "error"),
    ("target_symbol",           "error"),
    ("target_discovery_method", "error"),
    ("uniprot_id",              "warn"),   # legitimately unresolved sometimes
    ("_evidence_ledger",        "error"),  # empty dict = no lineage at all
    ("approval_basis",          "warn"),   # "unknown" is a value, "" is a bug
]

_REVIEWER_VALUE_FIELDS: list[tuple[str, str]] = [
    ("drug_name",               "error"),
    ("target_symbol",           "error"),
    ("disease_name",            "error"),
    ("target_discovery_method", "error"),
    ("approval_basis",          "warn"),
    ("target_tier",             "warn"),
    # uniprot_id and _evidence_ledger are deliberately NOT value-checked here:
    # rows persisted before those fields existed legitimately carry None/{} and
    # must stay replayable.  Both are value-checked one hop upstream at the
    # chemist boundary, which is where a dropout actually originates.
]


# ---------------------------------------------------------------------------
# Runtime validation
# ---------------------------------------------------------------------------

def validate_handoff(
    candidates: list[dict[str, Any]],
    stage: str,
    field_specs: list[tuple[str, str]],
    value_specs: Optional[list[tuple[str, str]]] = None,
) -> list[str]:
    """
    Validate a list of candidate dicts against a field spec.

    Returns a list of human-readable problem strings (empty = all OK).
    If STRICT_VALIDATION=true, also raises RuntimeError on the first 'error'
    severity problem.

    Args:
        candidates: the list of candidate dicts to check
        stage: human-readable label for the stage (e.g. "chemist→reviewer")
        field_specs: list of (field_name, severity) tuples — PRESENCE only
        value_specs: list of (field_name, severity) tuples whose VALUE must be
            non-blank.  Presence-only checking passes an empty string, so a
            field that is carried through blank (an empty
            ``target_discovery_method`` renders as an empty dossier row) is
            invisible to the presence check.
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
        for field, severity in (value_specs or []):
            if field not in cand:
                continue  # already reported by the presence pass
            if not _is_blank(cand.get(field)):
                continue
            msg = (
                f"[schemas] {severity.upper()} at {stage} handoff: "
                f"'{drug}' carries field '{field}' with a BLANK value "
                f"({cand.get(field)!r}). The field survived the handoff but "
                f"its value was never populated, so downstream reports will "
                f"render it empty."
            )
            problems.append(msg)
            print(msg)
            if severity == "error" and STRICT_VALIDATION:
                raise RuntimeError(msg)
    return problems


def _is_blank(value: Any) -> bool:
    """True when a value is present but carries no information.

    ``None`` counts as blank ONLY for fields listed in a value spec — those are
    fields where a null is a real dropout, not a legitimate "unmeasured".
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def validate_chemist_handoff(candidates: list[dict[str, Any]]) -> list[str]:
    """Validate candidates produced by the Chemist before the Reviewer sees them."""
    return validate_handoff(candidates, "chemist→reviewer",
                            _CHEMIST_REQUIRED_FIELDS, _CHEMIST_VALUE_FIELDS)


def validate_reviewer_handoff(candidates: list[dict[str, Any]]) -> list[str]:
    """Validate candidates produced by the Reviewer before Writer/Validator sees them."""
    return validate_handoff(candidates, "reviewer→writer",
                            _REVIEWER_REQUIRED_FIELDS, _REVIEWER_VALUE_FIELDS)
