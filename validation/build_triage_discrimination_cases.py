"""Build the triage-discrimination case set — OFFLINE and deterministic.

This builder runs BEFORE any scored case is executed. It consumes only the
already-enriched repoDB snapshot (`data_prep/output/enriched_dataset.csv`) and
the development-suite drug list; it makes no live API calls. Determinism is
the freeze guarantee: same inputs -> byte-identical case set.

Cohorts (unit of analysis in parentheses — see preregistration):

* **cohort_a** (distinct drug): confirmed repurposings, i.e. repoDB rows with
  status=Approved, one row per distinct drug. Development-suite drugs are
  excluded at DRUG level (benchmark criterion E3). n target = 200. Run
  claim-free, mimicking the product default for a bare triage list.
* **nc1** (control): biologics per ChEMBL molecule_type, run with a planted
  false claim `claimed_modality="small molecule"`. Detection = N2 flagged.
* **nc2** (control): drugs ChEMBL reports as non-oral, run with a planted
  false claim `claimed_route="oral"`. Detection = N4 flagged.

The enriched CSV columns (molecule_type, oral, xlogp, max_phase) are used ONLY
for cohort selection and stratified reporting — never as scored evidence.
Scored evidence comes live from the redacted audit lanes at run time.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import random
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data_prep" / "output" / "enriched_dataset.csv"
GROUND_TRUTH = ROOT / "validation" / "ground_truth.json"
OUT_PATH = ROOT / "validation" / "triage_discrimination_cases_v2.json"
SCORED_RESULTS = ROOT / "validation" / "triage_discrimination_results_v2.json"

CASESET_CONTRACT = "triage-discrimination-cases-v2"
SEED = 20260811
N_COHORT_A = 200
N_NC1 = 15
N_NC2 = 15

_BIOLOGIC_TYPES = {
    "antibody", "protein", "enzyme", "oligosaccharide", "oligonucleotide",
    "antibody drug conjugate", "vaccine", "cell",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _dev_suite_drugs() -> set[str]:
    """All drugs appearing in ANY development suite (criterion E3).

    Mirrors validation/select_benchmark_cases.py::_dev_suite_drugs so the two
    studies exclude the identical drug set; kept self-contained so this
    builder never imports a module with argparse/main side effects.
    """
    drugs: set[str] = set()
    for entry in json.load(open(GROUND_TRUTH)):
        drugs.add(_norm(entry["drug_name"]))
    for path in ("validation/run_repodb_cases.py",
                 "validation/run_repodb_cases_smallmol.py"):
        tree = ast.parse(open(ROOT / path).read())
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", "") == "TARGET_CASES" for t in node.targets):
                value = node.value
            elif (isinstance(node, ast.AnnAssign)
                  and getattr(node.target, "id", "") == "TARGET_CASES"):
                value = node.value
            if value is not None:
                for tup in ast.literal_eval(value):
                    drugs.add(_norm(tup[1]))  # (num, drug, disease, ...)
    return drugs


def _qc_ok(drug_name: str) -> bool:
    """repoDB drug-name identity is dirty; quarantine unresolvable-looking
    names before any drug-keyed work (project convention)."""
    n = _norm(drug_name)
    if len(n) < 3:
        return False
    if not n[0].isalpha():
        return False
    if not any(c.isalpha() for c in n):
        return False
    return True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.strip()


def build() -> dict:
    # Confirmed REPURPOSINGS only (Amendment 2): v1 filtered on
    # status==Approved alone, which admitted ~10% original-approval rows --
    # drugs approved for their first indication are not repurposings. The
    # benchmark selection criterion (E-rules) uses the dataset's own label.
    rows = [r for r in csv.DictReader(open(CSV_PATH))
            if r["status"].strip() == "Approved"
            and r["label"].strip() == "repurposed-success"]

    # One record per distinct drug, deterministic pair choice.
    by_drug: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (_norm(r["drug_name"]),
                                         _norm(r["ind_name"]))):
        by_drug.setdefault(_norm(r["drug_name"]), r)

    dev = _dev_suite_drugs()
    eligible: dict[str, dict] = {}
    n_dev_excluded = n_qc_excluded = 0
    for key, r in by_drug.items():
        if key in dev:
            n_dev_excluded += 1
            continue
        if not _qc_ok(r["drug_name"]):
            n_qc_excluded += 1
            continue
        eligible[key] = r

    rng = random.Random(SEED)
    keys = sorted(eligible)

    def pick(pool: list[str], n: int, exclude: set[str]) -> list[str]:
        candidates = [k for k in pool if k not in exclude]
        return sorted(rng.sample(candidates, min(n, len(candidates))))

    cohort_a = pick(keys, N_COHORT_A, set())
    used = set(cohort_a)

    bio_pool = [k for k in keys
                if (eligible[k]["chembl_molecule_type"].strip().lower()
                    in _BIOLOGIC_TYPES)]
    nc1 = pick(bio_pool, N_NC1, used)
    used |= set(nc1)

    nonoral_pool = [k for k in keys
                    if eligible[k]["chembl_oral"].strip() == "0.0"]
    nc2 = pick(nonoral_pool, N_NC2, used)

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
        "built_from_commit": _code_commit(),
        "cohorts": {
            "cohort_a": {
                "unit": "distinct_drug",
                "claim_mode": "claim_free",
                "n_target": N_COHORT_A,
                "cases": [entry(k) for k in cohort_a],
            },
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
            "qc_quarantined_names": n_qc_excluded,
            "eligible_distinct_drugs": len(eligible),
        },
    }
    return caseset


def main() -> None:
    if SCORED_RESULTS.exists():
        raise SystemExit(
            "[cases] REFUSED: scored results exist for this case-set "
            "generation. Frozen artifacts are amended, never rebuilt.")
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
