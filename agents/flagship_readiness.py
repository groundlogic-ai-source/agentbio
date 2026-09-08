"""Separate flagship-readiness policy for AgentBio.

This module deliberately does not participate in candidate ranking.  It answers
a narrower product question: is there enough differentiation and evidence
context to spend a full case attempt on an outreach flagship?

The preflight evaluator consumes Stage 1 rows and is intentionally conservative:
unknown evidence stays unknown, and a strong target score cannot turn a generic
or standard-of-care-concordant hypothesis into a flagship.
"""

from __future__ import annotations

from typing import Any, Optional


FLAGSHIP_READINESS_VERSION = "flagship-readiness-v2"

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
        "UNKNOWN",
        (
            "An expert supplied a subgroup, stage, or treatment setting, but "
            "preflight cannot independently support that claim."
            if scoped else
            "No scoped subgroup, stage, or treatment setting was supplied."
        ),
        evidence=[{
            "claims": {
                field: claims[field] for field in (
                    "subgroup", "stage", "treatment_setting"
                )
            },
            "source": "expert_input",
        }] if scoped else [],
        missing=["independent support for the scoped use case"],
    )
    advantage = _criterion(
        "UNKNOWN",
        (
            "An expert proposed a differentiator, but no comparative evidence "
            "supports it yet."
            if use_case.get("proposed_advantage") else
            "No proposed advantage over current care was supplied."
        ),
        evidence=[{
            "claim": claims["proposed_advantage"],
            "source": "expert_input",
        }] if use_case.get("proposed_advantage") else [],
        missing=["comparative evidence for the proposed advantage"],
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
        overlap = _criterion(
            "CONDITIONAL",
            "Approved treatment or pharmacological precedent is already present; "
            "a flagship must demonstrate an advantage rather than repeat the "
            "existing mechanism.",
            evidence=[{
                "approved_treatment_observed": has_approved,
                "approved_drug_names": approved_names[:12],
                "target_discovery_method": lead.get("target_discovery_method"),
                "source": "stage_1_target_selection",
            }],
            missing=["candidate-specific advantage over current care"],
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
    scope = _criterion(
        "PASS" if _has_scope_marker(disease) else "CONDITIONAL",
        (
            "The disease input carries a visible subtype, stage, mutation, or "
            "treatment-setting qualifier."
            if _has_scope_marker(disease)
            else expert_scope["summary"]
            if any(use_case.get(field) for field in (
                "subgroup", "stage", "treatment_setting"
            ))
            else "The disease input is broad; a flagship claim needs an explicit "
                 "subgroup, stage, or treatment-setting scope."
        ),
        evidence=expert_scope["evidence"] if not _has_scope_marker(disease)
        else [],
        missing=[] if _has_scope_marker(disease) else (
            expert_scope["missing_evidence"] or
            ["defined subgroup, stage, or treatment setting"]
        ),
    )

    process_support = lead.get("process_support") or []
    if process_support or method in {"literature_mechanism_class", "genetic_association"}:
        evidence_maturity = _criterion(
            "CONDITIONAL",
            "Disease-linked mechanistic or association evidence is present, but "
            "disease-model and clinical candidate evidence are not established "
            "by Stage 1.",
            evidence=[{
                "target_discovery_method": lead.get("target_discovery_method"),
                "process_support_count": len(process_support),
                "source": "stage_1_target_selection",
            }],
            missing=["disease-model evidence", "candidate-specific clinical evidence"],
        )
    else:
        evidence_maturity = _criterion(
            "UNKNOWN",
            "The preflight could not establish disease-specific evidence maturity.",
            missing=["disease-specific model or clinical evidence"],
        )

    advantage = _criterion(
        "UNKNOWN",
        "The candidate has not been reviewed yet, so advantage over current care "
        "cannot be inferred from Stage 1.",
        missing=["candidate-specific efficacy or safety advantage"],
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
            "Choose another disease or define a differentiated use case before "
            "spending a full flagship attempt."
            if verdict == "NOT_FLAGSHIP_READY"
            else (
                "Continue only as a scoped research hypothesis; final flagship "
                "readiness requires candidate-level review."
                if verdict == "CONDITIONAL_REVIEW"
                else "Proceed to full candidate-level review; final readiness is pending."
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
    overlap_status = (
        "PASS" if comparator_rows and advantage_value else
        "CONDITIONAL" if comparator_rows else
        "UNKNOWN"
    )
    overlap = _criterion(
        overlap_status,
        (
            "A comparator is present and the candidate-specific advantage is "
            "explicitly stated."
            if comparator_rows and advantage_value else
            "Approved or same-target comparator drugs are present; the candidate "
            "must show a defined advantage over current care."
            if comparator_rows else
            "No reliable comparator context was persisted."
        ),
        evidence=[{
            "comparator_count": len(comparator_rows),
            "comparators": [
                row.get("drug_name") for row in comparator_rows[:12]
                if row.get("drug_name")
            ],
            "source": "dossier_evidence_contract",
        }] if comparator_rows else [],
        missing=["candidate-specific advantage over current care"],
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
    if model == "OBSERVED_SUPPORT" or clinical == "OBSERVED_SUPPORT":
        evidence_status = "PASS"
        evidence_summary = "Disease-specific model or clinical support is persisted."
        missing = []
    elif model == "MIXED_CONFLICTING" or clinical == "MIXED_CONFLICTING":
        evidence_status = "CONDITIONAL"
        evidence_summary = "Disease-specific evidence is mixed or conflicting."
        missing = ["resolve conflicting disease-specific evidence"]
    else:
        evidence_status = "CONDITIONAL"
        evidence_summary = (
            "The dossier has target pharmacology but no observed disease-model or "
            "clinical efficacy evidence."
        )
        missing = ["disease-model or candidate-specific clinical evidence"]
    evidence_maturity = _criterion(
        evidence_status, evidence_summary, missing=missing
    )

    if advantage_value:
        advantage = _criterion(
            "PASS",
            "A candidate-specific advantage over current care was explicitly supplied.",
            evidence=[{"value": advantage_value, "source": "candidate_review"}],
        )
    else:
        advantage = _criterion(
            "UNKNOWN",
            (
                "An expert proposed a differentiator, but no comparative evidence "
                "supports it yet."
                if use_case.get("proposed_advantage") else
                "No candidate-specific advantage over current care was established."
            ),
            evidence=expert_advantage["evidence"],
            missing=(
                expert_advantage["missing_evidence"]
                if use_case.get("proposed_advantage")
                else ["comparative efficacy, safety, exposure, or access advantage"]
            ),
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
            "Do not present this candidate as a flagship; use it only as a "
            "qualified research hypothesis unless a differentiated use case is "
            "added."
            if verdict == "NOT_FLAGSHIP_READY"
            else (
                "Keep this as conditional expert review until the missing evidence "
                "is resolved."
                if verdict == "CONDITIONAL_REVIEW"
                else "Eligible for flagship human review; this is not an efficacy verdict."
            )
        ),
    }