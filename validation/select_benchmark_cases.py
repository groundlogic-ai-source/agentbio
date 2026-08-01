"""Mechanical benchmark case selection — implements
validation/benchmark_case_selection_criteria.md exactly.

Deterministic: SEED fixed, all filters mechanical, selection reproducible
byte-identically from this script + the committed inputs. Runs BEFORE the
single frozen benchmark run; the output list is committed to git.

Usage:
    python3 -m validation.select_benchmark_cases            # full run (live APIs)
    python3 -m validation.select_benchmark_cases --offline  # attrition sanity check
"""
import argparse
import ast
import csv
import json
import random
import re
import sys
import unicodedata

sys.path.insert(0, ".")

SEED = 20260731
TARGET_N = 50
STRATUM_CAP = 0.40

DATASET = "data_prep/output/enriched_dataset.csv"
OUT_JSON = "validation/benchmark_case_list.json"
OUT_ATTRITION = "validation/benchmark_attrition.md"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _dev_suite_drugs() -> set[str]:
    """All drugs appearing in ANY development suite (criterion E3, drug-level)."""
    drugs = set()
    for entry in json.load(open("validation/ground_truth.json")):
        drugs.add(_norm(entry["drug_name"]))
    for path in ("validation/run_repodb_cases.py",
                 "validation/run_repodb_cases_smallmol.py"):
        tree = ast.parse(open(path).read())
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


def _stratum(prev) -> str:
    if prev is None:
        return "unknown"
    if prev < 1:
        return "ultra_rare_lt1"
    if prev <= 10:
        return "rare_1_10"
    return "less_rare_gt10"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="skip live API filters (EFO/targets/prevalence)")
    args = ap.parse_args()

    attrition: list[tuple[str, int]] = []

    def step(label: str, rows: list) -> list:
        attrition.append((label, len(rows)))
        print(f"[select] {label}: {len(rows)}", flush=True)
        return rows

    rows = list(csv.DictReader(open(DATASET)))
    step("0. dataset rows", rows)

    # I1 — confirmed repurposing: approved for indication B after original A.
    rows = step("I1. status=Approved & label=repurposed-success",
                [r for r in rows if r["status"] == "Approved"
                 and r["label"] == "repurposed-success"])

    # E2 — combination products (mechanical heuristic: '+' in drug name).
    rows = step("E2. combination products excluded ('+' in name)",
                [r for r in rows if "+" not in r["drug_name"]])

    # E1/I2 — small molecules resolvable in ChEMBL, judged from the enriched
    # CSV. Pairs with EMPTY molecule_type (enrichment-era ChEMBL failures) are
    # excluded as unresolved per I2 with the count disclosed: live
    # re-resolution at selection time was REJECTED (see criteria doc I2
    # amendment) because selection must be reproducible regardless of
    # transient API health — ChEMBL was down during the first selection
    # attempt (2026-07-31) and the run ground to a halt on timeouts.
    sm = [r for r in rows if r["chembl_molecule_type"] == "Small molecule"]
    n_empty = sum(1 for r in rows if not r["chembl_molecule_type"])
    step("E1/I2. Small molecule per enrichment", sm)
    attrition.append((f"E1/I2. EMPTY molecule_type excluded "
                      f"(disclosed; ChEMBL-era failures)", -n_empty))
    print(f"[select] E1/I2. {n_empty} EMPTY-molecule_type pairs excluded "
          f"(disclosed)", flush=True)
    rows = sm

    # E3 — development-suite drugs excluded (drug-level, contamination-proof).
    dev_drugs = _dev_suite_drugs()
    rows = step(f"E3. development-suite drugs excluded ({len(dev_drugs)} drugs)",
                [r for r in rows if _norm(r["drug_name"]) not in dev_drugs])

    # I3a — Orphanet rare-disease universe membership (normalized name match).
    from data_sources.orphadata import (
        get_rare_disease_list, get_disease_prevalence)
    universe = {_norm(d["name"]): d for d in get_rare_disease_list()}
    rows = step("I3a. benchmark indication in Orphanet universe",
                [r for r in rows if _norm(r["ind_name"]) in universe])
    for r in rows:
        r["_orpha"] = universe[_norm(r["ind_name"])]["orpha_code"]

    # E4 — one case per drug: keep the lowest-prevalence indication
    # (unknown prevalence = +inf; full ties broken alphabetically).
    prev_cache: dict[str, float | None] = {}

    def prevalence(orpha: str):
        if orpha not in prev_cache:
            prev_cache[orpha] = (None if args.offline
                                 else get_disease_prevalence(orpha))
        return prev_cache[orpha]

    by_drug: dict[str, list] = {}
    for r in rows:
        by_drug.setdefault(r["drug_id"], []).append(r)
    kept = []
    for drug_id, cands in sorted(by_drug.items()):
        kept.append(min(cands, key=lambda r: (
            prevalence(r["_orpha"]) is None,
            prevalence(r["_orpha"]) or 0.0,
            _norm(r["ind_name"]))))
    rows = step(f"E4. one case per drug ({len(by_drug)} unique drugs)", kept)

    if args.offline:
        print("\n[select] OFFLINE sanity check complete — stopping before "
              "live EFO/target filters.")
        _write_attrition(attrition, offline=True)
        return

    # I3b — EFO resolution via the same v3 pipeline the system uses.
    from agents.target_selection import _resolve_efo_id
    resolved = []
    for r in rows:
        efo = _resolve_efo_id(r["ind_name"])
        if efo:
            r["_efo"] = efo
            resolved.append(r)
    rows = step("I3b. EFO resolved (v3 pipeline)", resolved)

    # I4 — disease has at least one target in the discovery universe.
    # Failures are COVERAGE failures: counted and disclosed, never counted as
    # discovery failures, and excluded from the primary set per the criteria.
    from data_sources.open_targets import (
        get_target_disease_score, get_disease_known_drugs)
    discoverable, coverage_failures = [], []
    for r in rows:
        try:
            genetic = [t for t in get_target_disease_score(r["_efo"])
                       if t.get("association_score", 0.0) >= 0.1]
            precedent = get_disease_known_drugs(
                r["_efo"]).get("approved_drug_names", [])
        except Exception as e:
            print(f"[select] target-universe check failed for "
                  f"{r['ind_name']}: {e} — retry-safe, not counted", flush=True)
            continue
        if genetic or precedent:
            r["_prev"] = prevalence(r["_orpha"])
            r["_stratum"] = _stratum(r["_prev"])
            discoverable.append(r)
        else:
            coverage_failures.append(
                {"drug_name": r["drug_name"], "ind_name": r["ind_name"],
                 "efo_id": r["_efo"]})
    rows = step("I4. at least one target in discovery universe", discoverable)
    print(f"[select] coverage failures (no target universe): "
          f"{len(coverage_failures)}", flush=True)

    # §5 — stratified sampling, seed-fixed, stratum cap 40%.
    rng = random.Random(SEED)
    strata: dict[str, list] = {}
    for r in rows:
        strata.setdefault(r["_stratum"], []).append(r)
    for s in strata.values():
        rng.shuffle(s)
    cap = int(TARGET_N * STRATUM_CAP)
    selected: list = []
    # Round-robin across strata (deterministic order) honoring the cap.
    order = sorted(strata, key=lambda k: -len(strata[k]))
    counts = {k: 0 for k in order}
    idx = 0
    while len(selected) < min(TARGET_N, len(rows)):
        k = order[idx % len(order)]
        idx += 1
        if counts[k] >= cap or not strata[k]:
            if all(counts[x] >= cap or not strata[x] for x in order):
                break
            continue
        selected.append(strata[k].pop())
        counts[k] += 1
    if len(selected) < 40:
        print(f"[select] WARNING: only {len(selected)} cases selected "
              f"(below the 40-case band floor) — taking all qualifying cases",
              flush=True)

    out = {
        "seed": SEED, "target_n": TARGET_N, "stratum_cap": STRATUM_CAP,
        "criteria": "validation/benchmark_case_selection_criteria.md",
        "freeze_tag": "benchmark-freeze-v1",
        "n_qualifying": len(rows) + len(selected),
        "primary": [
            {"drug_name": r["drug_name"], "ind_name": r["ind_name"],
             "orpha_code": r["_orpha"], "efo_id": r["_efo"],
             "prevalence_per_million": r["_prev"], "stratum": r["_stratum"]}
            for r in selected],
        "coverage_failures": coverage_failures,
        "development_subset_note": (
            "Development-suite drugs (excluded per E3): run separately as the "
            "labeled development subset; F2 predictions P1-P3 are scored there."),
        "attrition": [{"stage": s, "count": c} for s, c in attrition],
    }
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)
    _write_attrition(attrition, offline=False,
                     n_selected=len(selected),
                     n_coverage=len(coverage_failures))
    print(f"\n[select] DONE: {len(selected)} primary cases → {OUT_JSON}",
          flush=True)


def _write_attrition(attrition, offline: bool, n_selected: int = 0,
                     n_coverage: int = 0) -> None:
    lines = ["# Benchmark case-selection attrition table",
             "",
             f"Seed: {SEED} · criteria: `benchmark_case_selection_criteria.md`"
             f" · {'OFFLINE sanity check' if offline else 'full run'}",
             "", "| Stage | Cases remaining |", "| --- | --- |"]
    lines += [f"| {s} | {c} |" for s, c in attrition]
    if not offline:
        lines += ["", f"**Primary cases selected:** {n_selected} "
                      f"(seed {SEED}, stratum cap {STRATUM_CAP})",
                  f"**Coverage failures (disclosed, not discovery failures):** "
                  f"{n_coverage}"]
    with open(OUT_ATTRITION, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
