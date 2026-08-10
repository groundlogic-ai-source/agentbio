#!/usr/bin/env python3
"""Refresh stale pool snapshots' safety fields to the current classifier.

Why this exists: pool snapshots persisted before the black-box/withdrawal
split (pre ``safety-v2``) carry wrong "WITHDRAWN FROM MARKET" badges and hard
safety caps on marketed boxed-warning drugs (audit claim-set v1, class E2).
This script recomputes Layer-1 (ChEMBL structured) and Layer-2 (web) checks
and re-applies the SAME reconciliation the live reviewer uses
(``agents.reviewer._reconcile_safety``), then restores wrongly-capped
composite scores from the stored ``score_components``.

Fail-explicit design (no silent fallback):
  * PREFLIGHT: the composite reconstruction must reproduce every uncapped
    candidate's stored composite within rounding tolerance BEFORE any file
    is written.  A mismatch aborts the whole run.
  * Per-candidate source errors are recorded in the report and leave that
    candidate's stored safety fields untouched.

Usage:
    python3 -m scripts.refresh_pool_safety [--force]
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.reviewer import (
    COMPOSITE_WEIGHTS,
    LIPINSKI_PENALTY,
    SAFETY_CAP,
    SAFETY_SCHEMA_VERSION,
    STRONG_MATCH_THRESHOLD,
    _reconcile_safety,
)
from data_sources.chembl import get_molecule_safety_flags
from data_sources.safety_check import web_safety_check

CANDIDATES_DIR = "output/candidates"
BACKUP_DIR = os.path.join(CANDIDATES_DIR, "_backup_pre_safety_refresh")
REPORT_PATH = "output/pool_safety_refresh_report.json"
TOL = 0.002  # score_components are rounded to 4 decimal places


def _reconstruct_pre_cap(c: dict) -> float:
    """Rebuild the pre-cap composite from stored score_components.

    Handles both the current schema (efficacy_evidence + *_available flags)
    and the legacy schema (normalized_pchembl + confidence_term, where
    efficacy_evidence = 0.6*pChEMBL + 0.4*confidence_term per the reviewer
    comment on COMPOSITE_WEIGHTS).
    """
    sc = c.get("score_components") or {}
    if sc.get("efficacy_evidence") is not None:
        eff = float(sc["efficacy_evidence"])
    else:
        eff = (0.6 * float(sc.get("normalized_pchembl") or 0.0)
               + 0.4 * float(sc.get("confidence_term") or 0.0))
    num = COMPOSITE_WEIGHTS["efficacy_evidence"] * eff
    cov = COMPOSITE_WEIGHTS["efficacy_evidence"]

    ot_observed = (sc.get("ot_association_available")
                   if "ot_association_available" in sc
                   else sc.get("normalized_ot_association") is not None)
    if ot_observed and sc.get("normalized_ot_association") is not None:
        num += COMPOSITE_WEIGHTS["ot_association"] * float(sc["normalized_ot_association"])
        cov += COMPOSITE_WEIGHTS["ot_association"]

    nft_observed = (sc.get("trial_evidence_observed")
                    if "trial_evidence_observed" in sc
                    else sc.get("no_failed_trial") is not None)
    if nft_observed and sc.get("no_failed_trial") is not None:
        num += COMPOSITE_WEIGHTS["no_failed_trial"] * (1 if sc["no_failed_trial"] else 0)
        cov += COMPOSITE_WEIGHTS["no_failed_trial"]

    tan_observed = (sc.get("similarity_available")
                    if "similarity_available" in sc
                    else sc.get("normalized_tanimoto") is not None)
    if tan_observed and sc.get("normalized_tanimoto") is not None:
        num += COMPOSITE_WEIGHTS["tanimoto"] * float(sc["normalized_tanimoto"])
        cov += COMPOSITE_WEIGHTS["tanimoto"]

    composite = min(1.0, num / cov + float(sc.get("qualified_directional_bonus") or 0.0))
    if c.get("lipinski_penalty_applied"):
        composite -= LIPINSKI_PENALTY
    return round(composite, 4)


def _other_cap_applies(c: dict) -> bool:
    return bool(c.get("unapproved_cap_applied") or c.get("mechanism_cap_applied"))


def _needs_layer2(c: dict, layer1: dict) -> bool:
    stored_l2 = c.get("safety_layer2") or {}
    return bool(
        c.get("safety_cap_applied")
        or "WITHDRAWN" in str(c.get("status_badge") or "")
        or c.get("strong_match")
        or layer1.get("api_error")
        or layer1.get("black_box_advisory")
        or layer1.get("confirmed")
        or stored_l2.get("confirmed")
    )


def _preflight(candidates: list[dict], label: str) -> None:
    """Verify reconstruction against every uncapped candidate; abort on drift."""
    checked = 0
    worst = 0.0
    for c in candidates:
        if (c.get("safety_cap_applied") or c.get("unapproved_cap_applied")
                or c.get("mechanism_cap_applied")):
            continue
        recon = _reconstruct_pre_cap(c)
        stored = round(float(c.get("composite_score") or 0.0), 4)
        worst = max(worst, abs(recon - stored))
        if abs(recon - stored) > TOL:
            raise SystemExit(
                f"PREFLIGHT FAILED in {label}: {c.get('drug_name')} stored "
                f"composite {stored} != reconstructed {recon}. The snapshot "
                "predates the documented scoring formula; refusing to rewrite "
                "scores. Investigate before rerunning."
            )
        checked += 1
    print(f"[preflight] {label}: {checked} uncapped composites reproduced "
          f"(max |diff| {worst:.5f}, tol {TOL})")


def refresh_file(path: str, force: bool) -> dict:
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    name = os.path.basename(path)
    if payload.get("safety_schema_version") == SAFETY_SCHEMA_VERSION and not force:
        return {"file": name, "skipped": "already current"}

    candidates = payload.get("candidates", [])
    _preflight(candidates, name)

    changes = []
    errors = []
    for c in candidates:
        drug = c.get("drug_name")
        old = {
            "status_badge": c.get("status_badge"),
            "safety_cap_applied": bool(c.get("safety_cap_applied")),
            "black_box_advisory": bool(c.get("black_box_advisory")),
            "composite_score": c.get("composite_score"),
            "strong_match": bool(c.get("strong_match")),
        }
        try:
            layer1 = get_molecule_safety_flags(drug, c.get("molecule_chembl_id"))
            layer2 = (web_safety_check(drug) if _needs_layer2(c, layer1)
                      else c.get("safety_layer2"))
        except Exception as exc:  # explicit: record + leave fields untouched
            errors.append({"drug": drug, "error": f"{type(exc).__name__}: {exc}"})
            continue

        pre_cap = _reconstruct_pre_cap(c)
        c["pre_cap_score"] = pre_cap
        composite = pre_cap
        if _other_cap_applies(c):
            composite = min(composite, SAFETY_CAP)
        c["composite_score"] = composite
        c["safety_layer1"] = layer1
        c["safety_layer2"] = layer2
        _reconcile_safety(c, layer1, layer2)
        c["strong_match"] = c["composite_score"] >= STRONG_MATCH_THRESHOLD

        new = {
            "status_badge": c.get("status_badge"),
            "safety_cap_applied": bool(c.get("safety_cap_applied")),
            "black_box_advisory": bool(c.get("black_box_advisory")),
            "composite_score": c.get("composite_score"),
            "strong_match": bool(c.get("strong_match")),
        }
        if new != old:
            changes.append({"drug": drug, "before": old, "after": new})

    candidates.sort(
        key=lambda r: (r.get("composite_score") or 0.0,
                       r.get("pre_cap_score") or 0.0),
        reverse=True,
    )
    payload["safety_schema_version"] = SAFETY_SCHEMA_VERSION
    payload["safety_refreshed_at"] = datetime.date.today().isoformat()
    payload["n_strong_matches"] = sum(1 for c in candidates if c.get("strong_match"))

    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.replace(path, os.path.join(BACKUP_DIR, name))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)

    return {
        "file": name,
        "candidates": len(candidates),
        "changed": changes,
        "errors": errors,
        "n_strong_matches": payload["n_strong_matches"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true",
                        help="refresh even snapshots already stamped current")
    args = parser.parse_args()

    paths = sorted(glob.glob(os.path.join(CANDIDATES_DIR, "*.json")))
    if not paths:
        raise SystemExit(f"no pool snapshots found in {CANDIDATES_DIR}")

    report = {"safety_schema_version": SAFETY_SCHEMA_VERSION, "files": []}
    for path in paths:
        result = refresh_file(path, args.force)
        report["files"].append(result)
        changed = result.get("changed")
        if changed is None:
            print(f"[refresh] {result['file']}: {result['skipped']}")
        else:
            print(f"[refresh] {result['file']}: {result['candidates']} candidates, "
                  f"{len(changed)} changed, {len(result['errors'])} errors, "
                  f"{result['n_strong_matches']} strong matches")
            for ch in changed:
                print(f"    {ch['drug']}: cap {ch['before']['safety_cap_applied']}→"
                      f"{ch['after']['safety_cap_applied']}  badge "
                      f"{str(ch['before']['status_badge'])[:40]!r}→"
                      f"{str(ch['after']['status_badge'])[:40]!r}  composite "
                      f"{ch['before']['composite_score']}→{ch['after']['composite_score']}")

    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(f"[refresh] report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
