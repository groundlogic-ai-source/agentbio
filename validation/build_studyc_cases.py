"""Build the Study C case set — powered triage discrimination — OFFLINE and
deterministic.

Study C extends Study B's design (confirmed repurposings vs the pipeline's
own candidate pools) with enough diseases for statistical power and, for the
first time, NEGATIVES: drugs with a genuine-failure repoDB row for the same
disease. The discrimination question: does the pipeline's composite score
rank a disease's confirmed repurposings above its genuine failures?

Frame (greenlit 2026-08-12, reproduced by this builder):

* **Diseases (28).** A disease is in scope iff it has >=1 genuine-failure
  drug AND >=1 confirmed repurposed-success drug after exclusions. This
  anchors every disease to carry both a positive and a negative, so the
  per-disease ranking contrast is always defined.
* **Positives (140).** ALL distinct repurposed-success drugs per anchored
  disease — no per-disease cap: Study C's LLM cost scales with diseases
  (pool rebuilds), not with positives, so capping would only weaken power.
* **Negatives (45 rows / 37 drugs).** ALL distinct genuine-failure drugs per
  anchored disease. `administrative-exclude` rows are NEVER used as failures
  (known label artifact: broad framing mixes 1,290 administrative rows into
  "failure" — see the broad-framing memory note).

Exclusions (project conventions): development-suite drugs at DRUG level,
QC-quarantined names. Within a disease, a drug appearing as BOTH success
and genuine-failure is scored as negative only — conservative: the
discrimination metric must never be rewarded for ranking an ambiguous drug.

Never rebuilds: once scored Study C results exist, this builder refuses —
frozen artifacts are amended, never regenerated.
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from validation.build_triage_discrimination_cases import (  # noqa: E402
    _code_commit, _dev_suite_drugs, _norm, _qc_ok, _sha256, CSV_PATH)

OUT_PATH = ROOT / "validation" / "triage_discrimination_studyc_cases.json"
SCORED_RESULTS = (ROOT / "validation"
                  / "triage_discrimination_studyc_results.json")

CASESET_CONTRACT = "triage-discrimination-studyc-cases-v1"


def _display_names(rows: list[dict], key: str) -> dict[str, str]:
    """Deterministic display form per normalized key: the sorted-first raw
    spelling (repoDB capitalization is inconsistent across rows)."""
    out: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r[key].strip()):
        out.setdefault(_norm(r[key]), r[key].strip())
    return out


def build() -> dict:
    rows = list(csv.DictReader(open(CSV_PATH)))
    dev = _dev_suite_drugs()
    drug_display = _display_names(rows, "drug_name")
    dis_display = _display_names(rows, "ind_name")

    def usable(r: dict) -> bool:
        return _norm(r["drug_name"]) not in dev and _qc_ok(r["drug_name"])

    succ: dict[str, set[str]] = collections.defaultdict(set)
    fail: dict[str, set[str]] = collections.defaultdict(set)
    ind_ids: dict[str, set[str]] = collections.defaultdict(set)
    for r in rows:
        if not usable(r):
            continue
        d = _norm(r["ind_name"])
        label = r["label"].strip()
        if label == "repurposed-success":
            succ[d].add(_norm(r["drug_name"]))
            ind_ids[d].add(r["ind_id"].strip())
        elif label == "genuine-failure":
            # administrative-exclude NEVER enters the negative pool.
            fail[d].add(_norm(r["drug_name"]))
            ind_ids[d].add(r["ind_id"].strip())

    diseases: list[dict] = []
    n_overlap_demoted = 0
    for d in sorted(set(succ) & set(fail)):
        negatives = sorted(fail[d])
        positives = sorted(succ[d] - fail[d])
        n_overlap_demoted += len(succ[d] & fail[d])
        if not positives or not negatives:
            continue
        diseases.append({
            "disease_name": dis_display[d],
            "ind_ids": sorted(ind_ids[d]),
            "positives": [drug_display[k] for k in positives],
            "negatives": [drug_display[k] for k in negatives],
        })

    caseset = {
        "contract": CASESET_CONTRACT,
        "source_csv": str(CSV_PATH.relative_to(ROOT)),
        "source_csv_sha256": _sha256(CSV_PATH),
        "built_from_commit": _code_commit(),
        "frame": {
            "anchor": ("disease has >=1 genuine-failure drug AND >=1 "
                       "repurposed-success drug after exclusions"),
            "positive_cap_per_disease": None,
            "overlap_rule": ("drug both success and failure for the same "
                             "disease is scored as negative only"),
        },
        "diseases": diseases,
        "exclusions": {
            "dev_suite_drugs_excluded": len(dev),
            "overlap_drugs_demoted_to_negative_only": n_overlap_demoted,
        },
        "totals": {
            "n_diseases": len(diseases),
            "n_positive_pairs": sum(len(d["positives"]) for d in diseases),
            "n_negative_pairs": sum(len(d["negatives"]) for d in diseases),
        },
    }
    return caseset


def main() -> None:
    if SCORED_RESULTS.exists():
        raise SystemExit(
            "[cases] REFUSED: scored Study C results exist. Frozen artifacts "
            "are amended, never rebuilt.")
    caseset = build()
    OUT_PATH.write_text(json.dumps(caseset, indent=2, sort_keys=True) + "\n")
    print(f"[cases] wrote {OUT_PATH}")
    print(f"[cases] contract={CASESET_CONTRACT}")
    print(f"[cases] totals: {json.dumps(caseset['totals'])}")
    print(f"[cases] exclusions: {json.dumps(caseset['exclusions'])}")
    print(f"[cases] sha256={_sha256(OUT_PATH)}")
    if "--verify" in sys.argv:
        again = build()
        assert json.dumps(again, sort_keys=True) == json.dumps(
            caseset, sort_keys=True), "NON-DETERMINISTIC BUILD"
        print("[cases] determinism check: PASS")


if __name__ == "__main__":
    main()
