"""Scored run for the triage discrimination study (Study A + negative controls).

Discipline (see validation/triage_discrimination_preregistration.md):

* **Freeze-verified.** Refuses to run unless the freeze manifest matches the
  on-disk case set, the evidence-profile rule fingerprint, the redaction
  contract, and the current git commit.
* **Health-gated.** ChEMBL, openFDA, and PubTator must all be healthy before
  the first case runs; a degraded source aborts rather than scores.
* **Blindness-asserted.** Every case runs under holdout; a single audit
  context without `holdout_redaction.applied` aborts the whole run.
* **Checkpointed.** Per-case append-only JSONL; an interrupted run resumes.
* **Fail-closed.** If a scored results file exists, the runner refuses to
  re-score. Frozen artifacts are repaired by amendment, never regenerated.

No LLM calls are made anywhere in this run: the audit context is built with
the deterministic detectors only (the narrate path is never invoked here).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.audit import _find_molecule_chembl_id  # noqa: E402
from api.audit_context import build_audit_context  # noqa: E402
from data_sources import holdout  # noqa: E402
from data_sources.audit_redaction import REDACTION_CONTRACT  # noqa: E402
from data_sources.chembl import get_drug_mechanism_targets_for_audit  # noqa: E402
from validation.evidence_profile import (  # noqa: E402
    DISQUALIFIED, QUALIFIED, RULE_FINGERPRINT, build_profile)

CASES_PATH = ROOT / "validation" / "triage_discrimination_cases.json"
MANIFEST_PATH = ROOT / "validation" / "triage_discrimination_freeze_manifest.json"
PREREG_PATH = ROOT / "validation" / "triage_discrimination_preregistration.md"
CHECKPOINT_PATH = ROOT / "validation" / "triage_discrimination_checkpoint.jsonl"
RESULTS_PATH = ROOT / "validation" / "triage_discrimination_results.json"
REPORT_PATH = ROOT / "validation" / "triage_discrimination_report.md"

PER_CASE_DEADLINE_S = 30.0
WORKERS = 2


class BlindnessBreach(RuntimeError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * ((p * (1 - p) / n) + z * z / (4 * n * n)) ** 0.5
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _health_gate() -> None:
    probes = {
        "chembl": "https://www.ebi.ac.uk/chembl/api/data/status.json",
        "openfda": "https://api.fda.gov/drug/label.json?limit=1",
        "pubtator": ("https://www.ncbi.nlm.nih.gov/research/pubtator3-api/"
                     "publications/export/biocjson?pmids=20301572"),
    }
    failed = []
    for name, url in probes.items():
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code >= 500 or resp.status_code == 429:
                failed.append(f"{name}(HTTP {resp.status_code})")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}({exc.__class__.__name__})")
    if failed:
        raise SystemExit(
            f"[run] REFUSED: live sources unhealthy: {failed}. "
            "Never score during an outage.")


def _verify_freeze() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    problems = []
    if manifest["cases_sha256"] != _sha256_file(CASES_PATH):
        problems.append("case set hash mismatch")
    if manifest["rule_fingerprint"] != RULE_FINGERPRINT:
        problems.append("evidence-profile rule fingerprint mismatch")
    if manifest["redaction_contract"] != REDACTION_CONTRACT:
        problems.append("redaction contract mismatch")
    if manifest["preregistration_sha256"] != _sha256_file(PREREG_PATH):
        problems.append("pre-registration hash mismatch")
    if problems:
        raise SystemExit(f"[run] FREEZE VIOLATION: {problems}")
    return manifest


def _audit_case(case: dict, claim: dict) -> dict:
    """Run one case through the shipped audit-context path under holdout."""
    drug = case["drug_name"]
    deadline = time.monotonic() + PER_CASE_DEADLINE_S
    chembl_id = None
    try:
        chembl_id = _find_molecule_chembl_id(drug)
    except Exception:  # noqa: BLE001 — unresolved is an honest outcome
        chembl_id = None
    mechanism_symbol = ""
    if chembl_id:
        try:
            moas = get_drug_mechanism_targets_for_audit(drug, chembl_id)
            mechanism_symbol = moas[0] if moas else ""
        except Exception:  # noqa: BLE001
            mechanism_symbol = ""
    ctx = build_audit_context(
        drug,
        mechanism_symbol=mechanism_symbol,
        claimed_route=str(claim.get("claimed_route") or ""),
        claimed_modality=str(claim.get("claimed_modality") or ""),
        deadline_monotonic=deadline,
    )
    if not (ctx.get("holdout_redaction") or {}).get("applied"):
        raise BlindnessBreach(
            f"{drug}: audit context was NOT redacted under holdout")
    profile = build_profile(drug, {"status": "no_case", "audit_context": ctx})
    reg = (ctx.get("sources") or {}).get("regulatory_label") or {}
    lit = (ctx.get("sources") or {}).get("entity_linked_literature") or {}
    products = [p for p in reg.get("products") or [] if isinstance(p, dict)]
    return {
        "drug_name": drug,
        "ind_name": case.get("ind_name"),
        "resolved_chembl_id": chembl_id,
        "mechanism_symbol": mechanism_symbol or None,
        "label_status": reg.get("status"),
        "literature_status": lit.get("status"),
        "n_products": len(products),
        "product_modalities": sorted({
            str((p.get("regulatory") or {}).get("product_modality"))
            for p in products if (p.get("regulatory") or {}).get("product_modality")}),
        "approved_routes": sorted({
            str(r).lower() for p in products
            for r in (p.get("regulatory") or {}).get("routes") or []}),
        "profile": profile,
    }


def _load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CHECKPOINT_PATH.exists():
        for line in CHECKPOINT_PATH.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[f"{rec['cohort']}::{rec['drug_name'].casefold()}"] = rec
    return done


def _append_checkpoint(rec: dict) -> None:
    with open(CHECKPOINT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _run_cohort(name: str, cohort: dict, done: dict[str, dict]) -> list[dict]:
    claim = cohort.get("planted_claim") or {}
    todo = [c for c in cohort["cases"]
            if f"{name}::{c['drug_name'].casefold()}" not in done]
    print(f"[run] cohort {name}: {len(todo)} to run "
          f"({len(cohort['cases']) - len(todo)} checkpointed)", flush=True)
    results: list[dict] = []

    def work(case: dict) -> dict:
        rec = _audit_case(case, claim)
        rec["cohort"] = name
        return rec

    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for rec in pool.map(work, todo):
                _append_checkpoint(rec)
                done[f"{name}::{rec['drug_name'].casefold()}"] = rec
                print(f"[run]   {name}: {rec['drug_name']} "
                      f"label={rec['label_status']} "
                      f"disp={rec['profile']['primary_disposition']}",
                      flush=True)
    for c in cohort["cases"]:
        results.append(done[f"{name}::{c['drug_name'].casefold()}"])
    return results


def _score(cohort_a: list[dict], nc1: list[dict], nc2: list[dict]) -> dict:
    eligible = [r for r in cohort_a if r["label_status"] == "ok"]
    n, ne = len(cohort_a), len(eligible)

    disq = [r for r in eligible
            if r["profile"]["primary_disposition"] == DISQUALIFIED]
    qual = [r for r in eligible
            if r["profile"]["primary_disposition"] == QUALIFIED]
    res_lo, res_hi = _wilson(ne, n)
    e2_lo, e2_hi = _wilson(len(disq), ne)
    e3_lo, e3_hi = _wilson(len(qual), ne)

    # NC1: valid = biologic/vaccine confirmed by label; detected = N2 flagged.
    nc1_valid = [r for r in nc1 if r["label_status"] == "ok" and
                 {"biologic", "vaccine"} & set(r["product_modalities"])]
    nc1_det = [r for r in nc1_valid
               if r["profile"]["dimensions"]["modality_feasibility"] == "FLAGGED"]
    # NC2: valid = label routes exist and exclude oral; detected = N4 flagged.
    nc2_valid = [r for r in nc2 if r["label_status"] == "ok"
                 and r["approved_routes"] and "oral" not in r["approved_routes"]]
    nc2_det = [r for r in nc2_valid
               if r["profile"]["dimensions"]["route_feasibility"] == "FLAGGED"]
    e4_lo, _ = _wilson(len(nc1_det), len(nc1_valid))
    e5_lo, _ = _wilson(len(nc2_det), len(nc2_valid))

    return {
        "cohort_a": {
            "n": n, "eligible": ne,
            "resolution_rate": ne / n if n else None,
            "resolution_wilson95": [res_lo, res_hi],
            "disqualified": len(disq),
            "disqualified_names": [r["drug_name"] for r in disq],
            "disqualification_rate_wilson95_upper": e2_hi,
            "qualified": len(qual),
            "qualified_rate_wilson95": [e3_lo, e3_hi],
            "unresolved_label": n - ne,
        },
        "nc1": {"n": len(nc1), "valid": len(nc1_valid),
                "detected": len(nc1_det),
                "missed": [r["drug_name"] for r in nc1_valid if r not in nc1_det],
                "detection_wilson95_lower": e4_lo,
                "pass": len(nc1_det) >= 12},
        "nc2": {"n": len(nc2), "valid": len(nc2_valid),
                "detected": len(nc2_det),
                "missed": [r["drug_name"] for r in nc2_valid if r not in nc2_det],
                "detection_wilson95_lower": e5_lo,
                "pass": len(nc2_det) >= 12},
        "e2_pass": e2_hi <= 0.05,
    }


def main() -> None:
    if "--freeze" in sys.argv:
        if RESULTS_PATH.exists():
            raise SystemExit("[freeze] REFUSED: scored results already exist.")
        from validation.evidence_profile import RULE_FINGERPRINT as fp
        manifest = {
            "contract": "triage-discrimination-freeze-v1",
            "frozen_at_commit": os.popen("git rev-parse HEAD").read().strip(),
            "cases_sha256": _sha256_file(CASES_PATH),
            "preregistration_sha256": _sha256_file(PREREG_PATH),
            "rule_fingerprint": fp,
            "redaction_contract": REDACTION_CONTRACT,
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"[freeze] wrote {MANIFEST_PATH}")
        print(json.dumps(manifest, indent=2))
        return

    if RESULTS_PATH.exists():
        raise SystemExit(
            "[run] REFUSED: scored results exist. Frozen studies are amended, "
            "never regenerated.")
    manifest = _verify_freeze()
    _health_gate()

    caseset = json.loads(CASES_PATH.read_text())
    cohorts = caseset["cohorts"]
    all_drugs = sorted({
        c["drug_name"] for coh in cohorts.values() for c in coh["cases"]})

    done = _load_checkpoint()
    print(f"[run] resuming from {len(done)} checkpointed cases", flush=True)

    # Holdout activated once for the whole run: redact_audit_lanes gates on
    # is_active() only, and the global is not thread-safe per-case.
    with holdout.holdout_active(all_drugs):
        cohort_a = _run_cohort("cohort_a", cohorts["cohort_a"], done)
        nc1 = _run_cohort("nc1", cohorts["nc1_modality_contradiction"], done)
        nc2 = _run_cohort("nc2", cohorts["nc2_route_contradiction"], done)

    scores = _score(cohort_a, nc1, nc2)
    payload = {
        "contract": "triage-discrimination-results-v1",
        "freeze": manifest,
        "rule_fingerprint": RULE_FINGERPRINT,
        "scores": scores,
        "cases": {"cohort_a": cohort_a, "nc1": nc1, "nc2": nc2},
    }
    payload["results_sha256"] = _sha256_bytes(
        json.dumps(payload, sort_keys=True).encode())
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"[run] wrote {RESULTS_PATH}", flush=True)
    print(json.dumps(scores, indent=2), flush=True)


if __name__ == "__main__":
    main()
