"""Study B (DESCRIPTIVE ONLY): full-configuration audit against rebuilt pools.

Rebuilds the candidate pools for the 12 in-scope primary benchmark diseases
via the same in-process per-target harness that produced
`benchmark_results_v2.json` (biologist -> chemist -> reviewer, top-K targets,
disease-side holdout active), then computes the evidence profile for each
disease's confirmed repurposing drug(s) and the pool's top candidates.

Pre-registered boundaries (triage_discrimination_preregistration.md):

* **Descriptive only.** No thresholds, no pass/fail. n is small by
  construction (22 confirmed cases, 12 diseases) and the pools are NOT
  disease-blind at the composite/rank/mechanism level (reviewer derives those
  from disease-linked OT/trial data). Every row carries that caveat.
* **Non-confirmed candidates are never scored as errors.** Absence of
  approval is not evidence of a wrong hypothesis; they are context rows only.
* **Fail-closed.** Existing results are never regenerated.

This run makes LLM calls (the pipeline agents). It is the user-approved
expensive half of the study (approved 2026-08-11).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.target_selection import DiseaseNotInUniverse, select_for_disease  # noqa: E402
from agents.biologist import run_biologist  # noqa: E402
from agents.chemist import run_chemist  # noqa: E402
from agents.reviewer import run_reviewer  # noqa: E402
from api.audit import _find_molecule_chembl_id  # noqa: E402
from api.audit_context import build_audit_context  # noqa: E402
from data_sources import holdout  # noqa: E402
from validation.evidence_profile import RULE_FINGERPRINT, build_profile  # noqa: E402

V2_RESULTS = ROOT / "validation" / "benchmark_results_v2.json"
POOLS_CKPT = ROOT / "validation" / "triage_discrimination_studyb_pools.jsonl"
RESULTS_PATH = ROOT / "validation" / "triage_discrimination_studyb_results.json"
REPORT_PATH = ROOT / "validation" / "triage_discrimination_studyb_report.md"

TOP_K = 3
TOP_N_AUDIT = 10  # pool candidates profiled per disease, as context rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _health_gate() -> None:
    probes = {
        "chembl": "https://www.ebi.ac.uk/chembl/api/data/status.json",
        "openfda": "https://api.fda.gov/drug/label.json?limit=1",
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
        raise SystemExit(f"[studyb] REFUSED: sources unhealthy: {failed}")


def _disease_cases() -> dict[str, list[str]]:
    """In-scope primary diseases -> confirmed drug names (from frozen v2)."""
    data = json.loads(V2_RESULTS.read_text())
    out: dict[str, list[str]] = {}
    for c in data["cases"]:
        if c.get("subset") != "primary" or c.get("status") == "out_of_scope":
            continue
        out.setdefault(c["disease_name"], []).append(c["drug_name"])
    return out


def _load_pools_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if POOLS_CKPT.exists():
        for line in POOLS_CKPT.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["disease_name"]] = rec
    return done


def _append_pool(rec: dict) -> None:
    with open(POOLS_CKPT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _build_pool(disease: str, drugs: list[str]) -> dict:
    """Run the benchmark harness for one disease; return the merged pool."""
    rows = select_for_disease(disease)
    per_target = []
    merged: dict[str, dict] = {}  # normalized name -> best-composite candidate
    for k_idx, row in enumerate(rows[:TOP_K], 1):
        target = {
            "target_symbol": row["target_symbol"],
            "uniprot_id": row.get("uniprot_id"),
            "ensembl_id": row.get("ensembl_id"),
            "disease_name": row["disease_name"],
            "orpha_code": row.get("orpha_code"),
            "ot_association_score": row.get("ot_association_score", 0.0),
            "tractability_score": row.get("tractability_score"),
            "unmet_need_score": row.get("unmet_need_score"),
        }
        print(f"[studyb]   {disease}: target {k_idx}/{TOP_K} "
              f"{row.get('target_symbol')}", flush=True)
        try:
            bio = run_biologist(target)
            chem = run_chemist(bio)
            reviewed = run_reviewer(chem, bio)
        except Exception as exc:  # noqa: BLE001
            per_target.append({"target_symbol": row.get("target_symbol"),
                               "error": str(exc)[:300]})
            continue
        for cand in reviewed:
            name_key = " ".join(str(
                cand.get("drug_name") or "").split()).casefold()
            if not name_key:
                continue
            prev = merged.get(name_key)
            if prev is None or float(cand.get("composite_score") or 0) > float(
                    prev.get("composite_score") or 0):
                merged[name_key] = cand
        per_target.append({"target_symbol": row.get("target_symbol"),
                           "n_reviewed": len(reviewed)})
    pool = sorted(merged.values(),
                  key=lambda c: float(c.get("composite_score") or 0),
                  reverse=True)
    return {
        "disease_name": disease,
        "holdout_drugs": drugs,
        "per_target": per_target,
        "pool_size": len(pool),
        # Persist the pool so an interrupted profile phase never re-runs the
        # LLM pipeline. Candidates are plain dicts (JSON-safe).
        "pool": pool,
    }


def _profile_drug(disease: str, drug: str, pool: list[dict],
                  total: int) -> dict:
    """Profile one drug against one pool — the same fields run_audit emits."""
    deadline = time.monotonic() + 30.0
    chembl_id = None
    try:
        chembl_id = _find_molecule_chembl_id(drug)
    except Exception:  # noqa: BLE001
        chembl_id = None
    key = " ".join(drug.split()).casefold()
    rank, cand = None, None
    for i, c in enumerate(pool, 1):
        if " ".join(str(c.get("drug_name") or "").split()).casefold() == key:
            rank, cand = i, c
            break
    top = pool[0] if pool else None
    status = "found" if cand is not None else (
        "unresolved" if chembl_id is None else "absent")
    mechanism_symbol = str(
        (cand or {}).get("target_symbol")
        or (top or {}).get("target_symbol") or "")
    ctx = build_audit_context(drug, mechanism_symbol=mechanism_symbol,
                              deadline_monotonic=deadline)
    if not (ctx.get("holdout_redaction") or {}).get("applied"):
        raise RuntimeError(f"{drug}: unredacted audit context under holdout")
    audit = {"status": status, "rank": rank, "total_candidates": total,
             "candidate": cand, "audit_context": ctx}
    return {"drug_name": drug, "disease_name": disease, "rank": rank,
            "pool_status": status,
            "profile": build_profile(drug, audit)}


def main() -> None:
    if RESULTS_PATH.exists():
        raise SystemExit("[studyb] REFUSED: results exist. Amend, never "
                         "regenerate.")
    _health_gate()
    diseases = _disease_cases()
    pools_done = _load_pools_checkpoint()
    print(f"[studyb] {len(diseases)} diseases, "
          f"{len(pools_done)} pools checkpointed", flush=True)

    table: list[dict] = []
    for disease, drugs in sorted(diseases.items()):
        pool_rec = pools_done.get(disease)
        if pool_rec is None:
            # Holdout seals disease-side discovery leakage for the pipeline
            # phase AND switches on audit-lane redaction for the profile phase.
            with holdout.holdout_active(drugs):
                pool_rec = _build_pool(disease, drugs)
            _append_pool(pool_rec)
            print(f"[studyb] {disease}: pool={pool_rec['pool_size']}",
                  flush=True)
        pool = pool_rec["pool"]
        total = pool_rec["pool_size"]
        top_names = [str(c.get("drug_name")) for c in pool[:TOP_N_AUDIT]]
        with holdout.holdout_active(drugs):
            for drug in drugs:  # confirmed repurposings: the rows of interest
                table.append({**_profile_drug(disease, drug, pool, total),
                              "row_kind": "confirmed"})
            for name in top_names:  # context rows only, never scored
                if name.casefold() not in {d.casefold() for d in drugs}:
                    table.append({**_profile_drug(disease, name, pool, total),
                                  "row_kind": "context_top_candidate"})

    payload = {
        "contract": "triage-discrimination-studyb-v1",
        "descriptive_only": True,
        "non_disease_blind_pool_caveat": (
            "rank/composite/mechanism dimensions derive from a pool built "
            "with disease-linked OpenTargets and trial data; only the "
            "disease-independent dimensions are provably blind."),
        "rule_fingerprint": RULE_FINGERPRINT,
        "v2_results_sha256": _sha256_file(V2_RESULTS),
        "n_diseases": len(diseases),
        "rows": table,
    }
    payload["results_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"[studyb] wrote {RESULTS_PATH} ({len(table)} rows)", flush=True)


if __name__ == "__main__":
    main()
