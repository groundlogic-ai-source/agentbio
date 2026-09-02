"""Offline dossier render and contradiction audit from a real graph checkpoint.

This command deliberately blocks outbound sockets. It reads only persisted
LangGraph state, invokes the deterministic Writer/PDF renderer, and emits a
machine-readable audit beside the rendered artifacts.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from agents.writer import build_report_markdown
from agents.reviewer import _build_dossier_evidence_contract
from api.report_pdf import render_case_pdf
from data_sources.multisource_candidates import approval_basis


@contextmanager
def no_network():
    original_connect = socket.socket.connect

    def blocked_connect(self, address):  # noqa: ANN001
        raise RuntimeError(f"offline dry-render attempted network access: {address}")

    socket.socket.connect = blocked_connect
    try:
        yield
    finally:
        socket.socket.connect = original_connect


def _latest_state(db_path: Path, thread_id: str) -> dict[str, Any]:
    connection = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(connection)
    item = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    if item is None:
        raise ValueError(f"checkpoint thread not found: {thread_id}")
    return item.checkpoint["channel_values"]


def _matched_bio(state: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(candidate.get("target_symbol") or "").upper()
    accession = str(candidate.get("uniprot_id") or "").upper()
    for bio in state.get("biologist_outputs") or []:
        target = bio.get("target") or {}
        if accession and str(target.get("uniprot_id") or "").upper() == accession:
            return bio
        if symbol and str(target.get("target_symbol") or "").upper() == symbol:
            return bio
    return state.get("biologist_output")


def audit_report(
    candidate: dict[str, Any],
    bio: dict[str, Any] | None,
    markdown: str,
    *,
    original_approval: bool | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    checks: dict[str, bool] = {}
    target = str(candidate.get("target_symbol") or "").upper()
    bio_target = str(((bio or {}).get("target") or {}).get("target_symbol") or "").upper()
    checks["target_context_agrees"] = not bio_target or target == bio_target
    if not checks["target_context_agrees"]:
        failures.append(f"candidate target {target} != biologist target {bio_target}")

    method = str(candidate.get("target_discovery_method") or "").lower()
    components = candidate.get("score_components") or {}
    stamped = "pharmacological_precedent" in method
    checks["selection_value_not_presented_as_measurement"] = not (
        stamped and "measured Open Targets target–disease association" in markdown
    )
    if not checks["selection_value_not_presented_as_measurement"]:
        failures.append("precedent-stamped OT value rendered as a measurement")

    universal_absence = re.search(
        r"No approved drug currently exists|no approved drug (?:acts|modulates|targets)",
        markdown,
        re.IGNORECASE,
    )
    checks["source_absences_are_scoped"] = universal_absence is None
    if universal_absence:
        failures.append(f"universal absence claim: {universal_absence.group(0)}")

    rationale = str(candidate.get("rationale") or "").strip()
    checks["free_form_rationale_not_rendered"] = (
        not rationale or rationale not in markdown
    ) and "_Chemist rationale:_" not in markdown
    if not checks["free_form_rationale_not_rendered"]:
        failures.append("free-form Chemist rationale reached the public report")

    approval_basis = str(candidate.get("approval_basis") or "")
    if candidate.get("is_approved_drug") is True:
        checks["approval_identity_agrees"] = approval_basis not in {"", "unknown"}
    else:
        checks["approval_identity_agrees"] = "Approved / known drug | yes" not in markdown
    if not checks["approval_identity_agrees"]:
        failures.append("approval status lacks matching positive approval evidence")
    checks["approval_projection_preserves_snapshot"] = (
        original_approval is None
        or original_approval is candidate.get("is_approved_drug")
    )
    if not checks["approval_projection_preserves_snapshot"]:
        failures.append(
            "persisted approval flag conflicts with evidence-derived approval; "
            "the historical score cannot be reused under the current contract"
        )
    checks["unapproved_status_matches_score_cap"] = not (
        candidate.get("is_approved_drug") is False
        and not candidate.get("unapproved_cap_applied")
    )
    if not checks["unapproved_status_matches_score_cap"]:
        failures.append("unapproved eligibility state is inconsistent with the persisted score cap")

    contract = candidate.get("dossier_evidence_contract") or {}
    approved = ((contract.get("comparators") or {}).get("target_approved_drugs") or [])
    lead_name = str(candidate.get("drug_name") or "").casefold()
    lead_ids = {
        str(candidate.get(key) or "").casefold()
        for key in ("molecule_chembl_id", "parent_chembl_id")
        if candidate.get(key)
    }
    comparator_collision = False
    for row in approved:
        row_ids = {
            str(row.get(key) or "").casefold()
            for key in ("molecule_chembl_id", "parent_chembl_id")
            if row.get(key)
        }
        if str(row.get("name") or "").casefold() == lead_name or lead_ids.intersection(row_ids):
            comparator_collision = True
            break
    checks["lead_not_its_own_comparator"] = not comparator_collision
    if comparator_collision:
        failures.append("lead candidate survives in its approved comparator set")

    network = [str(x) for x in (bio or {}).get("interacting_genes", [])[:8]]
    checks["network_context_has_one_authoritative_source"] = (
        not network
        or all(markdown.count(gene) == 1 for gene in network)
    )
    if not checks["network_context_has_one_authoritative_source"]:
        failures.append("network genes are restated outside the canonical summary")

    banned = [
        "approved for this disease concept",
        "requires wet-lab",
        "ultimately, clinical validation",
        "If you want, I can next",
    ]
    checks["known_contradictory_or_directive_copy_absent"] = not any(
        phrase.lower() in markdown.lower() for phrase in banned
    )
    if not checks["known_contradictory_or_directive_copy_absent"]:
        failures.append("known contradictory/directive report copy remains")

    checks["ot_basis_present_when_required"] = (
        not stamped or components.get("ot_association_basis") == "precedent_stamped_constant"
    )
    if not checks["ot_basis_present_when_required"]:
        failures.append(
            "legacy precedent snapshot lacks Reviewer OT-basis provenance; "
            "it cannot satisfy the current report contract"
        )

    return {
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "candidate": candidate.get("drug_name"),
        "disease": candidate.get("disease_name"),
        "target": candidate.get("target_symbol"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="checkpoints.db")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    state = _latest_state(Path(args.db), args.thread_id)
    selected = state.get("selected") or []
    if not selected:
        raise ValueError("checkpoint has no persisted selected candidate")
    candidate = copy.deepcopy(selected[0])
    bio = _matched_bio(state, candidate)
    structures = state.get("structure_results") or {}
    struct = structures.get(candidate.get("drug_name"), {})
    reviewed = state.get("reviewed") or {}
    formula = reviewed.get("formula") or {}

    # Older persisted checkpoints predate the explicit approval and dossier
    # contract fields. Reconstruct only deterministic projections from evidence
    # already inside the checkpoint; never fetch or infer new evidence.
    original_approval = candidate.get("is_approved_drug")
    approved, basis, providers = approval_basis(candidate)
    candidate["is_approved_drug"] = approved
    candidate["approval_basis"] = basis
    candidate["approval_evidence_providers"] = providers
    components = candidate.setdefault("score_components", {})
    method = str(candidate.get("target_discovery_method") or "").lower()
    if "pharmacological_precedent" in method:
        components["ot_association_basis"] = "precedent_stamped_constant"
    elif components.get("normalized_ot_association") is not None:
        components["ot_association_basis"] = "measured_open_targets"
    candidate["dossier_evidence_contract"] = _build_dossier_evidence_contract(
        candidate,
        bio,
        reviewed.get("candidates") or selected,
    )

    targets = state.get("targets") or []
    target_meta = next((
        row for row in targets
        if str(row.get("target_symbol") or "").upper()
        == str(candidate.get("target_symbol") or "").upper()
    ), state.get("target"))

    with no_network():
        markdown = build_report_markdown(
            candidate,
            struct,
            formula,
            bio,
            target_meta,
            repurposing_only=bool(state.get("repurposing_only")),
            k_target_summary=reviewed.get("k_target_summary"),
        )
        audit = audit_report(
            candidate,
            bio,
            markdown,
            original_approval=original_approval,
        )
        report_sha = hashlib.sha256(markdown.encode()).hexdigest()
        job = {
            "job_id": state.get("job_id") or args.thread_id,
            "disease_name": candidate.get("disease_name"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "report_snapshot_at": datetime.now(timezone.utc).isoformat(),
            "report_sha256": report_sha,
        }
        pdf = render_case_pdf(markdown, job)

    (out / "report.md").write_text(markdown, encoding="utf-8")
    (out / "report.pdf").write_bytes(pdf)
    (out / "audit.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    (out / "snapshot.json").write_text(json.dumps({
        "thread_id": args.thread_id,
        "job_id": state.get("job_id"),
        "candidate": candidate,
        "biologist_output": bio,
        "structure_result": struct,
        "formula": formula,
        "target_meta": target_meta,
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())