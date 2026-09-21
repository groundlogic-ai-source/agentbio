"""
Claim-verification status derivation — extracted verbatim from the removed
api/dossier.py (the beta research/hypothesis-generation module's claim-ledger
UI) on 2026-09-21, because validation/run_audit_traps.py — a core, frozen
benchmark harness — depends on these two pure functions to verify its own
label-artifact and confirmation-discipline traps (T4, T5). They have no
dependency on the removed hypothesis registry: both operate purely on
parameters passed in by the caller. Preserved byte-identical to keep the
frozen audit_trap_results.json reproducible.

Status precedence (worst wins):
  label_artifact_suspect  — association lives in the administrative-exclude class
  confound_fail           — a computable confound adjustment killed the effect
  not_confirmed           — discovery passed but holdout confirmation did not
  verified_with_gaps      — passed both, but >=1 named confound was not testable
  verified                — passed discovery+confirmation, all computable
                            confounds survived
  not_tested              — never reached a tested state
"""
from __future__ import annotations

from typing import Any, Optional

_STATUS_LABEL_ARTIFACT = "label_artifact_suspect"
_STATUS_CONFOUND_FAIL = "confound_fail"
_STATUS_NOT_CONFIRMED = "not_confirmed"
_STATUS_VERIFIED_GAPS = "verified_with_gaps"
_STATUS_VERIFIED = "verified"
_STATUS_NOT_TESTED = "not_tested"


def parse_reviewer_tag(note: str, has_result: bool) -> str:
    """Extract the lead reviewer's per-hypothesis tag from outcome_note."""
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


def _confound_entries(confound_check: Optional[dict]) -> list[dict[str, Any]]:
    """Normalize a confound_check payload into audit rows, defensively.

    Key names have varied slightly across writer versions, so absent keys
    mean "unknown", never silently "passed".
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
    """Derive the claim-verification status + human-readable reasons."""
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
