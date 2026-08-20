"""Hash-gated, no-LLM acceptance for the version-bridged audit upgrade."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.audit import build_audit_scope_diagnostics  # noqa: E402
from data_sources.chembl import (  # noqa: E402
    get_drug_mechanism_identities_for_audit,
    get_mechanism_only_approved_drugs,
)

MANIFEST = ROOT / "validation" / "version_bridged_audit_manifest.json"
PREREG = ROOT / "validation" / "version_bridged_audit_preregistration.md"
MACHINE_V2 = ROOT / "validation" / "machine_v2_acceptance.json"
AUTOPSY = ROOT / "validation" / "studyc_miss_autopsy.json"
TARGET_CACHE = ROOT / "validation" / ".machine_v2_acceptance_cache.json"
OUT = ROOT / "validation" / "version_bridged_audit_acceptance.json"
REPORT = ROOT / "validation" / "version_bridged_audit_report.md"
K_PRODUCTION = 5
KILL_SWITCH = "AGENTBIO_DISABLE_MECHANISM_COMPLETENESS_REPAIR"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _implementation_identity() -> dict[str, Any]:
    files = (
        "api/audit.py",
        "api/triage.py",
        "data_sources/chembl.py",
        "artifacts/web-frontend/src/components/AuditTab.jsx",
        "artifacts/web-frontend/src/components/TriagePanel.jsx",
        "validation/run_version_bridged_audit_acceptance.py",
    )
    hashes = {relative: _sha256(ROOT / relative) for relative in files}
    fingerprint = hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"fingerprint": fingerprint, "source_sha256": hashes}


def _verify_manifest() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text())
    if _sha256(PREREG) != manifest["preregistration_sha256"]:
        raise RuntimeError("preregistration hash mismatch")
    for relative, expected in manifest["historical_artifacts"].items():
        actual = _sha256(ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen historical artifact changed: {relative}")
    if _sha256(TARGET_CACHE) != manifest["target_universe_cache_sha256"]:
        raise RuntimeError("frozen target-universe cache changed")
    for relative in (
        "api/audit_context.py",
        "agents/target_selection.py",
        "data_sources/holdout.py",
    ):
        expected = manifest["prechange_source_artifacts"][relative]
        if _sha256(ROOT / relative) != expected:
            raise RuntimeError(
                f"out-of-scope source changed after registration: {relative}")
    return manifest


def _candidate_rows(target_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "drug_name": f"frozen-target-{row['rank']}",
            "target_symbol": row.get("target_symbol"),
            "uniprot_id": row.get("uniprot_id"),
            "target_chembl_id": row.get("target_chembl_id"),
        }
        for row in target_rows
    ]


def _legacy_pool(uniprot_id: str) -> list[dict[str, Any]]:
    previous = os.environ.get(KILL_SWITCH)
    os.environ[KILL_SWITCH] = "1"
    try:
        return get_mechanism_only_approved_drugs(uniprot_id)
    finally:
        if previous is None:
            os.environ.pop(KILL_SWITCH, None)
        else:
            os.environ[KILL_SWITCH] = previous


def _molecule_match(
    chembl_id: str | None,
    candidates: list[dict[str, Any]],
) -> bool:
    if not chembl_id:
        return False
    case_ids = {chembl_id}
    for candidate in candidates:
        if candidate.get("molecule_chembl_id") == chembl_id:
            return True
        parent = candidate.get("parent_chembl_id")
        if parent and parent in case_ids:
            return True
    return False


def _control_result(
    name: str,
    passed: bool,
    detail: str,
) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _write_report(payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    controls = payload["controls"]
    lines = [
        "# Version-Bridged Audit Acceptance",
        "",
        "**Study:** `version_bridged_audit_acceptance_v1`  ",
        f"**Base commit at run:** `{payload['base_commit_at_run']}`  ",
        f"**Implementation fingerprint:** "
        f"`{payload['implementation']['fingerprint']}`  ",
        f"**Result:** **{'PASS' if payload['passed'] else 'FAIL'}**",
        "",
        "## Endpoint results (kept separate)",
        "",
        f"- Historical discovery recovery: **{summary['rediscovery_recovery']}/"
        f"{summary['misses_evaluated']}** (unchanged frozen contract)",
        f"- Stable-identity universe overlap: "
        f"**{summary['stable_identity_universe_overlap']}/"
        f"{summary['misses_evaluated']}**",
        f"- Production top-5 overlap: **{summary['production_gate_overlap']}/"
        f"{summary['misses_evaluated']}**",
        f"- Mechanism-pool recovery after approval-first repair: "
        f"**{summary['mechanism_pool_recovery']}/"
        f"{summary['misses_evaluated']}**",
        f"- Paired mechanism-pool gains versus legacy cap: "
        f"**{summary['paired_mechanism_pool_gain']}**",
        f"- Supplied-drug audit coverage: **{summary['audit_coverage']}/"
        f"{summary['misses_evaluated']}**",
        "",
        "Audit coverage is not discovery recall. Stable-identity overlap is not "
        "a rediscovery hit. The immutable machine-v2 result remains 0/16 under "
        "its original exact-string contract.",
        "",
        "## Controls",
        "",
    ]
    for control in controls:
        lines.append(
            f"- {'PASS' if control['passed'] else 'FAIL'} — "
            f"{control['name']}: {control['detail']}")
    lines.extend([
        "",
        "## Run bounds and provenance",
        "",
        f"- Retrieval date: {payload['run_metadata']['retrieval_date_utc']}",
        f"- Runtime: {payload['run_metadata']['runtime_seconds']} seconds",
        "- LLM calls: 0",
        "- Mechanism source: ChEMBL live REST API; the endpoint does not expose "
        "a release identifier, so the retrieval date and frozen output are recorded.",
        "- Enabled mechanism-pool repair: at most 200 source rows inspected per "
        "resolved target, at most 100 approved outputs returned; overflow and "
        "incomplete metadata fail closed.",
        "- Disease-side target selection and holdout code remained byte-identical "
        "to the preregistered versions.",
        "",
        "## Promotion decision",
        "",
        payload["promotion_decision"],
        "",
        "## Per-case bridge",
        "",
        "| Disease | Drug | Stable identity | Top-5 | Legacy pool | Repaired pool | Audit scope |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    for row in payload["rows"]:
        lines.append(
            f"| {row['disease_name']} | {row['drug_name']} | "
            f"{'yes' if row['stable_identity_universe_overlap'] else 'no'} | "
            f"{'yes' if row['production_gate_overlap'] else 'no'} | "
            f"{'yes' if row['legacy_mechanism_pool_recovery'] else 'no'} | "
            f"{'yes' if row['mechanism_pool_recovery'] else 'no'} | "
            f"{row['audit_scope_status']} |"
        )
    REPORT.write_text("\n".join(lines) + "\n")


def main() -> None:
    started = time.monotonic()
    manifest = _verify_manifest()
    machine = json.loads(MACHINE_V2.read_text())
    autopsy = json.loads(AUTOPSY.read_text())
    target_cache = json.loads(TARGET_CACHE.read_text())
    autopsy_by_key = {
        (row["disease_name"].casefold(), row["drug_name"].casefold()): row
        for row in autopsy["rows"]
    }

    rows: list[dict[str, Any]] = []
    source_failures: list[str] = []
    for historical in machine["rows"]:
        disease = historical["disease_name"]
        drug = historical["drug_name"]
        facts = autopsy_by_key[(disease.casefold(), drug.casefold())]
        evidence = get_drug_mechanism_identities_for_audit(
            drug, facts.get("chembl_id"))
        if evidence["status"] == "unavailable":
            source_failures.append(f"{disease} / {drug}")

        target_rows = list(target_cache[disease])
        diagnostics = build_audit_scope_diagnostics(
            "absent",
            candidates=_candidate_rows(target_rows),
            mechanism_evidence=evidence,
            molecule_type=facts.get("molecule_type"),
            identity_route=evidence.get("identity_route") or "",
        )
        gate_diagnostics = build_audit_scope_diagnostics(
            "absent",
            candidates=_candidate_rows(target_rows[:K_PRODUCTION]),
            mechanism_evidence=evidence,
            molecule_type=facts.get("molecule_type"),
            identity_route=evidence.get("identity_route") or "",
        )
        universe_hit = next(iter(diagnostics["pool_target_overlap"]), None)
        gate_hit = next(iter(gate_diagnostics["pool_target_overlap"]), None)
        repaired_pool: list[dict[str, Any]] = []
        legacy_pool: list[dict[str, Any]] = []
        if gate_hit and gate_hit.get("uniprot_id"):
            legacy_pool = _legacy_pool(gate_hit["uniprot_id"])
            repaired_pool = get_mechanism_only_approved_drugs(
                gate_hit["uniprot_id"])

        rows.append({
            "disease_name": disease,
            "drug_name": drug,
            "chembl_id": facts.get("chembl_id"),
            "historical_miss_class": historical["miss_class"],
            "historical_exact_string_universe_rescued":
                historical["universe_rescued"],
            "rediscovery_recovery": False,
            "mechanism_evidence_status": evidence["status"],
            "stable_mechanism_identities": evidence.get("targets") or [],
            "stable_identity_status": diagnostics.get("stable_identity_status"),
            "stable_identity_universe_overlap": universe_hit is not None,
            "universe_rank": universe_hit.get("rank") if universe_hit else None,
            "universe_target_symbol":
                universe_hit.get("target_symbol") if universe_hit else None,
            "production_gate_overlap": gate_hit is not None,
            "production_gate_rank": gate_hit.get("rank") if gate_hit else None,
            "legacy_mechanism_pool_recovery": _molecule_match(
                facts.get("chembl_id"), legacy_pool),
            "mechanism_pool_recovery": _molecule_match(
                facts.get("chembl_id"), repaired_pool),
            "audit_scope_status": diagnostics["audit_scope_status"],
            "deterministic_miss_reason":
                diagnostics["deterministic_miss_reason"],
            "audit_diagnostic_has_rank_or_score": any(
                diagnostics.get(field) is not None
                for field in (
                    "supplied_drug_discovery_rank",
                    "supplied_drug_discovery_score",
                )
            ),
            "audit_diagnostic_inserted_candidate":
                diagnostics.get("supplied_drug_candidate_inserted"),
            "component_target_overlap": diagnostics.get(
                "pool_component_target_overlap") or [],
        })

    controls: list[dict[str, Any]] = []
    for drug, disease in (
        ("Prednisone", "Lupus Erythematosus, Systemic"),
        ("Lenalidomide", "Multiple Myeloma"),
    ):
        diag = build_audit_scope_diagnostics(
            "found", identity_route="pool_name_match")
        controls.append(_control_result(
            f"found-by-discovery: {drug} / {disease}",
            diag["audit_scope_status"] == "found_by_discovery"
            and diag["deterministic_miss_reason"] == "FOUND",
            "found state remains distinct from supplied-only audit",
        ))

    for drug, disease in (
        ("Infliximab", "Giant Cell Arteritis"),
        ("Mycophenolate mofetil", "Interstitial Cystitis"),
    ):
        facts = autopsy_by_key[(disease.casefold(), drug.casefold())]
        evidence = get_drug_mechanism_identities_for_audit(
            drug, facts.get("chembl_id"))
        target_rows = list(target_cache[disease])
        diag = build_audit_scope_diagnostics(
            "absent",
            candidates=_candidate_rows(target_rows),
            mechanism_evidence=evidence,
            molecule_type=facts.get("molecule_type"),
        )
        passed = (
            diag["audit_scope_status"] == "auditable_only_because_supplied"
            and not diag["pool_target_overlap"]
            and diag["supplied_drug_discovery_rank"] is None
            and diag["supplied_drug_discovery_score"] is None
            and not diag["supplied_drug_candidate_inserted"]
        )
        controls.append(_control_result(
            f"unrelated supplied-only: {drug} / {disease}",
            passed,
            "remains absent, unranked, unscored, and outside target coverage",
        ))

    unresolved = build_audit_scope_diagnostics(
        "unresolved", identity_route="unresolved_name")
    controls.append(_control_result(
        "unresolved synthetic drug name",
        unresolved["audit_scope_status"] == "not_assessable"
        and unresolved["deterministic_miss_reason"] == "NAME_RESOLUTION_GAP",
        "unresolved identity is not treated as biological absence",
    ))
    degraded = build_audit_scope_diagnostics(
        "absent",
        candidates=[],
        mechanism_evidence={
            "status": "unavailable",
            "provider": "chembl",
            "targets": [],
        },
    )
    controls.append(_control_result(
        "degraded mechanism source",
        degraded["audit_scope_status"] == "source_failure"
        and degraded["deterministic_miss_reason"] is None,
        "transport failure does not become NO_MECHANISM_DATA",
    ))

    n = len(rows)
    summary = {
        "misses_evaluated": n,
        "rediscovery_recovery": 0,
        "stable_identity_universe_overlap": sum(
            row["stable_identity_universe_overlap"] for row in rows),
        "production_gate_overlap": sum(
            row["production_gate_overlap"] for row in rows),
        "legacy_mechanism_pool_recovery": sum(
            row["legacy_mechanism_pool_recovery"] for row in rows),
        "mechanism_pool_recovery": sum(
            row["mechanism_pool_recovery"] for row in rows),
        "paired_mechanism_pool_gain": sum(
            row["mechanism_pool_recovery"]
            and not row["legacy_mechanism_pool_recovery"]
            for row in rows
        ),
        "audit_coverage": sum(
            row["audit_scope_status"] == "auditable_only_because_supplied"
            for row in rows
        ),
        "source_failures": len(source_failures),
    }
    controls_pass = all(control["passed"] for control in controls)
    immutability_pass = (
        all(not row["rediscovery_recovery"] for row in rows)
        and all(not row["audit_diagnostic_has_rank_or_score"] for row in rows)
        and all(not row["audit_diagnostic_inserted_candidate"] for row in rows)
    )
    pool_gate_pass = summary["paired_mechanism_pool_gain"] >= 1
    passed = (
        not source_failures
        and controls_pass
        and immutability_pass
        and pool_gate_pass
    )
    runtime_seconds = round(time.monotonic() - started, 3)
    payload = {
        "contract": "version-bridged-audit-acceptance-v1",
        "preregistration": str(PREREG.relative_to(ROOT)),
        "manifest": str(MANIFEST.relative_to(ROOT)),
        "base_commit_at_run": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "implementation": _implementation_identity(),
        "run_metadata": {
            "retrieval_date_utc": datetime.now(timezone.utc).date().isoformat(),
            "runtime_seconds": runtime_seconds,
            "llm_calls": 0,
            "sources": {
                "mechanism_identity": {
                    "provider": "ChEMBL",
                    "api_base": "https://www.ebi.ac.uk/chembl/api/data",
                    "release": "not_exposed_by_endpoint",
                },
                "target_universe": {
                    "artifact": "validation/.machine_v2_acceptance_cache.json",
                    "sha256": manifest["target_universe_cache_sha256"],
                },
            },
            "bounds": {
                "acceptance_cases": n,
                "production_target_gate": K_PRODUCTION,
                "mechanism_source_rows_per_target": 200,
                "approved_mechanism_pool_output_per_target": 100,
            },
            "feature_changes": [
                "candidate-conditioned stable-identity audit with explicit supplied-only scope",
                "approval-first bounded mechanism-only pool completeness repair",
                "single and batch audit presentation of scope, source state, miss reason, and target ladder",
            ],
        },
        "historical_contract": machine["contract"],
        "historical_rediscovery_result": (
            manifest["historical_rediscovery_result"]),
        "passed": passed,
        "summary": summary,
        "source_failures": source_failures,
        "controls": controls,
        "rows": rows,
        "promotion_decision": (
            "Republish with the version-bridged supplied-drug audit and the "
            "bounded approval-first mechanism completeness repair enabled; "
            "do not describe this as a replacement Study C result or as a "
            "change to the historical 0/16 rediscovery result. The repair's "
            f"kill switch is `{KILL_SWITCH}=1`."
            if passed else
            "Do not enable the discovery completeness repair. Retain only "
            "components whose individual promotion gates passed."
        ),
        "interpretation_guard": (
            "Audit coverage is not discovery recall. Stable-identity universe "
            "overlap is not a rediscovery hit. Frozen historical artifacts "
            "remain unmodified."
        ),
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _write_report(payload)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"controls_pass={controls_pass} immutability_pass={immutability_pass}")
    print(f"result={'PASS' if passed else 'FAIL'}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()