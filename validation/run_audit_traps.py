"""
Audit trap benchmark — measures AgentBio's detection of known failure classes
as an AUDIT tool (not discovery accuracy).

Pre-registered in validation/audit_traps_preregistration.md (v1, frozen
2026-08-03). Offline-first: every trap drives production code
(api.audit / api.triage / api.dossier / agents.reviewer /
data_sources.multisource_candidates) against stubbed inputs. No live
ChEMBL/PubChem/LLM calls.

LABEL GUARD: runs ONLY under --label audit_trap_benchmark. This artifact must
never be reported as benchmark v2 or as discovery accuracy.

Usage:
    python3 -m validation.run_audit_traps --label audit_trap_benchmark [--fresh]

Artifacts:
    validation/audit_trap_results.json
    validation/audit_trap_results.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED_LABEL = "audit_trap_benchmark"
FORBIDDEN_LABELS = {"benchmark_v2", "engineering_acceptance"}

RESULTS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "audit_trap_results.json")
RESULTS_MD = RESULTS_JSON.replace(".json", ".md")

# Pre-registered thresholds (audit_traps_preregistration.md). Do not move
# these after a scored run — a failure is a product defect, not a threshold.
PASS_MIN_TRAP_RECALL = 0.90
PASS_MAX_CONTROL_FALSE_FLAG_RATE = 0.25


# --------------------------------------------------------------------------- #
# Trap and control cases — frozen literals. Changing these after the first
# scored run invalidates comparability with prior artifacts.
# --------------------------------------------------------------------------- #

def _pool_audit(pool: list[dict], drug: str, chembl_id: str | None,
                moa: list[str] | None = None) -> dict:
    """Drive the REAL audit path (run_audit) against a stubbed pool: jobs DB
    lookup, candidate-file load, ChEMBL resolution, and MoA lookup are mocked;
    everything from pool-row -> cap disclosure -> verdict is production code.

    Layer note: these traps cover the audit/mapping surfaces (run_audit,
    _cap_reason, triage _verdict, dossier status). Upstream DERIVATION of the
    fixture fields (who sets safety_cap_applied, mechanism_cap_applied, the L2
    black-box/withdrawal split, composite capping) is covered by the named
    guard suites: validation/test_safety_layer2_split.py,
    test_reviewer_ranking_integrity.py, test_ai_client_bounds.py. A trap that
    re-derived those upstream would duplicate the pipeline, not audit it.
    """
    import api.audit as audit_mod

    with mock.patch.object(audit_mod.jobs_db, "find_completed_job_by_disease",
                           return_value={"job_id": "job-trap", "disease_name": "TrapDisease"}), \
         mock.patch.object(audit_mod, "_load_candidates", return_value=pool), \
         mock.patch.object(audit_mod, "_find_molecule_chembl_id",
                           return_value=chembl_id), \
         mock.patch.object(
             audit_mod,
             "get_drug_mechanism_identities_for_audit",
             return_value={
                 "status": "ok" if moa else "empty",
                 "provider": "chembl",
                 "resolved_molecule_chembl_id": chembl_id,
                 "identity_route": "provided_chembl_id",
                 "targets": [{
                     "target_chembl_id": None,
                     "target_name": None,
                     "target_type": None,
                     "organism": None,
                     "tax_id": None,
                     "uniprot_ids": [],
                     "gene_symbols": [],
                     "mechanisms": moa or [],
                     "action_types": [],
                 }] if moa else [],
                 "error": None,
             },
         ):
        return audit_mod.run_audit("TrapDisease", drug, narrate=False)


def _t1_safety_cap_disclosure() -> bool:
    """Capped candidate must surface cap + capped score, never the raw 0.72."""
    from api.triage import _verdict, F_SAFETY_CAP

    cand = {
        "drug_name": "TrapDrug1", "molecule_chembl_id": "CHEMBL_T1",
        "composite_score": 0.4, "pre_cap_score": 0.72,
        "safety_cap_applied": True, "status_badge": "DILI concern",
        "pubchem_xlogp": 2.0, "high_lipophilicity_flag": False,
        "score_components": {"evidence_weight_coverage": 1.0},
    }
    audit = _pool_audit([cand, {"drug_name": "Other", "molecule_chembl_id": "CHEMBL_O"}],
                        "TrapDrug1", "CHEMBL_T1")
    verdict = _verdict("TrapDrug1", audit)
    return (
        audit["status"] == "found"
        and audit["cap_applied"] is True
        and "Safety cap" in (audit["cap_reason"] or "")
        and F_SAFETY_CAP in verdict["flags"]
        and verdict["composite_score"] <= 0.400
        and verdict["pre_cap_score"] == 0.72  # raw score disclosed, not hidden
    )


def _t2_blackbox_not_withdrawal() -> bool:
    """Black-box-only is advisory; it must NOT trip the safety cap."""
    from api.triage import _verdict, F_BLACK_BOX, F_SAFETY_CAP

    cand = {
        "drug_name": "TrapDrug2", "molecule_chembl_id": "CHEMBL_T2",
        "composite_score": 0.61, "pre_cap_score": 0.61,
        "safety_cap_applied": False, "black_box_advisory": True,
        "pubchem_xlogp": 1.5, "high_lipophilicity_flag": False,
        "score_components": {"evidence_weight_coverage": 1.0},
    }
    audit = _pool_audit([cand], "TrapDrug2", "CHEMBL_T2")
    verdict = _verdict("TrapDrug2", audit)
    return (
        audit["status"] == "found"
        and audit["cap_applied"] is False
        and audit["cap_reason"] is None
        and F_BLACK_BOX in verdict["flags"]
        and F_SAFETY_CAP not in verdict["flags"]
    )


def _t3_direction_incompatible() -> bool:
    """Directionally incompatible mechanism must be capped and disclosed."""
    from api.triage import _verdict, F_MECHANISM_CAP

    cand = {
        "drug_name": "TrapDrug3", "molecule_chembl_id": "CHEMBL_T3",
        "composite_score": 0.4, "pre_cap_score": 0.55,
        "mechanism_cap_applied": True,
        "mechanism_direction": {
            "verdict": "INCOMPATIBLE", "auto_precap": False,
            "reason": "drug inhibits a target the disease requires activated",
        },
        "pubchem_xlogp": 3.0, "high_lipophilicity_flag": False,
        "score_components": {"evidence_weight_coverage": 1.0},
    }
    audit = _pool_audit([cand], "TrapDrug3", "CHEMBL_T3")
    verdict = _verdict("TrapDrug3", audit)
    return (
        audit["status"] == "found"
        and "Mechanism-direction cap" in (audit["cap_reason"] or "")
        and F_MECHANISM_CAP in verdict["flags"]
    )


def _t4_label_artifact_screen() -> bool:
    """Admin-exclude-class association must be called an artifact, not a signal."""
    from api.dossier import audit_status_for, parse_reviewer_tag

    tag = parse_reviewer_tag(
        "LABEL_ARTIFACT_SUSPECT: admin-only replay reproduces the effect", True
    )
    facts = {"passed_both": True, "framings": [], "confound_check": None}
    status, _ = audit_status_for(facts, [tag])
    return tag == "LABEL_ARTIFACT_SUSPECT" and status == "label_artifact_suspect"


def _t5_confirmation_discipline() -> bool:
    """Discovery-significant but holdout-failed is NOT confirmed."""
    from api.dossier import audit_status_for

    facts = {
        "passed_both": False,
        "framings": [
            {"framing": "narrow", "discovery_pass": True, "confirmation_pass": True},
            {"framing": "broad", "discovery_pass": True, "confirmation_pass": False},
        ],
        "confound_check": None,
    }
    # passed_both is False here because collect_facts requires the SAME row to
    # pass both; the status layer must still refuse to call this confirmed.
    status, reasons = audit_status_for(facts, ["READY"])
    return status == "not_confirmed" and any("broad" in r for r in reasons)


def _t6_unresolvable_name_honesty() -> bool:
    """Unresolvable name => UNRESOLVED, never a false authoritative 'absent'."""
    pool = [{"drug_name": "RealDrug", "molecule_chembl_id": "CHEMBL_R"}]
    result = _pool_audit(pool, "Asprin", None)
    return result["status"] == "unresolved" and result["resolved_chembl_id"] is None


def _t7_salt_form_dedup() -> bool:
    """Same active moiety (same InChIKey block) dedups; different moieties don't."""
    from data_sources.multisource_candidates import _same_candidate

    parent = {"drug_name": "Trapamine", "inchikey": "AAAAAAAAAAAAAA-BBBBBBBB-C",
              "molecule_chembl_id": "CHEMBL_P"}
    salt = {"drug_name": "Trapamine hydrochloride",
            "inchikey": "AAAAAAAAAAAAAA-DDDDDDDD-E",
            "molecule_chembl_id": "CHEMBL_S"}
    other = {"drug_name": "Trapamine", "inchikey": "ZZZZZZZZZZZZZZ-BBBBBBBB-C",
             "molecule_chembl_id": "CHEMBL_O"}
    return _same_candidate(parent, salt) and not _same_candidate(parent, other)


def _t8_degraded_source_honesty() -> bool:
    """Provider health must be preserved verbatim — failure never becomes ok."""
    from data_sources.multisource_candidates import _source_status

    degraded = _source_status({
        "status": "degraded", "error": "HTTP 503 (transient)", "release": "v36",
    })
    failed = _source_status({"status": "error", "error": "timeout", "release": None})
    ok = _source_status({"status": "ok", "error": None, "release": "v36"})
    return (
        degraded["status"] == "degraded" and degraded["error"]
        and failed["status"] == "error" and ok["status"] == "ok"
    )


def _t9_unobserved_not_zero() -> bool:
    """Unmeasured terms drop from BOTH sides; composite is renormalized."""
    from agents.reviewer import COMPOSITE_WEIGHTS, _coverage_aware_composite

    composite, coverage = _coverage_aware_composite(0.8, None, None, None)
    total_weight = sum(COMPOSITE_WEIGHTS.values())
    naive_zero_filled = (COMPOSITE_WEIGHTS["efficacy_evidence"] * 0.8) / total_weight
    return (
        abs(composite - 0.8) < 1e-9            # renormalized to observed weight
        and coverage == COMPOSITE_WEIGHTS["efficacy_evidence"]
        and composite > naive_zero_filled       # "never looked" != "measured 0"
    )


def _t10_xlogp_unresolved_disclosure() -> bool:
    """PubChem-unresolvable XLogP is UNRESOLVED, never silently low-risk."""
    from api.triage import _verdict, F_XLOGP_CAUTION, F_XLOGP_UNRESOLVED

    cand = {
        "drug_name": "TrapDrug10", "molecule_chembl_id": "CHEMBL_T10",
        "composite_score": 0.5, "pre_cap_score": 0.5,
        "pubchem_xlogp": None, "high_lipophilicity_flag": False,
        "score_components": {"evidence_weight_coverage": 1.0},
    }
    audit = _pool_audit([cand], "TrapDrug10", "CHEMBL_T10")
    verdict = _verdict("TrapDrug10", audit)
    return (
        audit["status"] == "found"
        and F_XLOGP_UNRESOLVED in verdict["flags"]
        and F_XLOGP_CAUTION not in verdict["flags"]
        and verdict["xlogp_status"] == "unresolved"
    )


def _c1_clean_candidate() -> bool:
    """Clean approved candidate must collect ZERO flags."""
    from api.triage import _verdict

    cand = {
        "drug_name": "CleanDrug", "composite_score": 0.66, "pre_cap_score": 0.66,
        "is_approved_drug": True, "pubchem_xlogp": 2.1,
        "high_lipophilicity_flag": False, "black_box_advisory": False,
        "score_components": {"evidence_weight_coverage": 1.0},
    }
    verdict = _verdict("CleanDrug", {
        "status": "found", "rank": 1, "total_candidates": 20,
        "resolved_chembl_id": "CHEMBL_C1", "cap_reason": None, "candidate": cand,
    })
    return verdict["flags"] == [] and verdict["xlogp_status"] == "clear"


def _c2_verified_hypothesis() -> bool:
    """Fully confirmed + all computable confounds survived => VERIFIED."""
    from api.dossier import audit_status_for

    facts = {
        "passed_both": True,
        "framings": [{"framing": "narrow", "discovery_pass": True,
                      "confirmation_pass": True}],
        "confound_check": {
            "status": "completed",
            "confounds": [
                {"name": "saturation", "adjustment_result": {"survives": True}},
                {"name": "product status", "adjustment_result": {"survives": True}},
            ],
        },
    }
    status, _ = audit_status_for(facts, ["READY"])
    return status == "verified"


def _c3_measured_zero_counts() -> bool:
    """A MEASURED tanimoto 0.0 stays in scoring — opposite of 'unobserved'."""
    from agents.reviewer import COMPOSITE_WEIGHTS, _coverage_aware_composite

    with_zero, cov_zero = _coverage_aware_composite(0.8, None, 0.0, None)
    without, cov_without = _coverage_aware_composite(0.8, None, None, None)
    w_e = COMPOSITE_WEIGHTS["efficacy_evidence"]
    w_t = COMPOSITE_WEIGHTS["tanimoto"]
    expected = (w_e * 0.8 + w_t * 0.0) / (w_e + w_t)
    return (
        abs(with_zero - expected) < 1e-9
        and cov_zero == w_e + w_t               # measured zero keeps its weight
        and cov_without == w_e
        and with_zero < without                 # adverse measured evidence hurts
    )


def _c4_resolved_absent_not_unresolved() -> bool:
    """Resolvable drug genuinely not in pool => ABSENT, not UNRESOLVED."""
    pool = [{"drug_name": "RealDrug", "molecule_chembl_id": "CHEMBL_R",
             "target_symbol": "TGT"}]
    result = _pool_audit(pool, "GenuineDrug", "CHEMBL_OTHER", moa=["OTHER_TGT"])
    return result["status"] == "absent" and result["resolved_chembl_id"] == "CHEMBL_OTHER"


def _t11_degraded_200_empty_pool_not_cached() -> bool:
    """A degraded 200-with-empty-payload must NEVER be cached as an empty pool.

    Reproduces the 2026-07 incident class: ChEMBL returns HTTP 200 with an
    empty activities payload during an outage; caching that emptiness would
    zero the target's pool for 7 days. Drives the real public pool builder
    with only the transport seams mocked; asserts the cache gate refuses the
    write and the returned pool is honestly (not authoritatively) empty.
    """
    import data_sources.chembl as chembl

    writes: list[tuple] = []
    with mock.patch.object(chembl, "get", return_value=None), \
         mock.patch.object(chembl, "cache_set",
                           lambda *a, **k: writes.append(a)), \
         mock.patch.object(chembl, "_resolve_target_chembl_id",
                           return_value=["CHEMBL_T11"]), \
         mock.patch.object(chembl, "_fetch_activities_full",
                           return_value=([], False)):
        result = chembl.get_target_candidate_compounds("P0T11X")
    return result["compounds"] == [] and writes == []


def _t12_holdout_name_no_api_leak() -> bool:
    """Under an active benchmark holdout, the precedent-target path must not
    leak the held-out drug — by exact name, by salt/ester shared parent, or
    via the ChEMBL drug_indication EFO fallback rediscovering it after
    redaction empties the list. A naive tool falls through to the fallback
    and re-discovers the drug it was supposed to hide.
    """
    import data_sources.chembl as chembl
    from data_sources import holdout

    def _resolve_stub():
        holdout.register_molecules({"CHEMBL_HELD"}, {"CHEMBL_PARENT"})
        holdout.mark_resolved()

    mol_table = {
        "TrapHeldOutDrug citrate": "CHEMBL_SALT",
        "CleanDrug": "CHEMBL_CLEAN",
    }
    meta = {
        "CHEMBL_SALT": {"parent_chembl_id": "CHEMBL_PARENT"},
        "CHEMBL_CLEAN": {"parent_chembl_id": "CHEMBL_CLEANP"},
    }
    fallback_calls: list = []
    sentinel_writes: list = []

    def _cache_set_spy(key, value, **kw):
        # make_key() hashes its arguments, so the sentinel name is NOT
        # string-matchable in the key — assert by VALUE: the sentinel path
        # is the only cache_set in this function that writes an empty list.
        if value == []:
            sentinel_writes.append((key, value))

    with holdout.holdout_active(["TrapHeldOutDrug"]):
        with mock.patch.object(chembl, "_ensure_holdout_resolved", _resolve_stub), \
             mock.patch.object(chembl, "_find_molecule_chembl_id",
                               lambda name: mol_table.get(name)), \
             mock.patch.object(chembl, "_fetch_molecule_meta",
                               lambda ids: {i: meta.get(i, {}) for i in ids}), \
             mock.patch.object(chembl, "_get_json",
                               lambda *a, **k: fallback_calls.append(a) or {"drug_indications": []}), \
             mock.patch.object(chembl, "get", return_value=None), \
             mock.patch.object(chembl, "cache_set", _cache_set_spy):
            # 1. Exact-name redaction empties the list -> sentinel, no fallback
            r1 = chembl.get_pharmacological_targets_for_disease(
                "EFO_0000001", ["TrapHeldOutDrug"])
            # 2. Salt form redacted via shared parent -> same short-circuit
            r2 = chembl.get_pharmacological_targets_for_disease(
                "EFO_0000001", ["TrapHeldOutDrug citrate"])
            # 3. Mixed list: clean drug survives; held-out name + salt removed
            kept = chembl.redact_holdout_names(
                ["TrapHeldOutDrug", "TrapHeldOutDrug citrate", "CleanDrug"])

    return (
        r1 == [] and r2 == []
        and fallback_calls == []
        and len(sentinel_writes) >= 1
        and kept == ["CleanDrug"]
    )


TRAPS: list[tuple[str, str, Callable[[], bool]]] = [
    ("T1", "safety_cap_disclosure", _t1_safety_cap_disclosure),
    ("T2", "blackbox_not_withdrawal", _t2_blackbox_not_withdrawal),
    ("T3", "direction_incompatible", _t3_direction_incompatible),
    ("T4", "label_artifact_screen", _t4_label_artifact_screen),
    ("T5", "confirmation_discipline", _t5_confirmation_discipline),
    ("T6", "unresolvable_name_honesty", _t6_unresolvable_name_honesty),
    ("T7", "salt_form_dedup", _t7_salt_form_dedup),
    ("T8", "degraded_source_honesty", _t8_degraded_source_honesty),
    ("T9", "unobserved_not_zero", _t9_unobserved_not_zero),
    ("T10", "xlogp_unresolved_disclosure", _t10_xlogp_unresolved_disclosure),
    # v1.1 (2026-08-04): pre-registered in audit_traps_preregistration.md
    # addendum BEFORE implementation; thresholds unchanged.
    ("T11", "degraded_200_empty_pool_not_cached", _t11_degraded_200_empty_pool_not_cached),
    ("T12", "holdout_name_no_api_leak", _t12_holdout_name_no_api_leak),
]

CONTROLS: list[tuple[str, str, Callable[[], bool]]] = [
    ("C1", "clean_candidate_no_flags", _c1_clean_candidate),
    ("C2", "verified_hypothesis_not_flagged", _c2_verified_hypothesis),
    ("C3", "measured_zero_still_counts", _c3_measured_zero_counts),
    ("C4", "resolved_absent_not_unresolved", _c4_resolved_absent_not_unresolved),
]


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run() -> dict[str, Any]:
    trap_rows: list[dict[str, Any]] = []
    for tid, cls, fn in TRAPS:
        try:
            caught = bool(fn())
            error = None
        except Exception as exc:  # noqa: BLE001 — an exception is a MISS, recorded
            caught, error = False, f"{type(exc).__name__}: {exc}"
        trap_rows.append({"id": tid, "class": cls, "caught": caught, "error": error})
        print(f"[trap {tid:>4}] {cls:<34} {'CAUGHT' if caught else 'MISSED'}"
              + (f"  ({error})" if error else ""))

    control_rows: list[dict[str, Any]] = []
    for cid, cls, fn in CONTROLS:
        try:
            clean = bool(fn())
            error = None
        except Exception as exc:  # noqa: BLE001 — an exception is a FALSE FLAG
            clean, error = False, f"{type(exc).__name__}: {exc}"
        control_rows.append({"id": cid, "class": cls, "clean": clean, "error": error})
        print(f"[ctrl {cid:>4}] {cls:<34} {'CLEAN' if clean else 'FALSE-FLAGGED'}"
              + (f"  ({error})" if error else ""))

    caught_n = sum(1 for r in trap_rows if r["caught"])
    flagged_n = sum(1 for r in control_rows if not r["clean"])
    recall = caught_n / len(trap_rows)
    false_flag_rate = flagged_n / len(control_rows)
    precision = caught_n / (caught_n + flagged_n) if (caught_n + flagged_n) else None
    verdict = (
        "PASS"
        if recall >= PASS_MIN_TRAP_RECALL
        and false_flag_rate <= PASS_MAX_CONTROL_FALSE_FLAG_RATE
        else "FAIL"
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": REQUIRED_LABEL,
        "kind": "audit_traps",
        "preregistration": "validation/audit_traps_preregistration.md",
        "offline": True,
        "traps": trap_rows,
        "controls": control_rows,
        "metrics": {
            "traps_total": len(trap_rows),
            "traps_caught": caught_n,
            "trap_recall": recall,
            "controls_total": len(control_rows),
            "controls_false_flagged": flagged_n,
            "control_false_flag_rate": false_flag_rate,
            "precision": precision,
            "thresholds": {
                "min_trap_recall": PASS_MIN_TRAP_RECALL,
                "max_control_false_flag_rate": PASS_MAX_CONTROL_FALSE_FLAG_RATE,
            },
        },
        "verdict": verdict,
        "limitations": (
            "Engineering acceptance instrument measuring audit-layer detection "
            "of known failure classes against stubbed inputs. NOT discovery "
            "accuracy; must never be reported as benchmark v2. External "
            "organizational validation still requires a frozen claim set with "
            "independent ground truth and an inter-rater study."
        ),
    }


def _write_markdown(result: dict[str, Any]) -> None:
    m = result["metrics"]
    lines = [
        "# Audit Trap Benchmark — Results",
        "",
        f"- Generated: {result['generated_at']}",
        f"- Label: `{result['label']}` (offline: {result['offline']})",
        f"- **Verdict: {result['verdict']}**",
        "",
        "## Metrics",
        "",
        f"- Trap recall: {m['traps_caught']}/{m['traps_total']} = {m['trap_recall']:.2f}"
        f" (threshold ≥ {m['thresholds']['min_trap_recall']})",
        f"- Control false-flag rate: {m['controls_false_flagged']}/{m['controls_total']}"
        f" = {m['control_false_flag_rate']:.2f}"
        f" (threshold ≤ {m['thresholds']['max_control_false_flag_rate']})",
        f"- Precision: {m['precision']:.2f}" if m["precision"] is not None else "- Precision: n/a",
        "",
        "## Traps (must be caught)",
        "",
        "| ID | Class | Outcome |",
        "|----|-------|---------|",
    ]
    lines += [
        f"| {r['id']} | {r['class']} | {'CAUGHT' if r['caught'] else '**MISSED**'} |"
        for r in result["traps"]
    ]
    lines += [
        "",
        "## Controls (must NOT be flagged)",
        "",
        "| ID | Class | Outcome |",
        "|----|-------|---------|",
    ]
    lines += [
        f"| {r['id']} | {r['class']} | {'CLEAN' if r['clean'] else '**FALSE-FLAGGED**'} |"
        for r in result["controls"]
    ]
    lines += ["", f"> {result['limitations']}", ""]
    with open(RESULTS_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--fresh", action="store_true",
                        help="delete any prior artifact before running")
    args = parser.parse_args()

    if args.label in FORBIDDEN_LABELS or args.label != REQUIRED_LABEL:
        print(
            f"[audit-traps] REFUSED: audit trap benchmark runs ONLY under label "
            f"{REQUIRED_LABEL!r}; got {args.label!r}. This harness measures "
            f"audit-layer detection and must never be reported as benchmark v2.",
            file=sys.stderr,
        )
        return 2

    if args.fresh and os.path.exists(RESULTS_JSON):
        os.remove(RESULTS_JSON)
        print("[audit-traps] --fresh: cleared prior artifact")

    result = run()
    with open(RESULTS_JSON, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    _write_markdown(result)
    print(f"[audit-traps] verdict={result['verdict']} -> {RESULTS_JSON}")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
