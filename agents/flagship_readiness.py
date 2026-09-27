"""Computational flagship-hypothesis gate for AgentBio.

This module deliberately does not participate in candidate ranking. It answers
a narrower, computationally decidable product question: is there a sufficiently
specific, mechanism-linked, testable hypothesis to take forward as a flagship
research case?

This is not an efficacy, clinical-readiness, or partner-approval gate. Disease
models, human efficacy, exposure, safety, and comparative advantage remain
explicit evidence states for later validation; requiring them here would make
the gate circular.
"""

from __future__ import annotations

from typing import Any, Optional


FLAGSHIP_READINESS_VERSION = "flagship-readiness-v3"

# Conservative allow-list of targets whose direct inhibition is commonly a
# general cytotoxic/proliferation signal.  This is a disclosure/readiness
# heuristic, not a scientific claim that these targets are never disease drivers.
GENERIC_CYTOTOXIC_TARGETS = frozenset({
    "AURKA",
    "AURKB",
    "CDK1",
    "KIF11",
    "MKI67",
    "PCNA",
    "TOP2A",
    "TOP2B",
    "TUBB",
    "TUBB3",
})

_SCOPE_MARKERS = (
    "mutation", "mutant", "mutated", "variant", "type ", "subtype", "stage",
    "relapsed", "refractory", "resistant", "high-risk", "high risk",
    "metastatic", "localized", "cutaneous", "cardiac", "renal",
    "pediatric", "adult", "deficient", "deficiency",
)
_USE_CASE_FIELDS = ("subgroup", "stage", "treatment_setting", "proposed_advantage")


def _criterion(
    status: str,
    summary: str,
    *,
    evidence: Optional[list[dict[str, Any]]] = None,
    missing: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "evidence": evidence or [],
        "missing_evidence": missing or [],
    }


def _target_evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "target_symbol": row.get("target_symbol"),
        "uniprot_id": row.get("uniprot_id"),
        "discovery_method": row.get("target_discovery_method"),
        "association_score": row.get("ot_association_score"),
        "source": "stage_1_target_selection",
    }]


def _has_scope_marker(value: str) -> bool:
    normalized = f" {value.casefold().strip()} "
    return any(marker in normalized for marker in _SCOPE_MARKERS)


def _normalize_flagship_use_case(value: Any) -> dict[str, Optional[str]]:
    """Return bounded, display-safe expert framing without treating it as evidence."""
    raw = value if isinstance(value, dict) else {}
    differentiator = raw.get("proposed_advantage") or raw.get("differentiator")
    return {
        "subgroup": str(raw.get("subgroup") or "").strip() or None,
        "stage": str(raw.get("stage") or "").strip() or None,
        "treatment_setting": str(
            raw.get("treatment_setting") or raw.get("setting") or ""
        ).strip() or None,
        "proposed_advantage": str(differentiator or "").strip() or None,
    }


def _use_case_claims(
    use_case: dict[str, Optional[str]],
) -> dict[str, dict[str, Optional[str]]]:
    """Make both absent and supplied-but-unverified claims explicit UNKNOWN."""
    return {
        field: {
            "value": use_case.get(field),
            "status": "UNKNOWN",
            "source": "expert_input" if use_case.get(field) else "not_supplied",
            "verification": "not_established_by_preflight",
        }
        for field in _USE_CASE_FIELDS
    }


def _use_case_criteria(
    use_case: dict[str, Optional[str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    claims = _use_case_claims(use_case)
    scoped = any(use_case.get(field) for field in (
        "subgroup", "stage", "treatment_setting"
    ))
    scope = _criterion(
        "PASS" if scoped else "CONDITIONAL",
        (
            "A bounded subgroup, stage, or treatment setting is supplied as the "
            "computational hypothesis scope; it remains an expert framing claim, "
            "not independently established evidence."
            if scoped else
            "No bounded subgroup, stage, or treatment setting was supplied."
        ),
        evidence=[{
            "claims": {
                field: claims[field] for field in (
                    "subgroup", "stage", "treatment_setting"
                )
            },
            "source": "expert_input",
        }] if scoped else [],
        missing=[] if scoped else [
            "bounded subgroup, stage, or treatment-setting hypothesis"
        ],
    )
    advantage = _criterion(
        "PASS" if use_case.get("proposed_advantage") else "CONDITIONAL",
        (
            "A testable differentiator is supplied as a hypothesis; comparative "
            "evidence is outside this computational gate."
            if use_case.get("proposed_advantage") else
            "No testable differentiator over current care was supplied."
        ),
        evidence=[{
            "claim": claims["proposed_advantage"],
            "source": "expert_input",
        }] if use_case.get("proposed_advantage") else [],
        missing=[] if use_case.get("proposed_advantage") else [
            "testable differentiating hypothesis"
        ],
    )
    return scope, advantage


def _next_experiment(
    disease_name: str,
    target_symbol: str,
    drug_name: Optional[str] = None,
) -> dict[str, Any]:
    subject = drug_name or "the leading approved candidate"
    return {
        "question": (
            f"Does {subject} produce a reproducible {target_symbol}-relevant "
            f"effect in a disease-relevant {disease_name} model?"
        ),
        "success_criterion": (
            "A reproducible disease-model effect at clinically plausible exposure "
            "with a measurable advantage over the closest current-care comparator."
        ),
        "stop_criterion": (
            "No effect at achievable exposure, no target/pathway engagement, or "
            "no advantage over the comparator in the prespecified model."
        ),
        "status": "SPECIFIABLE",
    }


def evaluate_target_preflight(
    rows: list[dict[str, Any]],
    requested_disease: str = "",
    flagship_use_case: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate whether a disease is worth a full flagship case attempt.

    Stage 1 cannot know the eventual reviewed compound, so candidate-specific
    advantage and candidate-level efficacy remain explicit unknowns here.  A
    later candidate evaluator is required before a dossier can be called
    flagship-ready.
    """
    use_case = _normalize_flagship_use_case(flagship_use_case)
    if not rows:
        return {
            "schema_version": FLAGSHIP_READINESS_VERSION,
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason_codes": ["no_target_selection_evidence"],
            "reasons": ["No authoritative disease-target rows were available."],
            "missing_evidence": ["disease_target_context", "leading_target"],
            "criteria": {},
            "targets": [],
            "flagship_use_case": use_case,
            "flagship_use_case_claims": _use_case_claims(use_case),
            "next_action": "Do not start a full flagship case until target context recovers.",
        }

    top = list(rows[:5])
    lead = top[0]
    disease = (
        requested_disease.strip()
        or str(lead.get("disease_name") or "").strip()
        or "the selected disease"
    )
    target = str(lead.get("target_symbol") or "").upper()
    association = lead.get("ot_association_score")
    method = str(lead.get("target_discovery_method") or "").casefold()
    evidence = _target_evidence(lead)
    generic = target in GENERIC_CYTOTOXIC_TARGETS

    if generic:
        target_specificity = _criterion(
            "FAIL",
            f"{target} is a generic proliferation/cytotoxicity target for this "
            "flagship screen, so target pharmacology alone is not differentiated.",
            evidence=evidence,
            missing=["disease-selective dependency or biomarker-defined context"],
        )
    elif isinstance(association, (int, float)) and association >= 0.5:
        target_specificity = _criterion(
            "PASS",
            "The leading target has a measured disease association and is not in "
            "the conservative generic-cytotoxic target list.",
            evidence=evidence,
        )
    else:
        target_specificity = _criterion(
            "CONDITIONAL",
            "The target is not obviously generic, but disease-specific target "
            "strength is not high enough for an unconditional flagship pass.",
            evidence=evidence,
            missing=["stronger disease-specific target evidence"],
        )

    approved_names = list(lead.get("approved_drug_names") or [])
    has_approved = lead.get("has_approved_treatment")
    precedent = "precedent" in method or "pharmacological" in method
    if precedent or has_approved is True:
        differentiated = bool(use_case.get("proposed_advantage"))
        overlap = _criterion(
            "PASS" if differentiated else "CONDITIONAL",
            (
                "Existing treatment or pharmacological precedent is disclosed, "
                "and the supplied differentiator makes this a testable hypothesis "
                "rather than a silent rediscovery."
                if differentiated else
                "Approved treatment or pharmacological precedent is already "
                "present; a differentiated use case is still needed."
            ),
            evidence=[{
                "approved_treatment_observed": has_approved,
                "approved_drug_names": approved_names[:12],
                "target_discovery_method": lead.get("target_discovery_method"),
                "source": "stage_1_target_selection",
            }],
            missing=[] if differentiated else [
                "testable differentiating hypothesis"
            ],
        )
    elif has_approved is False:
        overlap = _criterion(
            "PASS",
            "No approved treatment was observed in the disease context used by "
            "Stage 1; candidate-level overlap still requires review.",
            evidence=[{
                "approved_treatment_observed": False,
                "source": "stage_1_target_selection",
            }],
            missing=["candidate comparator confirmation"],
        )
    else:
        overlap = _criterion(
            "UNKNOWN",
            "Treatment status was not authoritative enough to assess overlap.",
            missing=["authoritative treatment landscape"],
        )

    expert_scope, expert_advantage = _use_case_criteria(use_case)
    scope = (
        _criterion(
            "PASS",
            "The disease input carries a visible subtype, stage, mutation, or "
            "treatment-setting qualifier.",
        )
        if _has_scope_marker(disease)
        else expert_scope
    )

    process_support = lead.get("process_support") or []
    if (
        method in {"genetic_association", "literature_mechanism_class"}
        and isinstance(association, (int, float))
        and association >= 0.1
    ):
        evidence_maturity = _criterion(
            "PASS",
            "Direct disease-target evidence is sufficient to state a computational "
            "hypothesis. Disease-model, exposure, and clinical evidence are outside "
            "this gate and remain explicitly unknown in the dossier.",
            evidence=[{
                "target_discovery_method": lead.get("target_discovery_method"),
                "process_support_count": len(process_support),
                "validation_scope": "outside_computational_flagship_gate",
                "source": "stage_1_target_selection",
            }],
        )
    else:
        evidence_maturity = _criterion(
            "CONDITIONAL",
            "The preflight has some target context, but not enough direct "
            "disease-linked evidence for a computational flagship hypothesis.",
            missing=["direct disease-target evidence"],
        )

    advantage = (
        _criterion(
            "PASS",
            "A candidate-level differentiator is stated as a testable hypothesis; "
            "comparative efficacy, safety, and exposure remain future validation.",
            evidence=expert_advantage["evidence"],
        )
        if use_case.get("proposed_advantage")
        else _criterion(
            "CONDITIONAL",
            "No candidate-level differentiator is stated yet.",
            missing=["testable differentiating hypothesis"],
        )
    )

    experiment = _next_experiment(disease, target)
    next_experiment = _criterion(
        "PASS",
        "A falsifiable orthogonal experiment can be stated without claiming that "
        "the hypothesis is true.",
        evidence=[experiment],
    )

    criteria = {
        "target_specificity": target_specificity,
        "standard_of_care_overlap": overlap,
        "scope_clarity": scope,
        "disease_specific_evidence": evidence_maturity,
        "candidate_advantage": advantage,
        "scoped_use_case": expert_scope,
        "proposed_advantage": expert_advantage,
        "next_experiment": next_experiment,
    }
    failures = [
        name for name, value in criteria.items()
        if value["status"] == "FAIL"
    ]
    uncertainty = [
        name for name, value in criteria.items()
        if value["status"] in {"CONDITIONAL", "UNKNOWN"}
    ]
    if failures:
        verdict = "NOT_FLAGSHIP_READY"
    elif uncertainty:
        verdict = "CONDITIONAL_REVIEW"
    else:
        # A target-only preflight can only reach this branch for unusually
        # complete source rows. Candidate review still has the final say.
        verdict = "FLAGSHIP_READY"

    reasons = []
    missing: list[str] = []
    for name, value in criteria.items():
        if value["status"] != "PASS":
            reasons.append(f"{name}: {value['summary']}")
            missing.extend(value.get("missing_evidence") or [])
    deduped_missing = list(dict.fromkeys(missing))
    return {
        "schema_version": FLAGSHIP_READINESS_VERSION,
        "verdict": verdict,
        "reason_codes": failures + uncertainty,
        "reasons": reasons,
        "missing_evidence": deduped_missing,
        "criteria": criteria,
        "targets": [{
            "target_symbol": row.get("target_symbol"),
            "uniprot_id": row.get("uniprot_id"),
            "association_score": row.get("ot_association_score"),
            "tractability_score": row.get("tractability_score"),
            "unmet_need_score": row.get("unmet_need_score"),
            "discovery_method": row.get("target_discovery_method"),
        } for row in top],
        "lead_target": target,
        "disease_name": disease,
        "flagship_use_case": use_case,
        "flagship_use_case_claims": _use_case_claims(use_case),
        "next_experiment": experiment,
        "next_action": (
            "Choose another disease or define a direct, differentiated hypothesis "
            "before spending a full flagship attempt."
            if verdict == "NOT_FLAGSHIP_READY"
            else (
                "Continue as a conditional flagship hypothesis after resolving the "
                "listed computational gaps."
                if verdict == "CONDITIONAL_REVIEW"
                else (
                    "Proceed as a computational flagship hypothesis. This does not "
                    "establish efficacy, exposure, safety, or clinical benefit."
                )
            )
        ),
    }


def evaluate_candidate_readiness(
    candidate: dict[str, Any],
    contract: Optional[dict[str, Any]] = None,
    flagship_use_case: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Evaluate final differentiation using the persisted dossier contract."""
    contract = contract or candidate.get("dossier_evidence_contract") or {}
    raw_use_case = (
        flagship_use_case
        if flagship_use_case is not None
        else candidate.get("flagship_use_case")
        or contract.get("flagship_use_case")
    )
    use_case = _normalize_flagship_use_case(raw_use_case)
    context = contract.get("disease_mechanism_context") or {}
    scientific = contract.get("scientific_readiness") or {}
    comparators = contract.get("comparators") or {}
    target = str(
        candidate.get("target_symbol") or context.get("target_symbol") or ""
    ).upper()
    disease = str(
        candidate.get("disease_name") or context.get("disease_name") or "the disease"
    )
    drug = str(candidate.get("drug_name") or "the candidate")
    generic = target in GENERIC_CYTOTOXIC_TARGETS
    target_criterion = _criterion(
        "FAIL" if generic else (
            "PASS" if str(candidate.get("target_applicability") or "").upper()
            in {"DIRECT_CAUSAL", "DIRECT_DISEASE_ASSOCIATED"}
            else "CONDITIONAL"
        ),
        (
            f"{target} is a generic proliferation/cytotoxicity target; direct "
            "binding does not differentiate this flagship."
            if generic else
            "The candidate has direct disease-associated target applicability."
            if str(candidate.get("target_applicability") or "").upper()
            in {"DIRECT_CAUSAL", "DIRECT_DISEASE_ASSOCIATED"} else
            "Target applicability is not strong enough for a flagship pass."
        ),
        missing=[] if not generic and str(
            candidate.get("target_applicability") or "").upper()
        in {"DIRECT_CAUSAL", "DIRECT_DISEASE_ASSOCIATED"} else [
            "disease-specific target dependency"
        ],
    )

    comparator_rows = (
        list(comparators.get("target_approved_drugs") or [])
        + list(comparators.get("selected_candidates") or [])
    )
    advantage_value = (
        candidate.get("flagship_candidate_advantage")
        or candidate.get("differentiation_evidence")
    )
    proposed_differentiator = advantage_value or use_case.get("proposed_advantage")
    overlap_status = (
        "PASS" if proposed_differentiator else
        "CONDITIONAL"
    )
    overlap = _criterion(
        overlap_status,
        (
            "A candidate-specific differentiator is explicitly stated as a "
            "testable hypothesis; comparator selection and validation remain "
            "downstream work."
            if proposed_differentiator else
            "No candidate-specific differentiator is stated against current care."
        ),
        evidence=[{
            "comparator_count": len(comparator_rows),
            "comparators": [
                row.get("drug_name") for row in comparator_rows[:12]
                if row.get("drug_name")
            ],
            "source": "dossier_evidence_contract",
        }] if comparator_rows else [],
        missing=[] if proposed_differentiator else [
            "candidate-specific differentiating hypothesis"
        ],
    )

    expert_scope, expert_advantage = _use_case_criteria(use_case)
    scope = _criterion(
        "PASS" if _has_scope_marker(disease) else "CONDITIONAL",
        (
            "The final disease context is explicitly scoped."
            if _has_scope_marker(disease) else
            expert_scope["summary"]
            if any(use_case.get(field) for field in (
                "subgroup", "stage", "treatment_setting"
            )) else
            "The final case remains broad and does not identify a subgroup, stage, "
            "or treatment setting."
        ),
        evidence=expert_scope["evidence"] if not _has_scope_marker(disease)
        else [],
        missing=[] if _has_scope_marker(disease) else (
            expert_scope["missing_evidence"] or
            ["defined subgroup, stage, or treatment setting"]
        ),
    )

    model = str(scientific.get("disease_model_evidence") or "UNKNOWN").upper()
    clinical = str(
        scientific.get("clinical_efficacy_evidence") or "UNKNOWN"
    ).upper()
    if model == "MIXED_CONFLICTING" or clinical == "MIXED_CONFLICTING":
        evidence_status = "CONDITIONAL"
        evidence_summary = (
            "The dossier records mixed or conflicting disease-specific evidence. "
            "This does not block a computational hypothesis; both findings are "
            "recorded."
        )
        missing = ["resolve conflicting disease-specific evidence"]
    elif model == "OBSERVED_SUPPORT" or clinical == "OBSERVED_SUPPORT":
        evidence_status = "PASS"
        evidence_summary = (
            "Disease-specific model or clinical support is persisted. This is "
            "additional context, not a requirement of the computational gate."
        )
        missing = []
    else:
        evidence_status = "PASS"
        evidence_summary = (
            "No disease-model or clinical efficacy evidence is required for this "
            "computational hypothesis gate; those states remain UNKNOWN for "
            "external validation."
        )
        missing = []
    evidence_maturity = _criterion(
        evidence_status, evidence_summary, missing=missing
    )

    if proposed_differentiator:
        advantage = _criterion(
            "PASS",
            "A candidate-specific differentiator was supplied as a testable "
            "hypothesis; comparative evidence is outside this gate.",
            evidence=[{
                "value": proposed_differentiator,
                "source": "candidate_review"
                if advantage_value else "expert_input",
            }],
        )
    else:
        advantage = _criterion(
            "CONDITIONAL",
            "No candidate-specific differentiator was supplied.",
            missing=["candidate-specific differentiating hypothesis"],
        )

    experiment = _next_experiment(disease, target, drug)
    experiment_criterion = (
        _criterion(
            "PASS",
            "A falsifiable candidate-versus-comparator experiment is defined.",
            evidence=[experiment],
        )
        if target and disease != "the disease" and drug != "the candidate"
        else _criterion(
            "UNKNOWN",
            "A concrete next experiment cannot be written without a resolved "
            "disease, target, and candidate.",
            missing=["resolved disease, target, and candidate identity"],
        )
    )
    criteria = {
        "target_specificity": target_criterion,
        "standard_of_care_overlap": overlap,
        "scope_clarity": scope,
        "disease_specific_evidence": evidence_maturity,
        "candidate_advantage": advantage,
        "next_experiment": experiment_criterion,
    }
    if any(use_case.values()):
        criteria["scoped_use_case"] = expert_scope
        criteria["proposed_advantage"] = expert_advantage
    failures = [
        name for name, value in criteria.items()
        if value["status"] == "FAIL"
    ]
    uncertainty = [
        name for name, value in criteria.items()
        if value["status"] in {"CONDITIONAL", "UNKNOWN"}
    ]
    verdict = "NOT_FLAGSHIP_READY" if failures else (
        "CONDITIONAL_REVIEW" if uncertainty else "FLAGSHIP_READY"
    )
    reasons = []
    missing_evidence: list[str] = []
    for name, value in criteria.items():
        if value["status"] != "PASS":
            reasons.append(f"{name}: {value['summary']}")
            missing_evidence.extend(value.get("missing_evidence") or [])
    return {
        "schema_version": FLAGSHIP_READINESS_VERSION,
        "verdict": verdict,
        "reason_codes": failures + uncertainty,
        "reasons": reasons,
        "missing_evidence": list(dict.fromkeys(missing_evidence)),
        "criteria": criteria,
        "disease_name": disease,
        "drug_name": drug,
        "lead_target": target,
        "flagship_use_case": use_case,
        "flagship_use_case_claims": _use_case_claims(use_case),
        "next_experiment": experiment,
        "next_action": (
            "A direct target and a differentiating use case are not supplied."
            if verdict == "NOT_FLAGSHIP_READY"
            else (
                "The computational framing below is incomplete; the missing "
                "fields are listed."
                if verdict == "CONDITIONAL_REVIEW"
                else (
                    "Eligible as a computational flagship hypothesis. Efficacy, "
                    "exposure and safety are not measured by this screen."
                )
            )
        ),
    }