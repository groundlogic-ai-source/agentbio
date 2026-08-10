"""Shared N1–N4 research-audit disclosures.

The detectors are pure and deterministic.  They consume structured regulatory
and literature envelopes and never change ranks, scores, caps, or verdicts.
"""
from __future__ import annotations

from typing import Any

from data_sources.openfda import get_label_evidence
from data_sources.pubtator_assertions import search_drug_mechanism_assertions

_BIOLOGIC_MODALITIES = {"biologic", "vaccine"}
_LOCAL_ROUTES = {
    "topical", "ophthalmic", "otic", "intra-articular", "intralesional",
    "intradermal", "vaginal", "rectal", "nasal",
}
_SYSTEMIC_CONTEXT = {
    "systemic", "plasma", "serum", "blood", "circulating", "whole body",
    "central nervous system", "brain",
}


def _finding(code: str, status: str, title: str, rationale: str,
             evidence: list[dict[str, Any]], *,
             action: str = "Human review required") -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "title": title,
        "rationale": rationale,
        "action": action,
        "evidence": evidence,
        "effect": "disclosure_only",
    }


def detect_audit_findings(
    regulatory: dict[str, Any],
    literature: dict[str, Any],
    *,
    claimed_route: str = "",
    claimed_dose: str = "",
    claimed_modality: str = "",
    claimed_context: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    products = [
        product for product in regulatory.get("products") or []
        if isinstance(product, dict) and product.get("citation_eligible")
    ]
    reg_status = str(regulatory.get("status") or "unavailable")

    if reg_status in {"unavailable", "parse_failed", "degraded"}:
        findings.append(_finding(
            "N1", "unresolved",
            "Combination-product status could not be resolved",
            f"Regulatory source state: {reg_status}. This is not evidence that "
            "the product has one active ingredient.",
            [{"provider": "openfda", "status": reg_status}],
        ))
        findings.append(_finding(
            "N2", "unresolved",
            "Product modality could not be resolved",
            f"Regulatory source state: {reg_status}. Missing label evidence is "
            "not treated as small-molecule evidence.",
            [{"provider": "openfda", "status": reg_status}],
        ))
        findings.append(_finding(
            "N4", "unresolved",
            "Approved route and dosage form could not be resolved",
            f"Regulatory source state: {reg_status}. Route feasibility cannot "
            "be inferred from missing data.",
            [{"provider": "openfda", "status": reg_status}],
        ))
    elif not products:
        for code, title in (
            ("N1", "Combination-product status lacks cutoff-eligible label evidence"),
            ("N2", "Product modality lacks cutoff-eligible label evidence"),
            ("N4", "Route and dosage form lack cutoff-eligible label evidence"),
        ):
            findings.append(_finding(
                code, "unresolved", title,
                "No dated label artifact strictly before 2026-08-10 was "
                "available. This is an unresolved evidence state, not a negative.",
                [{"provider": "openfda", "status": reg_status}],
            ))
    else:
        combination_products = [
            product for product in products
            if (product.get("regulatory") or {}).get("combination") is True
        ]
        if combination_products:
            evidence = [{
                "spl": product.get("spl"),
                "active_ingredients": (
                    product.get("regulatory") or {}).get("active_ingredients", []),
            } for product in combination_products]
            findings.append(_finding(
                "N1", "flagged",
                "Label identifies a multi-ingredient product",
                "Any claim framed as a single active moiety must be split or "
                "explicitly justified against the labeled combination.",
                evidence,
            ))

        biologics = [
            product for product in products
            if (product.get("regulatory") or {}).get("product_modality")
            in _BIOLOGIC_MODALITIES
        ]
        if biologics and claimed_modality.lower() in {
                "small molecule", "small_molecule", "drug"}:
            findings.append(_finding(
                "N2", "flagged",
                "Claimed modality conflicts with the regulatory product type",
                "The label identifies a biologic or vaccine while the supplied "
                "claim context identifies a small-molecule drug.",
                [{"spl": p.get("spl"), "modality": (
                    p.get("regulatory") or {}).get("product_modality")}
                 for p in biologics],
            ))
        elif biologics:
            findings.append(_finding(
                "N2", "review",
                "Biologic modality requires explicit scope review",
                "Biologic products must not inherit small-molecule assumptions "
                "about structure, oral delivery, or target engagement.",
                [{"spl": p.get("spl"), "modality": (
                    p.get("regulatory") or {}).get("product_modality")}
                 for p in biologics],
            ))

        claim_route = claimed_route.strip().lower()
        if claim_route:
            approved_routes = {
                route.strip().lower()
                for product in products
                for route in (product.get("regulatory") or {}).get("routes", [])
            }
            if approved_routes and claim_route not in approved_routes:
                findings.append(_finding(
                    "N4", "flagged",
                    "Claimed route is not among the dated approved label routes",
                    "This route mismatch requires pharmacology and formulation "
                    "review; it does not by itself prove exposure is impossible.",
                    [{"claimed_route": claimed_route,
                      "approved_routes": sorted(approved_routes)}],
                ))

        context = claimed_context.lower()
        approved_routes = {
            route.strip().lower()
            for product in products
            for route in (product.get("regulatory") or {}).get("routes", [])
        }
        if (context and any(token in context for token in _SYSTEMIC_CONTEXT)
                and approved_routes
                and all(route in _LOCAL_ROUTES for route in approved_routes)):
            findings.append(_finding(
                "N4", "review",
                "Systemic claim context is not established by locally labeled routes",
                "Locally labeled administration does not alone establish or "
                "exclude systemic exposure. Dose, formulation, PK, and tissue "
                "distribution require human review.",
                [{"approved_routes": sorted(approved_routes),
                  "claimed_context": claimed_context}],
            ))

        dose = claimed_dose.strip()
        if dose:
            dosage_quotes = [
                evidence
                for product in products
                for evidence in product.get("evidence") or []
                if evidence.get("field") == "dosage_and_administration"
            ]
            if dosage_quotes:
                findings.append(_finding(
                    "N4", "review",
                    "Claimed dose requires comparison with dated label dosing",
                    "The supplied dose is preserved for explicit human review "
                    "against the quoted label. A deterministic text comparison "
                    "does not establish equivalent exposure across route, "
                    "formulation, population, or indication.",
                    [{
                        "claimed_dose": dose,
                        "label_dosage_evidence": dosage_quotes,
                    }],
                ))
            else:
                findings.append(_finding(
                    "N4", "unresolved",
                    "Claimed dose lacks structured label dosing evidence",
                    "A dose was supplied, but no cutoff-eligible "
                    "dosage-and-administration quote was available.",
                    [{"claimed_dose": dose}],
                ))

    lit_status = str(literature.get("status") or "unavailable")
    assertions = [
        assertion for assertion in literature.get("assertions") or []
        if isinstance(assertion, dict) and assertion.get("citation_eligible")
    ]
    if lit_status in {"unavailable", "parse_failed", "degraded"}:
        findings.append(_finding(
            "N3", "unresolved",
            "Entity-linked species/context evidence could not be resolved",
            f"Literature source state: {lit_status}. Failure is not treated as "
            "human evidence or as absence of evidence.",
            [{"provider": "pubtator3", "status": lit_status}],
        ))
    elif assertions:
        settings = {
            str(a.get("experimental_setting") or "unknown")
            for a in assertions
        }
        if "clinical_or_human_in_vivo" not in settings:
            assertion_evidence = [{
                "pmid": a.get("pmid"), "pmcid": a.get("pmcid"),
                "species": a.get("species"),
                "experimental_setting": a.get("experimental_setting"),
                "experimental_context": a.get("experimental_context"),
                "direction": a.get("direction"),
                "evidence_sentence": a.get("evidence_sentence"),
                "lineage_id": a.get("lineage_id"),
            } for a in assertions]
            if products:
                # Approved-label guard (audit-context-v2): cutoff-eligible
                # marketed label evidence establishes human use of the drug
                # itself, so its evidence base must not be asserted to be
                # preclinical-only.  The mechanism-specific human gap is
                # still disclosed, but as an unresolved state rather than a
                # defect flag.  (Fixes the control false-flag failure mode
                # found by audit claim-set v1.)
                findings.append(_finding(
                    "N3", "unresolved",
                    "Marketed drug: mechanism-specific human evidence "
                    "unconfirmed",
                    "Cutoff-eligible marketed label evidence exists for this "
                    "drug, so its evidence base is not asserted to be "
                    "preclinical-only.  No admitted entity-linked assertion "
                    "in this bounded search establishes a human experimental "
                    "or clinical context for this mechanism.",
                    assertion_evidence,
                ))
            else:
                findings.append(_finding(
                    "N3", "flagged",
                    "Admitted mechanism evidence is preclinical-only or species-unresolved",
                    "No admitted entity-linked assertion in this bounded "
                    "search establishes a human experimental or clinical "
                    "context.",
                    assertion_evidence,
                ))
    else:
        findings.append(_finding(
            "N3", "unresolved",
            "No admitted entity-linked human mechanism assertion was found",
            f"Literature source state: {lit_status}. A healthy empty or "
            "filtered-empty result is not proof that human evidence does not "
            "exist; it means none passed this bounded deterministic search.",
            [{
                "provider": "pubtator3",
                "status": lit_status,
                "retrieved_count": literature.get("retrieved_count"),
                "filtered_count": literature.get("filtered_count"),
            }],
        ))

    return sorted(findings, key=lambda finding: finding["code"])


def build_audit_context(
    drug_name: str,
    *,
    mechanism_symbol: str = "",
    claimed_route: str = "",
    claimed_dose: str = "",
    claimed_modality: str = "",
    claimed_context: str = "",
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    regulatory = get_label_evidence(
        drug_name, deadline_monotonic=deadline_monotonic)
    literature = search_drug_mechanism_assertions(
        drug_name, mechanism_symbol,
        deadline_monotonic=deadline_monotonic) if mechanism_symbol else {
            "provider": "pubtator3",
            "status": "empty",
            "drug": {"name": drug_name},
            "mechanism": {"name": ""},
            "assertions": [],
            "retrieved_count": 0,
            "filtered_count": 0,
            "release": "not queried: no resolved mechanism entity",
            "citation_cutoff": "2026-08-10",
            "error": None,
        }
    findings = detect_audit_findings(
        regulatory, literature,
        claimed_route=claimed_route,
        claimed_dose=claimed_dose,
        claimed_modality=claimed_modality,
        claimed_context=claimed_context,
    )
    return {
        "contract_version": "audit-context-v2",
        "purpose": "research_evidence_audit",
        "effect": "disclosure_only",
        "citation_cutoff": "2026-08-10",
        "sources": {
            "regulatory_label": regulatory,
            "entity_linked_literature": literature,
        },
        "findings": findings,
        "limitations": [
            "No finding changes an AgentBio score, rank, cap, or verdict.",
            "N4 route/context findings require formulation, dose, PK, and tissue-distribution review.",
            "A healthy empty source result is distinct from filtered, malformed, degraded, or unavailable evidence.",
            "N3 preclinical-only is not asserted for drugs with cutoff-eligible marketed label evidence; the mechanism-specific human evidence gap is disclosed as unresolved instead.",
        ],
    }