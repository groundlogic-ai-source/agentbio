"""The single pre-registered benchmark run — executes
validation/benchmark_case_list.json under the frozen pipeline.

Protocol enforcement (validation/benchmark_case_selection_criteria.md §8):
  - REFUSES TO START if the pipeline code (agents/, data_sources/, cache/)
    differs from the benchmark-freeze-v1 tag.
  - REFUSES TO START / HALTS MID-RUN when ChEMBL is unhealthy — cases must
    never be competed against a degraded API. A halt preserves all completed
    cases; restarting the script RESUMES the same single run (completed cases
    are never re-executed, whatever their outcome).
  - Every case runs under benchmark holdout redaction (the true drug's
    disease-side evidence is sealed), exactly as the development harness did.

Outputs: validation/benchmark_results.json (incremental) and
validation/benchmark_results.md (full table + enrichment-vs-chance stats).

Usage: python3 -m validation.run_benchmark
"""
import ast
import json
import os
import subprocess
import sys
import time
from typing import Any

import requests

sys.path.insert(0, ".")

from validation.run_repodb_cases import (  # noqa: E402
    _run_inline_pipeline, _norm_name, _holdout_fp, _log,
)
from validation import miss_classifier  # noqa: E402
from data_sources import holdout as holdout_mod  # noqa: E402
from data_sources.chembl import get_target_candidate_compounds  # noqa: E402

CASE_LIST = "validation/benchmark_case_list.json"
SCREENED_LIST = "validation/benchmark_case_list_v2.json"
SCREEN_VERSION = "v2-screen-1"
RESULTS_JSON = "validation/benchmark_results_v2.json"
RESULTS_MD = "validation/benchmark_results_v2.md"
FREEZE_TAG = "benchmark-freeze-v2"
# Probe every molecule route the benchmark relies on.  The broad list route can
# remain healthy while the filtered metadata requests used for holdout redaction
# and salt/parent normalization return 500s (2026-08-05).  A run must not begin
# in that partially degraded state.
CHEMBL_PROBES = (
    ("https://www.ebi.ac.uk/chembl/api/data/molecule.json"
     "?pref_name__iexact=SILDENAFIL&limit=1"),
    ("https://www.ebi.ac.uk/chembl/api/data/molecule.json"
     "?parent_chembl_id=CHEMBL192&limit=1"),
    "https://www.ebi.ac.uk/chembl/api/data/molecule/CHEMBL192.json",
)


# ── Protocol gates ───────────────────────────────────────────────────────────

def _check_freeze_integrity() -> None:
    """The one v2 run executes AT the tagged commit, with the pipeline dirs
    byte-identical to the tag."""
    head = subprocess.run(["git", "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    tagged = subprocess.run(["git", "rev-parse", f"{FREEZE_TAG}^{{commit}}"],
                            capture_output=True, text=True).stdout.strip()
    if not tagged or head != tagged:
        _log(f"FATAL: HEAD ({head[:8] or '?'}) != {FREEZE_TAG} "
             f"({tagged[:8] or 'missing'}) — the frozen run must execute at "
             "the tagged commit; amend + re-tag deliberately, never drift.")
        sys.exit(2)
    diff = subprocess.run(
        ["git", "diff", "--name-only", FREEZE_TAG, "--",
         "agents/", "data_sources/", "cache/"],
        capture_output=True, text=True, cwd=".")
    dirty = [f for f in diff.stdout.splitlines()
             if not f.endswith(("select_benchmark_cases.py",))]
    if diff.returncode != 0 or dirty:
        _log(f"FATAL: pipeline differs from {FREEZE_TAG}: {dirty}")
        sys.exit(2)


def _chembl_healthy() -> bool:
    try:
        for url in CHEMBL_PROBES:
            r = requests.get(url, timeout=15)
            payload = r.json()
            if r.status_code != 200:
                return False
            if isinstance(payload, dict) and "molecules" in payload:
                if not payload["molecules"]:
                    return False
            elif not isinstance(payload, dict) or not payload.get("molecule_chembl_id"):
                return False
        return True
    except Exception:
        return False


def _health_gate(attempts: int, wait_s: int, label: str) -> None:
    for i in range(attempts):
        if _chembl_healthy():
            return
        _log(f"[health] ChEMBL probe failed ({i + 1}/{attempts}) — {label}")
        time.sleep(wait_s)
    _log("FATAL: ChEMBL unhealthy — refusing to compete cases against a "
         "degraded API. Completed cases are preserved; re-run resumes.")
    sys.exit(4)


# ── Case list ────────────────────────────────────────────────────────────────

def _development_cases() -> list[dict[str, str]]:
    cases = [{"drug": e["drug_name"], "disease": e["disease_name"]}
             for e in json.load(open("validation/ground_truth.json"))]
    tree = ast.parse(open("validation/run_repodb_cases.py").read())
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
                cases.append({"drug": tup[1], "disease": tup[2]})
    return cases


def _all_cases() -> list[dict[str, str]]:
    if not os.path.exists(SCREENED_LIST):
        _log(f"FATAL: {SCREENED_LIST} missing — run "
             "`python3 -m validation.run_v2_preflight` first (it generates the "
             "Amendment-1 screened list before the freeze tag).")
        sys.exit(2)
    screened = json.load(open(SCREENED_LIST))
    if screened.get("screen_version") != SCREEN_VERSION:
        _log(f"FATAL: screened list version "
             f"{screened.get('screen_version')!r} != {SCREEN_VERSION!r} — "
             "regenerate via run_v2_preflight.")
        sys.exit(2)
    primary = [{"drug": c["drug_name"], "disease": c["ind_name"],
                "subset": "primary", "stratum": c["stratum"]}
               for c in screened["primary"]]
    dev = [{**c, "subset": "development", "stratum": "development"}
           for c in _development_cases()]
    return primary + dev


# ── Persistence (fingerprinted resume — continuation of the ONE run) ─────────

def _key(drug: str, disease: str) -> tuple:
    return (_norm_name(drug), _norm_name(disease), _holdout_fp([drug]))


def _load_done() -> dict:
    if not os.path.exists(RESULTS_JSON):
        return {}
    with open(RESULTS_JSON) as f:
        payload = json.load(f)
    if payload.get("freeze_tag") != FREEZE_TAG:
        _log(f"FATAL: {RESULTS_JSON} belongs to "
             f"{payload.get('freeze_tag')!r}, not {FREEZE_TAG!r} — refusing to "
             "resume/merge across freezes (protocol: v1 and v2 never merge).")
        sys.exit(2)
    return {_key(c["drug_name"], c["disease_name"]): c
            for c in payload.get("cases", [])
            if c.get("holdout_drugs")}


def _flush(cases: list[dict[str, Any]]) -> None:
    with open(RESULTS_JSON, "w") as f:
        json.dump({"freeze_tag": FREEZE_TAG, "case_list": CASE_LIST,
                   "screened_list": SCREENED_LIST,
                   "cases": cases}, f, indent=2)


# ── Chance-rate machinery (mechanical, fixed a priori in the criteria) ───────

def _attach_pool_sizes(case: dict[str, Any]) -> None:
    """Per-target eligible-pool size = molecules in the exact pool the chemist
    drew from (same call, same default filter). Cached from the run, so cheap.
    Measurement only — does not affect any pipeline behavior."""
    total_pool, per = 0, {}
    for t in case.get("per_target_results", []):
        uid = t.get("uniprot_id")
        if not uid:
            continue
        try:
            n = len(get_target_candidate_compounds(
                uid, repurposing_only=False).get("compounds", []))
        except Exception:
            n = 0
        per[t["target_symbol"]] = n
        total_pool += n
    case["pool_sizes"] = per
    # Chance that a SPECIFIC drug appears in the reviewed lists by chance:
    # 1 - Π_targets (1 - n_reviewed_t / pool_t); 0 when pools unknown.
    p_miss = 1.0
    for t in case.get("per_target_results", []):
        pool = per.get(t.get("target_symbol"), 0)
        if pool > 0:
            p_miss *= (1 - min(t.get("n_reviewed", 0) / pool, 1.0))
    case["chance_hit_probability"] = round(1 - p_miss, 6) if total_pool else None


def _poisson_binomial_p_ge(probs: list[float], k: int) -> float:
    """Exact P(X >= k) for independent Bernoulli trials via DP."""
    dp = [1.0]
    for p in probs:
        new = [0.0] * (len(dp) + 1)
        for i, v in enumerate(dp):
            new[i] += v * (1 - p)
            new[i + 1] += v * p
        dp = new
    return max(0.0, 1.0 - sum(dp[:k]))


def _stats_block(cases: list[dict[str, Any]]) -> str:
    lines = []
    for subset in ("primary", "development"):
        sc = [c for c in cases if c.get("subset") == subset
              and c.get("in_universe") and c.get("status") in ("hit", "miss")]
        if not sc:
            continue
        hits = sum(1 for c in sc if c.get("found"))
        probs = [c["chance_hit_probability"] for c in sc
                 if c.get("chance_hit_probability") is not None]
        expected = sum(probs)
        p_val = (_poisson_binomial_p_ge(probs, hits)
                 if len(probs) == len(sc) else None)
        lines += [
            f"### {subset} subset",
            f"- cases scored: {len(sc)} · rediscovered: **{hits}** "
            f"({hits / len(sc):.1%})",
            f"- expected by chance (mechanical): {expected:.2f} "
            f"({expected / len(sc):.1%})",
            f"- enrichment vs chance: "
            f"**{(hits / expected) if expected else float('nan'):.1f}×**",
            f"- Poisson-binomial P(X ≥ {hits}): "
            + (f"{p_val:.2e}" if p_val is not None else "n/a "
               "(pool sizes incomplete)"),
            "",
        ]
    return "\n".join(lines)


def _build_markdown(cases: list[dict[str, Any]]) -> str:
    lines = ["# Pre-registered benchmark — full results table", "",
             f"Freeze: `{FREEZE_TAG}` · case list: `benchmark-cases-v1` · "
             "single run (infrastructure halts resume; cases never re-run)", "",
             "| # | Subset | Drug | Disease | Status | Found | Rank | "
             "Hit target (rank) | Chance p | Miss reason |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, c in enumerate(cases, 1):
        hit_t = (f"{c.get('hit_target_symbol')} "
                 f"(#{c.get('hit_at_target_rank')})") if c.get("found") else "—"
        lines.append(
            f"| {i} | {c.get('subset', '?')} | {c['drug_name']} | "
            f"{c['disease_name']} | {c.get('status')} | "
            f"{'**YES**' if c.get('found') else 'no'} | "
            f"{c.get('rank') or '—'} | {hit_t} | "
            f"{c.get('chance_hit_probability') if c.get('chance_hit_probability') is not None else '—'} | "
            f"{(c.get('miss_reason') or '—')[:80]} |")
    lines += ["", "## Enrichment vs chance", "", _stats_block(cases)]
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if os.path.exists("validation/BENCHMARK_V1_TERMINATED") and FREEZE_TAG == "benchmark-freeze-v1":
        _log("v1 was terminated by protocol decision (see benchmark_v1_partial_report.md); refusing to run. rc=2.")
        sys.exit(2)
    _check_freeze_integrity()
    _health_gate(attempts=5, wait_s=30, label="startup gate")
    done = _load_done()
    _log(f"Benchmark: {len(done)} cases already completed (resume of the "
         f"single run; completed cases are never re-executed)")

    cases_ordered: list[dict[str, Any]] = []
    for spec in _all_cases():
        drug, disease = spec["drug"], spec["disease"]
        k = _key(drug, disease)
        if k in done:
            cases_ordered.append(done[k])
            continue
        _health_gate(attempts=3, wait_s=60, label="pre-case gate — halting")
        _log(f"=== [{spec['subset']}] {drug} / {disease} "
             f"[holdout: {drug}] ===")
        with holdout_mod.holdout_active([drug]):
            result = _run_inline_pipeline(drug, disease)
            unresolved = holdout_mod.unresolved()
        result.update(subset=spec["subset"], stratum=spec["stratum"],
                      benchmark_mode="holdout", holdout_drugs=[drug])
        if unresolved:
            result["holdout_unresolved"] = unresolved
        _attach_pool_sizes(result)
        cases_ordered.append(result)
        done[k] = result
        _flush(cases_ordered)

    cases_ordered = miss_classifier.classify_cases(cases_ordered)
    _flush(cases_ordered)
    with open(RESULTS_MD, "w") as f:
        f.write(_build_markdown(cases_ordered))
    in_uni = [c for c in cases_ordered
              if c.get("in_universe") and c.get("subset") == "primary"]
    hits = [c for c in in_uni if c.get("found")]
    _log(f"RESULT (primary): {len(hits)}/{len(in_uni)} rediscovered "
         f"→ {RESULTS_MD}")


if __name__ == "__main__":
    main()
