"""Build the EXPANDED negative-control case set — OFFLINE and deterministic.

Companion to build_triage_discrimination_cases.py (the frozen v2 set). The
v2 controls (15+15) passed, but at n≈15 the Wilson lower bound on the
detection rate is ~0.74 — too weak to reassure a reviewer. This expansion
draws up to 60+60 fresh controls from the same enriched repoDB snapshot
under identical selection rules, EXCLUDING every drug already used anywhere
in the v2 case set, so the two generations pool into one larger independent
sample (pooling is valid only while the rule fingerprint matches; the
runner asserts that before pooling).

Discipline: same offline determinism as v2 (same seed, same enriched CSV,
same dev-suite/QC exclusions). Never rebuilds: once scored expansion
results exist, this builder refuses — frozen artifacts are amended, never
regenerated.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation.build_triage_discrimination_cases import (  # noqa: E402
    _BIOLOGIC_TYPES, _code_commit, _dev_suite_drugs, _norm, _qc_ok, _sha256,
    CSV_PATH, SEED)

V2_CASES = ROOT / "validation" / "triage_discrimination_cases_v2.json"
OUT_PATH = ROOT / "validation" / "triage_discrimination_nc_expansion_cases.json"
SCORED_RESULTS = (ROOT / "validation"
                  / "triage_discrimination_nc_expansion_results.json")

CASESET_CONTRACT = "triage-discrimination-nc-expansion-cases-v1"
N_NC1 = 60
N_NC2 = 60


def _v2_drugs() -> set[str]:
    """Every drug used anywhere in the frozen v2 case set (normalized)."""
    caseset = json.loads(V2_CASES.read_text())
    return {_norm(c["drug_name"])
            for coh in caseset["cohorts"].values() for c in coh["cases"]}


def build() -> dict:
    rows = [r for r in csv.DictReader(open(CSV_PATH))
            if r["status"].strip() == "Approved"
            and r["label"].strip() == "repurposed-success"]

    by_drug: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (_norm(r["drug_name"]),
                                         _norm(r["ind_name"]))):
        by_drug.setdefault(_norm(r["drug_name"]), r)

    dev = _dev_suite_drugs()
    v2 = _v2_drugs()
    eligible: dict[str, dict] = {}
    n_dev_excluded = n_qc_excluded = n_v2_excluded = 0
    for key, r in by_drug.items():
        if key in dev:
            n_dev_excluded += 1
            continue
        if key in v2:
            n_v2_excluded += 1
            continue
        if not _qc_ok(r["drug_name"]):
            n_qc_excluded += 1
            continue
        eligible[key] = r

    rng = random.Random(SEED)

    def pick(pool: list[str], n: int, exclude: set[str]) -> list[str]:
        candidates = sorted(k for k in pool if k not in exclude)
        return sorted(rng.sample(candidates, min(n, len(candidates))))

    bio_pool = [k for k in eligible
                if (eligible[k]["chembl_molecule_type"].strip().lower()
                    in _BIOLOGIC_TYPES)]
    nc1 = pick(bio_pool, N_NC1, set())

    nonoral_pool = [k for k in eligible
                    if eligible[k]["chembl_oral"].strip() == "0.0"]
    nc2 = pick(nonoral_pool, N_NC2, set(nc1))

    def entry(key: str) -> dict:
        r = eligible[key]
        return {
            "drug_name": r["drug_name"].strip(),
            "ind_name": r["ind_name"].strip(),
            "ind_id": r["ind_id"].strip(),
            # selection/stratification metadata only — never scored evidence
            "sel_molecule_type": r["chembl_molecule_type"].strip(),
            "sel_oral": r["chembl_oral"].strip(),
            "sel_xlogp": r["pubchem_xlogp"].strip(),
            "sel_max_phase": r["chembl_max_phase"].strip(),
        }

    caseset = {
        "contract": CASESET_CONTRACT,
        "seed": SEED,
        "source_csv": str(CSV_PATH.relative_to(ROOT)),
        "source_csv_sha256": _sha256(CSV_PATH),
        "v2_cases_sha256": _sha256(V2_CASES),
        "built_from_commit": _code_commit(),
        "cohorts": {
            "nc1_modality_contradiction": {
                "unit": "control",
                "claim_mode": "planted_false_claim",
                "planted_claim": {"claimed_modality": "small molecule"},
                "expect_finding": {"code": "N2", "status": "flagged"},
                "n_target": N_NC1,
                "cases": [entry(k) for k in nc1],
            },
            "nc2_route_contradiction": {
                "unit": "control",
                "claim_mode": "planted_false_claim",
                "planted_claim": {"claimed_route": "oral"},
                "expect_finding": {"code": "N4", "status": "flagged"},
                "n_target": N_NC2,
                "cases": [entry(k) for k in nc2],
            },
        },
        "exclusions": {
            "dev_suite_drugs_excluded": n_dev_excluded,
            "v2_caseset_drugs_excluded": n_v2_excluded,
            "qc_quarantined_names": n_qc_excluded,
            "eligible_distinct_drugs": len(eligible),
            "nc1_pool": len(bio_pool),
            "nc2_pool": len(nonoral_pool),
        },
    }
    return caseset


def main() -> None:
    if SCORED_RESULTS.exists():
        raise SystemExit(
            "[cases] REFUSED: scored expansion results exist. Frozen "
            "artifacts are amended, never rebuilt.")
    caseset = build()
    OUT_PATH.write_text(json.dumps(caseset, indent=2, sort_keys=True) + "\n")
    print(f"[cases] wrote {OUT_PATH}")
    print(f"[cases] contract={CASESET_CONTRACT} seed={SEED}")
    for name, coh in caseset["cohorts"].items():
        print(f"[cases]   {name}: {len(coh['cases'])}/{coh['n_target']}")
    print(f"[cases] exclusions: {json.dumps(caseset['exclusions'])}")
    print(f"[cases] sha256={_sha256(OUT_PATH)}")
    if "--verify" in sys.argv:
        again = build()
        assert json.dumps(again, sort_keys=True) == json.dumps(
            caseset, sort_keys=True), "NON-DETERMINISTIC BUILD"
        print("[cases] determinism check: PASS")


if __name__ == "__main__":
    main()
