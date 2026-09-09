"""Canonical policy/version fingerprints for actionable API dossiers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

DECISION_CONTRACT_VERSION = "actionable-decision-v1"
REPORT_CONTRACT_VERSION = "flagship-dossier-evidence-v2"
REVIEWER_FORMULA_VERSION = "reviewer-composite-v3-production"
SAFETY_SCHEMA_VERSION = "safety-v3"
LITERATURE_SCHEMA_VERSION = "literature-v4"
TARGET_APPLICABILITY_POLICY_VERSION = "target-applicability-v1"
CANDIDATE_IDENTITY_POLICY_VERSION = "candidate-identity-v3"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def reviewer_input_fingerprint(chemist_output: dict[str, Any]) -> str:
    """Bind a reviewer cache entry to input, failures, sources, and policy."""
    source_status = chemist_output.get("source_status") or {}
    return sha256_json({
        "chemist_input": chemist_output,
        "target_source_failure_envelopes": {
            "source_status": source_status,
            "k_target_summary": chemist_output.get("k_target_summary"),
        },
        "enabled_source_configuration": (
            chemist_output.get("enabled_sources")
            or _source_names(source_status)
        ),
        "policy": {
            "reviewer_formula_version": REVIEWER_FORMULA_VERSION,
            "safety_schema_version": SAFETY_SCHEMA_VERSION,
            "literature_schema_version": LITERATURE_SCHEMA_VERSION,
            "report_contract_version": REPORT_CONTRACT_VERSION,
            "target_applicability_policy_version":
                TARGET_APPLICABILITY_POLICY_VERSION,
            "candidate_identity_policy_version":
                CANDIDATE_IDENTITY_POLICY_VERSION,
        },
    })


def _source_names(value: Any) -> list[str]:
    supported = {"chembl", "gtopdb", "drugcentral", "bindingdb"}
    found: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key, child in node.items():
            if str(key).casefold() in supported:
                found.add(str(key).casefold())
            visit(child)

    visit(value)
    return sorted(found)