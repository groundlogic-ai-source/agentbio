"""Scored run for the EXPANDED negative controls (NC1/NC2 expansion).

Mirrors validation/run_triage_discrimination.py's NC path exactly — same
_audit_case execution, same health gate, same holdout-blindness assertion,
same Wilson math, same checkpoint/fail-closed discipline. No LLM calls are
made anywhere in this run: the audit context is built with the deterministic
detectors only.

Pooling: the expansion exists to tighten the detection-rate lower bound
beyond what the frozen v2 controls (15+15) could support. If the frozen v2
results carry the SAME rule fingerprint as the current code, the scores
section pools v2 + expansion into one detection-rate estimate per control
type; if the fingerprint differs, pooling is refused and expansion-only
stats are reported (a rule change means the two generations measured
different detectors).
"""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_sources import holdout  # noqa: E402
from validation.evidence_profile import RULE_FINGERPRINT  # noqa: E402
from validation.run_triage_discrimination import (  # noqa: E402
    PER_CASE_DEADLINE_S, WORKERS, _audit_case, _health_gate, _wilson)

CASES_PATH = (ROOT / "validation"
              / "triage_discrimination_nc_expansion_cases.json")
CHECKPOINT_PATH = (ROOT / "validation"
                   / "triage_discrimination_nc_expansion_checkpoint.jsonl")
RESULTS_PATH = (ROOT / "validation"
                / "triage_discrimination_nc_expansion_results.json")
V2_RESULTS_PATH = (ROOT / "validation"
                   / "triage_discrimination_results_v2.json")

RESULTS_CONTRACT = "triage-discrimination-nc-expansion-results-v1"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cases_sha256() -> str:
    return _sha256_bytes(CASES_PATH.read_bytes())


def _load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CHECKPOINT_PATH.exists():
        for line in CHECKPOINT_PATH.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if (rec.get("cases_sha256") != _cases_sha256()
                    or rec.get("rule_fingerprint") != RULE_FINGERPRINT):
                raise SystemExit(
                    "[run] REFUSED: checkpoint record predates the current "
                    f"case set or rules ({rec.get('cohort')}::"
                    f"{rec.get('drug_name')}). Move the stale checkpoint "
                    "aside or rebuild deliberately.")
            done[f"{rec['cohort']}::{rec['drug_name'].casefold()}"] = rec
    return done


def _append_checkpoint(rec: dict) -> None:
    rec = {**rec,
           "cases_sha256": _cases_sha256(),
           "rule_fingerprint": RULE_FINGERPRINT}
    with open(CHECKPOINT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def _run_cohort(name: str, cohort: dict, done: dict[str, dict]) -> list[dict]:
    claim = cohort.get("planted_claim") or {}
    todo = [c for c in cohort["cases"]
            if f"{name}::{c['drug_name'].casefold()}" not in done]
    print(f"[run] cohort {name}: {len(todo)} to run "
          f"({len(cohort['cases']) - len(todo)} checkpointed)", flush=True)

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
    return [done[f"{name}::{c['drug_name'].casefold()}"]
            for c in cohort["cases"]]


def _nc1_stats(records: list[dict]) -> dict:
    """Valid = biologic/vaccine confirmed by label; detected = N2 flagged."""
    valid = [r for r in records if r["label_status"] == "ok" and
             {"biologic", "vaccine"} & set(r["product_modalities"])]
    det = [r for r in valid
           if r["profile"]["dimensions"]["modality_feasibility"] == "FLAGGED"]
    lo, _ = _wilson(len(det), len(valid))
    return {"n": len(records), "valid": len(valid), "detected": len(det),
            "missed": [r["drug_name"] for r in valid if r not in det],
            "detection_wilson95_lower": lo,
            "pass": bool(valid) and len(det) / len(valid) >= 0.8}


def _nc2_stats(records: list[dict]) -> dict:
    """Valid = label routes exist and exclude oral; detected = N4 flagged."""
    valid = [r for r in records if r["label_status"] == "ok"
             and r["approved_routes"] and "oral" not in r["approved_routes"]]
    det = [r for r in valid
           if r["profile"]["dimensions"]["route_feasibility"] == "FLAGGED"]
    lo, _ = _wilson(len(det), len(valid))
    return {"n": len(records), "valid": len(valid), "detected": len(det),
            "missed": [r["drug_name"] for r in valid if r not in det],
            "detection_wilson95_lower": lo,
            "pass": bool(valid) and len(det) / len(valid) >= 0.8}


def _pooled_with_v2(nc1: dict, nc2: dict) -> dict | None:
    """Pool expansion + frozen v2 controls when (and only when) both were
    scored by the same rule fingerprint."""
    if not V2_RESULTS_PATH.exists():
        print("[run] v2 results not found; skipping pooled stats", flush=True)
        return None
    v2 = json.loads(V2_RESULTS_PATH.read_text())
    if v2.get("rule_fingerprint") != RULE_FINGERPRINT:
        print("[run] v2 rule fingerprint differs; pooling REFUSED",
              flush=True)
        return None
    pooled: dict = {"rule_fingerprint": RULE_FINGERPRINT}
    for tag, exp in (("nc1", nc1), ("nc2", nc2)):
        v2s = v2["scores"][tag]
        k = v2s["detected"] + exp["detected"]
        n = v2s["valid"] + exp["valid"]
        lo, _ = _wilson(k, n)
        pooled[tag] = {
            "valid": n, "detected": k,
            "detection_rate": k / n if n else None,
            "detection_wilson95_lower": lo,
            "components": {
                "v2": {"valid": v2s["valid"], "detected": v2s["detected"]},
                "expansion": {"valid": exp["valid"],
                              "detected": exp["detected"]},
            },
        }
    return pooled


def main() -> None:
    if RESULTS_PATH.exists():
        raise SystemExit(
            "[run] REFUSED: scored expansion results exist. Frozen studies "
            "are amended, never regenerated.")
    _health_gate()

    caseset = json.loads(CASES_PATH.read_text())
    cohorts = caseset["cohorts"]
    all_drugs = sorted({
        c["drug_name"] for coh in cohorts.values() for c in coh["cases"]})

    done = _load_checkpoint()
    print(f"[run] resuming from {len(done)} checkpointed cases", flush=True)

    with holdout.holdout_active(all_drugs):
        nc1 = _run_cohort("nc1", cohorts["nc1_modality_contradiction"], done)
        nc2 = _run_cohort("nc2", cohorts["nc2_route_contradiction"], done)

    nc1_stats = _nc1_stats(nc1)
    nc2_stats = _nc2_stats(nc2)
    payload = {
        "contract": RESULTS_CONTRACT,
        "cases_sha256": _cases_sha256(),
        "rule_fingerprint": RULE_FINGERPRINT,
        "scores": {
            "nc1": nc1_stats,
            "nc2": nc2_stats,
            "pooled_with_v2": _pooled_with_v2(nc1_stats, nc2_stats),
        },
        "cases": {"nc1": nc1, "nc2": nc2},
    }
    payload["results_sha256"] = _sha256_bytes(
        json.dumps(payload, sort_keys=True).encode())
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True)
                            + "\n")
    print(f"[run] wrote {RESULTS_PATH}", flush=True)
    print(json.dumps(payload["scores"], indent=2), flush=True)


if __name__ == "__main__":
    main()
