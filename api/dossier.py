"""
Dossier audit workspace: claim-level verification for saved hypothesis
reports.

A saved report is a frozen narrative snapshot. This module recomputes the
*current* verification state of every claim in it from the registry at read
time (the same collect_facts() the report endpoint re-gates with), derives a
single audit status per dossier, and exposes the claim ledger — framings,
effect sizes, confirmation results, confound checks, and provenance — in a
form an organizational reviewer can work through line by line.

Status precedence (worst wins):
  label_artifact_suspect  — association lives in the administrative-exclude class
  confound_fail           — a computable confound adjustment killed the effect
  not_confirmed           — discovery passed but holdout confirmation did not
  verified_with_gaps      — passed both, but ≥1 named confound was not testable
  verified                — passed discovery+confirmation, all computable
                            confounds survived
  not_tested              — never reached a tested state
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

import api.saved_reports_db as saved_reports_db

_STATUS_LABEL_ARTIFACT = "label_artifact_suspect"
_STATUS_CONFOUND_FAIL = "confound_fail"
_STATUS_NOT_CONFIRMED = "not_confirmed"
_STATUS_VERIFIED_GAPS = "verified_with_gaps"
_STATUS_VERIFIED = "verified"
_STATUS_NOT_TESTED = "not_tested"


def _research_modules():
    """Lazy import matching api/main.py's _ensure_research_modules pattern."""
    import hypothesis_registry as R  # noqa: N812
    import hypothesis_report as HR  # noqa: N812
    return R, HR


def parse_reviewer_tag(note: str, has_result: bool) -> str:
    """Extract the lead reviewer's per-hypothesis tag from outcome_note.

    Single source of truth for the tag parsing also used by
    GET /api/research/hypotheses — keep the two in sync by calling this.
    """
    if note.startswith("SKIPPED (duplicate):"):
        return "SKIPPED_DUPLICATE"
    if note.startswith("hard-blocked:"):
        return "HARD_BLOCKED"
    if note.startswith("auto-demoted"):
        return "NEEDS_ENRICHMENT"
    if note.startswith("not tested:"):
        return "NOT_TESTED"
    if note.startswith("DISCARDED:") or note.startswith("DISCARDED "):
        return "DISCARDED"
    if note.startswith("NEEDS_ENRICHMENT:") or note.startswith("NEEDS_ENRICHMENT "):
        return "NEEDS_ENRICHMENT"
    if note.startswith("REFUTED (direction):"):
        return "REFUTED"
    if note.startswith("LABEL_ARTIFACT_SUSPECT:"):
        return "LABEL_ARTIFACT_SUSPECT"
    if has_result:
        return "READY"
    return ""


def _reviewer_tags_for(R, hypothesis_id: str) -> list[str]:  # noqa: N803
    """All reviewer tags across a hypothesis's framing rows."""
    hist = R.load_history_full()
    rows = hist[hist["hypothesis_id"] == hypothesis_id]
    tags: list[str] = []
    for _, r in rows.iterrows():
        note = str(r.get("outcome_note") or "")
        dt = str(r.get("discovery_test_type") or "").strip()
        dp = r.get("discovery_raw_p")
        has_result = bool(dt and dp is not None and str(dp) != "")
        tag = parse_reviewer_tag(note, has_result)
        if tag:
            tags.append(tag)
    return tags


def _confound_entries(confound_check: Optional[dict]) -> list[dict[str, Any]]:
    """Normalize the confound_check payload into audit rows, defensively.

    The registry stores one parsed summary dict per hypothesis; key names have
    varied slightly across writer versions, so absent keys mean "unknown",
    never silently "passed".
    """
    if not isinstance(confound_check, dict):
        return []
    raw = confound_check.get("confounds")
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        adj = c.get("adjustment_result")
        computable = c.get("computable")
        if computable is None:
            computable = adj is not None
        survives = c.get("survives_adjustment")
        if survives is None and isinstance(adj, dict):
            # Explicit key checks — an `or` chain would silently drop a real
            # False ("effect did NOT survive adjustment") and mislabel the
            # dossier as verified.
            if "survives" in adj:
                survives = adj["survives"]
            elif "survives_adjustment" in adj:
                survives = adj["survives_adjustment"]
        entries.append({
            "name": c.get("name") or c.get("confound") or "(unnamed confound)",
            "rationale": c.get("rationale"),
            "computable": bool(computable),
            "survives_adjustment": survives if survives is None else bool(survives),
            "adjustment_result": adj,
        })
    return entries


def audit_status_for(
    facts: dict,
    reviewer_tags: list[str],
) -> tuple[str, list[str]]:
    """Derive the dossier audit status + human-readable reasons."""
    reasons: list[str] = []

    if "LABEL_ARTIFACT_SUSPECT" in reviewer_tags:
        reasons.append(
            "At least one framing was flagged LABEL_ARTIFACT_SUSPECT: the "
            "association reproduces in the administrative-exclude class, so it "
            "is a labeling artifact, not a biological signal."
        )
        return _STATUS_LABEL_ARTIFACT, reasons

    confounds = _confound_entries(facts.get("confound_check"))
    failed = [c["name"] for c in confounds
              if c["computable"] and c["survives_adjustment"] is False]
    if failed:
        reasons.append(
            "Effect did not survive adjustment for computable confound(s): "
            + ", ".join(failed) + "."
        )
        return _STATUS_CONFOUND_FAIL, reasons

    if not facts.get("passed_both"):
        framings = facts.get("framings") or []
        unconfirmed = [
            f.get("framing") for f in framings
            if f.get("discovery_pass") and not f.get("confirmation_pass")
        ]
        if unconfirmed:
            reasons.append(
                "Discovery significant but holdout confirmation failed or is "
                "absent for framing(s): " + ", ".join(str(u) for u in unconfirmed)
                + ". The effect is NOT confirmed."
            )
            return _STATUS_NOT_CONFIRMED, reasons
        reasons.append("Hypothesis never passed discovery FDR at the locked threshold.")
        return _STATUS_NOT_TESTED, reasons

    not_testable = [c["name"] for c in confounds if not c["computable"]]
    if not_testable:
        reasons.append(
            "Passed discovery and holdout confirmation; all computable confounds "
            "survived. However, these named confounds were not testable from the "
            "dataset and remain open: " + ", ".join(not_testable) + "."
        )
        return _STATUS_VERIFIED_GAPS, reasons

    reasons.append(
        "Passed discovery FDR and holdout confirmation; every computable "
        "confound adjustment survived."
    )
    return _STATUS_VERIFIED, reasons


def _facts_fingerprint(facts: dict) -> str:
    """SHA-256 of canonical facts — matches the report-cache re-gating scheme."""
    canonical = json.dumps(facts, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verification_notes(facts: dict, status: str) -> list[str]:
    notes = [
        "Discovery FDR q-values were recomputed cumulatively at read time; "
        "stored per-run q-values are never trusted.",
        "Confirmation numbers come from holdout data never used during discovery.",
    ]
    if status == _STATUS_LABEL_ARTIFACT_SUSPECT:
        notes.append(
            "This dossier's effect is reported as an artifact finding — treat "
            "the claim as refuted-by-screen, not as a result."
        )
    if status == _STATUS_VERIFIED_GAPS:
        notes.append(
            "Untestable confounds are disclosed, not adjusted away — no adjusted "
            "numbers exist for them and none were fabricated."
        )
    return notes


def dossier_claims(hypothesis_id: str) -> Optional[dict[str, Any]]:
    """Claim ledger + current audit status for one dossier.

    Returns None if the hypothesis_id is unknown to the registry. Never raises
    409 semantics: the workspace must show failed/artifact dossiers too — that
    is what an audit tool is for.
    """
    R, HR = _research_modules()  # noqa: N806
    facts = HR.collect_facts(hypothesis_id)
    if facts is None:
        return None
    tags = _reviewer_tags_for(R, hypothesis_id)
    status, reasons = audit_status_for(facts, tags)

    saved = None
    for row in saved_reports_db.list_reports():
        if row.get("hypothesis_id") == hypothesis_id:
            saved = {
                "id": row.get("id"),
                "saved_at": row.get("saved_at").isoformat() if row.get("saved_at") else None,
                "generated_at": row.get("generated_at").isoformat() if row.get("generated_at") else None,
                "report_markdown": row.get("report_markdown"),
            }
            break

    return {
        "status": "ok",
        "hypothesis_id": hypothesis_id,
        "hypothesis_text": facts.get("hypothesis_text"),
        "audit_status": status,
        "status_reasons": reasons,
        "reviewer_tags": tags,
        "framings": facts.get("framings") or [],
        "confound_check": facts.get("confound_check"),
        "confounds": _confound_entries(facts.get("confound_check")),
        "provenance": facts.get("provenance"),
        "novelty_tag": facts.get("novelty_tag"),
        "passed_both": bool(facts.get("passed_both")),
        "fingerprint": _facts_fingerprint(facts),
        "verification_notes": _verification_notes(facts, status),
        "saved_report": saved,
    }


def list_dossiers() -> list[dict[str, Any]]:
    """All saved reports with their current read-time audit status."""
    R, HR = _research_modules()  # noqa: N806
    out: list[dict[str, Any]] = []
    for row in saved_reports_db.list_reports():
        hid = row.get("hypothesis_id")
        entry: dict[str, Any] = {
            "id": row.get("id"),
            "hypothesis_id": hid,
            "hypothesis_text": row.get("hypothesis_text"),
            "saved_at": row.get("saved_at").isoformat() if row.get("saved_at") else None,
            "generated_at": row.get("generated_at").isoformat() if row.get("generated_at") else None,
            "audit_status": _STATUS_NOT_TESTED,
            "status_reasons": [],
        }
        try:
            facts = HR.collect_facts(hid) if hid else None
        except Exception:  # noqa: BLE001 — one bad dossier must not sink the list
            facts = None
        if facts is not None:
            tags = _reviewer_tags_for(R, hid)
            status, reasons = audit_status_for(facts, tags)
            entry["audit_status"] = status
            entry["status_reasons"] = reasons
            entry["passed_both"] = bool(facts.get("passed_both"))
        else:
            entry["status_reasons"] = [
                "Hypothesis is no longer present in the registry; the saved "
                "snapshot is view-only and cannot be re-verified."
            ]
            entry["audit_status"] = "unverifiable"
        out.append(entry)
    return out
