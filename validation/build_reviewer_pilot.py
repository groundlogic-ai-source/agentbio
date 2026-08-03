"""Build a compact, reviewer-facing report from the completed small-molecule run.

This is deliberately a REPORT BUILDER, not another benchmark runner.  The
underlying cases were already executed through the disease-input pipeline with
the confirmed drug held out from disease-side discovery signals.  Keeping the
case selection here explicit makes it possible to show a small pilot without
quietly selecting cases after seeing their outcomes.

The headline set is nine one-drug cases with interpretable human small-molecule
scope.  Three additional cases are shown as stress controls but excluded from
the headline: pathogen-directed, phenotypic/cytotoxic, and circular
pharmacological-precedent cases.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Any


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(ROOT, "validation", "repodb_results_smallmol.json")
DEFAULT_OUTPUT = os.path.join(ROOT, "validation", "reviewer_pilot_report.md")

# One case per drug. These labels are chosen from the case definitions and
# scope properties, not from the observed rank or hit/miss outcome. This is a
# fixed reviewer-pilot subset, not a preregistered benchmark sample.
PILOT_CASES: dict[str, dict[str, str]] = {
    "Dapsone": {
        "stratum": "human-target scope boundary",
        "headline": "yes",
        "note": "Leprosy's confirmed mechanism is bacterial; the human rare-disease target universe cannot represent it.",
    },
    "Anagrelide": {
        "stratum": "right target / pool coverage",
        "headline": "yes",
        "note": "PDE3B was selected, but the strict ChEMBL activity pool has no qualifying assay for the drug.",
    },
    "Sapropterin": {
        "stratum": "right target / pool coverage",
        "headline": "yes",
        "note": "PAH was selected, but a cofactor/chaperone is not visible in the strict IC50/Ki pool.",
    },
    "Miglustat": {
        "stratum": "target-ranking stress",
        "headline": "yes",
        "note": "UGCG was present in the considered targets but ranked second; only the top target was pursued.",
    },
    "Riociguat": {
        "stratum": "target-ranking stress",
        "headline": "yes",
        "note": "Soluble guanylate cyclase targets were present at selection rank six; PDE5A was pursued first.",
    },
    "Ibrutinib": {
        "stratum": "positive control",
        "headline": "yes",
        "note": "BTK was selected and the drug was recovered at rank two.",
    },
    "Imatinib": {
        "stratum": "target-ranking stress",
        "headline": "yes",
        "note": "For chronic eosinophilic leukemia, the relevant target was present at rank six; only the top target was pursued.",
    },
    "Pyridostigmine": {
        "stratum": "right target / pool coverage",
        "headline": "yes",
        "note": "ACHE was selected, but the strict ChEMBL activity pool has no qualifying assay for the drug.",
    },
    "Lorazepam": {
        "stratum": "target-ranking stress",
        "headline": "yes",
        "note": "GABA-A targets were present at selection rank six; the top carbonic-anhydrase precedent target was pursued first.",
    },
    "Pentamidine": {
        "stratum": "stress control: pathogen",
        "headline": "no",
        "note": "African trypanosomiasis is a pathogen-directed mechanism outside the human-target headline contract.",
    },
    "Temozolomide": {
        "stratum": "stress control: phenotypic/cytotoxic",
        "headline": "no",
        "note": "DNA alkylation is not honestly represented by the current single human-protein target contract.",
    },
    "Everolimus": {
        "stratum": "stress control: circular precedent",
        "headline": "no",
        "note": "The selected FKBP1A target comes from a pharmacological-precedent lane that already knows the rapalog class; this is not an independent discovery case.",
    },
}


def _load_cases(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", [])
    by_drug: dict[str, dict[str, Any]] = {}
    for case in cases:
        drug = str(case.get("drug_name") or "").strip()
        if drug:
            by_drug[drug] = case
    missing = sorted(set(PILOT_CASES) - set(by_drug))
    if missing:
        raise ValueError(f"Input is missing pilot cases: {', '.join(missing)}")
    return by_drug


def _target(case: dict[str, Any]) -> str:
    return str((case.get("selected_target") or {}).get("target_symbol") or "—")


def _target_method(case: dict[str, Any]) -> str:
    return str(
        (case.get("selected_target") or {}).get("target_discovery_method") or "—"
    )


def _target_outcome(case: dict[str, Any]) -> str:
    if case.get("status") == "hit":
        return "top target matched"
    if case.get("target_was_right") is True:
        return "top target matched"
    if case.get("recoverable_by_target_fix"):
        rank = case.get("true_target_rank_in_considered")
        return f"true target in considered list (rank {rank or '—'})"
    if case.get("miss_class") == "right_target_pool_gap":
        return "top target matched; pool gap"
    return "true target not considered"


def _result_label(case: dict[str, Any]) -> str:
    if case.get("status") == "hit":
        rank = case.get("rank")
        return f"recovered at rank {rank}"
    miss = case.get("miss_class") or case.get("status") or "unknown"
    return miss.replace("_", " ")


def _pct(n: int, d: int) -> str:
    return f"{(100 * n / d):.1f}%" if d else "—"


def build_report(input_path: str = DEFAULT_INPUT) -> str:
    by_drug = _load_cases(input_path)
    headline_names = [
        drug for drug, metadata in PILOT_CASES.items() if metadata["headline"] == "yes"
    ]
    stress_names = [
        drug for drug, metadata in PILOT_CASES.items() if metadata["headline"] == "no"
    ]
    headline = [by_drug[drug] for drug in headline_names]
    stress = [by_drug[drug] for drug in stress_names]

    hits = sum(case.get("status") == "hit" for case in headline)
    top10 = sum(bool(case.get("recovered_top10")) for case in headline)
    top_target = sum(
        case.get("status") == "hit" or case.get("target_was_right") is True
        for case in headline
    )
    target_considered = sum(
        case.get("status") == "hit"
        or case.get("target_was_right") is True
        or bool(case.get("recoverable_by_target_fix"))
        for case in headline
    )
    pool_gaps = sum(case.get("miss_class") == "right_target_pool_gap" for case in headline)
    target_misses = sum(case.get("miss_class") == "wrong_target" for case in headline)
    errors = sum(case.get("status") == "error" for case in headline)

    generated_at = "unknown"
    with open(input_path, encoding="utf-8") as handle:
        generated_at = json.load(handle).get("generated_at", "unknown")

    lines: list[str] = [
        "# AgentBio reviewer pilot — compact retrospective rediscovery",
        "",
        f"_Source results generated: {generated_at}_",
        f"_Report built: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
        "## What this pilot tests",
        "",
        "This is a small, transparent reviewer pilot of the current discovery "
        "pipeline. It asks whether a confirmed drug appears in the final ranked "
        "candidate list when the disease-side discovery run is executed without "
        "using that drug's known indication as an input.",
        "",
        "It is **not** a prospective or historical benchmark. The data sources are "
        "present-day databases, and the bioactivity pool is intentionally not "
        "redacted: seeing a held-out drug in a correctly selected target's pool "
        "is the retrospective rediscovery event being measured. The result should "
        "be described as **rediscovery in present-day data under disease-side "
        "holdout**, not as proof that the system would have discovered the "
        "repurposing historically.",
        "",
        "## Headline result",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Cases in headline set | {len(headline)} |",
        f"| Recovered anywhere in final candidate list | {hits}/{len(headline)} ({_pct(hits, len(headline))}) |",
        f"| Recovered in Top-10 | {top10}/{len(headline)} ({_pct(top10, len(headline))}) |",
        f"| Correct mechanism target selected first | {top_target}/{len(headline)} ({_pct(top_target, len(headline))}) |",
        f"| Correct mechanism target considered anywhere | {target_considered}/{len(headline)} ({_pct(target_considered, len(headline))}) |",
        f"| Right-target but strict activity-pool gap | {pool_gaps}/{len(headline)} ({_pct(pool_gaps, len(headline))}) |",
        f"| Target-selection misses | {target_misses}/{len(headline)} ({_pct(target_misses, len(headline))}) |",
        f"| Pipeline errors | {errors}/{len(headline)} |",
        "",
        "The one recovered case is Ibrutinib / Waldenstrom macroglobulinemia "
        "(BTK, rank 2). The main limitation visible in this pilot is not one "
        "single ranking score: three cases had the right target but no qualifying "
        "strict ChEMBL activity record, while four had the correct target somewhere "
        "in the considered set but lost because only the top target was pursued.",
        "",
        "## Headline cases",
        "",
        "| Drug / disease | Selected target | Target outcome | Final result | Interpretation |",
        "|---|---|---|---|---|",
    ]

    for case in headline:
        drug = case["drug_name"]
        disease = case["disease_name"]
        lines.append(
            f"| **{drug}** / {disease} | {_target(case)} | "
            f"{_target_outcome(case)} | {_result_label(case)} | "
            f"{PILOT_CASES[drug]['note']} |"
        )

    lines += [
        "",
        "## Stratified readout",
        "",
        "| Stratum | Cases | Recovered | What it shows |",
        "|---|---:|---:|---|",
    ]
    strata: dict[str, list[dict[str, Any]]] = {}
    for case in headline:
        strata.setdefault(PILOT_CASES[case["drug_name"]]["stratum"], []).append(case)
    for stratum, cases in strata.items():
        recovered = sum(case.get("status") == "hit" for case in cases)
        if "positive" in stratum:
            meaning = "A positive control is recovered near the top."
        elif "pool" in stratum:
            meaning = "Target selection can be right while the strict evidence pool is blind."
        elif "ranking" in stratum:
            meaning = "Top-1 pursuit loses cases whose true target was already considered."
        else:
            meaning = "The current human-target contract does not cover this mechanism."
        lines.append(f"| {stratum} | {len(cases)} | {recovered} | {meaning} |")

    lines += [
        "",
        "## Stress controls excluded from the headline",
        "",
        "These cases remain useful for showing where the current system's contract "
        "ends, but combining them with the core small-molecule human-target cases "
        "would make the headline less interpretable.",
        "",
        "| Drug / disease | Selected target | Result | Why excluded |",
        "|---|---|---|---|",
    ]
    for case in stress:
        drug = case["drug_name"]
        lines.append(
            f"| **{drug}** / {case['disease_name']} | {_target(case)} "
            f"({ _target_method(case) }) | {_result_label(case)} | "
            f"{PILOT_CASES[drug]['note']} |"
        )

    lines += [
        "",
        "## Protocol and limitations",
        "",
        "- The source run contains 13 disease-drug cases; this report uses 12 "
        "unique-drug cases (9 headline cases and 3 stress controls), leaving out "
        "the duplicate Imatinib / Idiopathic Hypereosinophilic Syndrome pair. "
        "The subset is fixed in this script for reviewer readability; it was not "
        "preregistered before the underlying run.",
        "- The confirmed drug was used only for post-run matching and miss "
        "classification; it was held out from disease-side approved-drug and "
        "indication signals.",
        "- Drug matching uses the runner's InChIKey/ChEMBL-ID-first logic with a "
        "name fallback, and is performed after ranking.",
        "- The case list is a development/reviewer pilot, not the frozen v2 "
        "benchmark population. No chance-rate significance claim is made.",
        "- Everolimus is shown only as a circularity control because its selected "
        "precedent lane already knows the rapalog class.",
        "- The pilot therefore supports a narrow claim: the machine can recover "
        "at least one known repurposing pair near the top in the current data, "
        "while exposing target-coverage and evidence-coverage limits. It does "
        "not support a claim of broad autonomous discovery accuracy.",
        "",
        "## Reproduction",
        "",
        "The underlying completed run is `validation/repodb_results_smallmol.json`. "
        "This report is generated with:",
        "",
        "```bash",
        "python -m validation.build_reviewer_pilot",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.input)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(report)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()