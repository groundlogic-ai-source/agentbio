"""
Retrospective validation — 10 repoDB "Approved" cases from the enriched dataset.

Selection rule:
  - status == "Approved" in enriched_dataset.csv
  - disease is in the pipeline universe (Orphanet rare / WHO NTD)
  - exclude diseases already covered by ground_truth.json (IPAH, MM, TSC, PCOS, hemangioma)
  - pick first 10 unique diseases by ascending row index (confirmed in-universe)
  - for each disease, use the FIRST approved drug in the dataset (by row index)

The 10 cases below were pre-verified via _build_candidate_universe() against the
cached Orphanet list (11,645 diseases, 7-day TTL) + WHO NTDs.

Run: python -m validation.run_repodb_cases
Out: validation/repodb_results_topk.json  (incremental — safe to interrupt and resume)
     validation/repodb_results_topk.md
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.target_selection import select_for_disease, DiseaseNotInUniverse
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from agents.reviewer import run_reviewer, STRONG_MATCH_THRESHOLD
from data_sources.pubchem import get_compound_data
from data_sources import holdout as holdout_mod
from api import audit as _audit
from validation import miss_classifier
import api.jobs_db as jobs_db

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_JSON = os.path.join(VALIDATION_DIR, "repodb_results_topk.json")
RESULTS_MD   = os.path.join(VALIDATION_DIR, "repodb_results_topk.md")

# Top-N ranking threshold for the reviewed list
TOP_N = 10

# Top-K targets to try per disease (main tunable for this harness)
TOP_K = 3

# Pre-verified 10 in-universe cases from enriched_dataset.csv (Approved rows,
# ascending row-index order, excluding IPAH/MM/TSC/PCOS/hemangioma).
# Each tuple: (csv_row_idx, drug_name, disease_name_in_csv)
# Note: the pipeline resolves disease_name to its canonical form internally.
TARGET_CASES: list[tuple[int, str, str]] = [
    (8,  "Dornase alfa",            "Cystic Fibrosis"),
    (36, "Anakinra",                "Cryopyrin-Associated Periodic Syndromes"),
    (37, "Anakinra",                "Chronic Infantile Neurological, Cutaneous, and Articular Syndrome"),
    (46, "Desmopressin",            "Hemophilia A"),
    (47, "Desmopressin",            "von Willebrand Disease"),
    (50, "Coagulation factor VIIa Recombinant Human", "Hemophilia B"),
    (78, "Somatropin recombinant",  "Prader-Willi Syndrome"),
    (80, "Somatropin recombinant",  "Turner Syndrome"),
    (81, "Imiglucerase",            "Gaucher Disease"),
    (110,"Laronidase",              "Mucopolysaccharidosis I"),
]


def _log(msg: str) -> None:
    print(f"[repodb-val] {msg}", flush=True)


def _norm_name(s: Any) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _inchikey_block(ik: Optional[str]) -> Optional[str]:
    if not ik:
        return None
    return str(ik).split("-")[0]


def _resolve_confirmed_inchikey(drug_name: str) -> Optional[str]:
    try:
        pc = get_compound_data(drug_name)
        return pc.get("inchikey")
    except Exception as e:
        _log(f"  WARN could not resolve InChIKey for '{drug_name}': {e}")
        return None


def _name_match(a: str, b: str) -> bool:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _match_in_pipeline(
    drug_name: str,
    chemist_candidates: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
) -> tuple[Optional[int], Optional[dict[str, Any]], Optional[str]]:
    conf_ik = _inchikey_block(_resolve_confirmed_inchikey(drug_name))
    matched_ids: set[str] = set()
    for c in chemist_candidates:
        cik = _inchikey_block(c.get("inchikey"))
        ik_hit = bool(conf_ik and cik and conf_ik == cik)
        nm_hit = _name_match(drug_name, c.get("drug_name", ""))
        if ik_hit or nm_hit:
            mid = c.get("molecule_chembl_id")
            if mid:
                matched_ids.add(mid)
    for i, r in enumerate(reviewed, 1):
        id_hit = r.get("molecule_chembl_id") in matched_ids
        nm_hit = _name_match(drug_name, r.get("drug_name", ""))
        if id_hit or nm_hit:
            method = "inchikey/chembl_id" if id_hit else "name"
            return i, r, method
    return None, None, None


def _diagnose_miss_topk(
    drug_name: str,
    tried_targets: list[dict[str, Any]],
    total_chem: int,
) -> str:
    syms = ", ".join(
        f"{r.get('target_symbol')} ({r.get('uniprot_id')})"
        for r in tried_targets
    )
    return (
        f"'{drug_name}' absent across {total_chem} ChEMBL compound(s) drawn from "
        f"top-{len(tried_targets)} targets [{syms}]. "
        f"The Chemist admits only Homo sapiens IC50/Ki at assay confidence ≥ 8 — "
        f"the confirmed drug's primary target lies outside these {len(tried_targets)} "
        f"candidates, or it is a biologic/peptide not represented in the small-mol pool."
    )


def _run_one_target(
    target_row: dict[str, Any],
    drug_name: str,
) -> dict[str, Any]:
    """Run biologist→chemist→reviewer for a single target row; return a per-target record."""
    target = {
        "target_symbol": target_row["target_symbol"],
        "uniprot_id": target_row.get("uniprot_id"),
        "ensembl_id": target_row.get("ensembl_id"),
        "disease_name": target_row["disease_name"],
        "orpha_code": target_row.get("orpha_code"),
        "ot_association_score": target_row.get("ot_association_score", 0.0),
        "tractability_score": target_row.get("tractability_score"),
        "unmet_need_score": target_row.get("unmet_need_score"),
    }
    rec: dict[str, Any] = {
        "target_symbol": target["target_symbol"],
        "uniprot_id": target["uniprot_id"],
        "ot_association_score": target["ot_association_score"],
        "status": None,
        "n_chemist_candidates": 0,
        "n_reviewed": 0,
        "found": False,
        "rank": None,
        "composite_score": None,
        "strong_match": None,
        "match_method": None,
        "error": None,
    }
    try:
        bio      = run_biologist(target)
        chem     = run_chemist(bio)
        reviewed = run_reviewer(chem, bio)
    except Exception as e:
        rec.update(status="error", error=str(e))
        _log(f"    ERROR pipeline: {e}")
        return rec

    chem_cands = chem.get("candidates", [])
    rec["n_chemist_candidates"] = len(chem_cands)
    rec["n_reviewed"]           = len(reviewed)
    _log(f"    chemist candidates: {len(chem_cands)}; reviewed: {len(reviewed)}")

    rank, matched, method = _match_in_pipeline(drug_name, chem_cands, reviewed)
    if rank is not None:
        rec.update(
            status="hit",
            found=True,
            rank=rank,
            composite_score=matched.get("composite_score"),
            strong_match=bool(matched.get("strong_match")),
            match_method=method,
        )
        _log(f"    HIT: rank {rank}, composite={matched.get('composite_score')}, "
             f"strong_match={matched.get('strong_match')}")
    else:
        rec["status"] = "miss"
        _log(f"    MISS: {drug_name} not in reviewed list")

    return rec


def _run_inline_pipeline(
    drug_name: str, disease_name: str
) -> dict[str, Any]:
    """
    Drive the full biologist→chemist→reviewer pipeline for one case,
    trying the top-TOP_K OT targets in order. The case is a HIT if the
    confirmed drug is found in ANY of the K pools.
    """
    result: dict[str, Any] = {
        "drug_name": drug_name,
        "disease_name": disease_name,
        "top_k": TOP_K,
        "status": None,
        "in_universe": None,
        # top-1 target kept for backward-compat with existing markdown renderers
        "selected_target": None,
        "candidate_targets_considered": [],
        # aggregated across all K runs
        "n_chemist_candidates": None,
        "n_reviewed_candidates": None,
        # hit fields
        "found": False,
        "rank": None,
        "recovered_top10": False,
        "composite_score": None,
        "strong_match": None,
        "is_approved_drug": None,
        "match_method": None,
        # new: which target rank (1-K) recovered the drug
        "hit_at_target_rank": None,
        "hit_target_symbol": None,
        "hit_target_uniprot": None,
        # detailed per-target breakdown
        "per_target_results": [],
        "miss_reason": None,
        "interpretation": None,
        "audit_rank": None,
        "audit_status": None,
        "audit_cap": None,
    }

    # ── 1. Target selection ──────────────────────────────────────────────────
    try:
        rows = select_for_disease(disease_name)
    except DiseaseNotInUniverse as e:
        result.update(status="out_of_scope", in_universe=False,
                      miss_reason=str(e),
                      interpretation=f"'{disease_name}' outside rare/NTD universe.")
        _log(f"  OUT OF SCOPE: {e}")
        return result
    except Exception as e:
        result.update(status="error", in_universe=True,
                      miss_reason=f"target_selection failed: {e}",
                      interpretation="Target selection error.")
        _log(f"  ERROR in target_selection: {e}")
        return result

    result["in_universe"] = True
    result["candidate_targets_considered"] = [
        {"target_symbol": r.get("target_symbol"),
         "uniprot_id": r.get("uniprot_id"),
         "ot_association_score": r.get("ot_association_score")}
        for r in rows
    ]
    # backward-compat: report the top-1 target
    top = rows[0]
    result["selected_target"] = {
        "target_symbol": top["target_symbol"],
        "uniprot_id": top.get("uniprot_id"),
        "ot_association_score": top.get("ot_association_score", 0.0),
    }
    _log(f"  top-{TOP_K} targets: " + ", ".join(
        f"{r.get('target_symbol')} (OT {r.get('ot_association_score', 0):.2f})"
        for r in rows[:TOP_K]
    ))

    # ── 2. Loop over top-K targets ───────────────────────────────────────────
    top_rows = rows[:TOP_K]
    per_target_results: list[dict[str, Any]] = []
    first_hit: Optional[dict[str, Any]] = None

    for k_idx, row in enumerate(top_rows, 1):
        _log(f"  [target {k_idx}/{len(top_rows)}] "
             f"{row.get('target_symbol')} ({row.get('uniprot_id')}) "
             f"OT={row.get('ot_association_score', 0):.3f}")
        rec = _run_one_target(row, drug_name)
        rec["target_rank"] = k_idx
        per_target_results.append(rec)

        if rec["found"] and first_hit is None:
            first_hit = rec
            first_hit["_k_idx"] = k_idx
            first_hit["_target_row"] = row

    result["per_target_results"] = per_target_results
    result["n_chemist_candidates"] = sum(r.get("n_chemist_candidates", 0) for r in per_target_results)
    result["n_reviewed_candidates"] = sum(r.get("n_reviewed", 0) for r in per_target_results)

    # ── 3. Audit (best-effort, using top-1 target pool) ─────────────────────
    try:
        audit_res = _audit.run_audit(disease_name, drug_name)
        if audit_res.get("status") in ("no_case", "no_candidates"):
            if first_hit is not None:
                audit_res = {"status": "found", "rank": first_hit["rank"],
                             "total_candidates": result["n_reviewed_candidates"],
                             "cap_reason": None}
            else:
                audit_res = {"status": "absent",
                             "total_candidates": result["n_reviewed_candidates"],
                             "cap_reason": None}
    except Exception as e:
        audit_res = {"status": "error", "error": str(e)}

    result["audit_status"] = audit_res.get("status")
    result["audit_rank"]   = audit_res.get("rank")
    result["audit_cap"]    = audit_res.get("cap_reason")

    # ── 4. Record outcome ────────────────────────────────────────────────────
    if first_hit is None:
        result.update(
            status="miss", found=False,
            miss_reason=_diagnose_miss_topk(drug_name, top_rows,
                                            result["n_chemist_candidates"]),
        )
        result["interpretation"] = (
            f"MISS — {drug_name} not found in any of the top-{len(top_rows)} target pools. "
            + result["miss_reason"]
        )
        _log(f"  MISS: {drug_name} absent from all {len(top_rows)} target pools")
        return result

    k_idx   = first_hit["_k_idx"]
    hit_row = first_hit["_target_row"]
    rank    = first_hit["rank"]
    top10   = "within top 10" if rank <= TOP_N else f"below top 10 (rank {rank})"
    strong  = "reached STRONG_MATCH" if first_hit.get("strong_match") else "did NOT reach STRONG_MATCH"

    result.update(
        status="hit", found=True, rank=rank,
        recovered_top10=(rank <= TOP_N),
        composite_score=first_hit.get("composite_score"),
        strong_match=bool(first_hit.get("strong_match")),
        match_method=first_hit.get("match_method"),
        hit_at_target_rank=k_idx,
        hit_target_symbol=hit_row.get("target_symbol"),
        hit_target_uniprot=hit_row.get("uniprot_id"),
        interpretation=(
            f"HIT — {drug_name} at rank {rank} "
            f"(composite={first_hit.get('composite_score')}, {top10}, {strong}) "
            f"via target-rank {k_idx} ({hit_row.get('target_symbol')}), "
            f"by {first_hit.get('match_method')}."
        ),
    )
    _log(f"  HIT: drug found via target rank {k_idx} "
         f"({hit_row.get('target_symbol')}), reviewed rank {rank}, "
         f"composite={first_hit.get('composite_score')}")
    return result


def _holdout_fp(holdout_drugs: list[str]) -> tuple[str, ...]:
    return tuple(sorted(_norm_name(h) for h in (holdout_drugs or [])))


def _load_existing() -> dict[tuple[str, str, tuple[str, ...]], dict[str, Any]]:
    if not os.path.exists(RESULTS_JSON):
        return {}
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        # Key includes the holdout fingerprint: pre-holdout (naive) results
        # have no holdout_drugs field and therefore never satisfy a blind resume.
        return {
            (_norm_name(c["drug_name"]), _norm_name(c["disease_name"]),
             _holdout_fp(c.get("holdout_drugs"))): c
            for c in data.get("cases", [])
        }
    except (json.JSONDecodeError, OSError):
        return {}


def _flush(cases: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "top_k": TOP_K,
        "top_n": TOP_N,
        "strong_match_threshold": STRONG_MATCH_THRESHOLD,
        "cases": cases,
    }
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    _log(f"  saved {len(cases)} cases → {RESULTS_JSON}")


def _build_markdown(cases: list[dict[str, Any]]) -> str:
    in_uni = [c for c in cases if c.get("in_universe")]
    hits   = sum(1 for c in in_uni if c["status"] == "hit")
    misses = sum(1 for c in in_uni if c["status"] == "miss")
    errors = sum(1 for c in in_uni if c["status"] == "error")
    oos    = sum(1 for c in cases  if c["status"] == "out_of_scope")

    lines: list[str] = [
        f"# repoDB Retrospective — 10 In-Universe Cases (Top-{TOP_K} Targets)\n",
        f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n",
        f"_Each disease runs the Biologist→Chemist→Reviewer pipeline against "
        f"the top {TOP_K} OT-associated targets. A case is HIT if the approved "
        f"drug is found in ANY of the {TOP_K} pools._\n",
        "## Summary\n",
        f"- In-universe cases: {len(in_uni)}/10",
        f"- Hits: {hits}  |  Misses: {misses}  |  Errors: {errors}  |  Out-of-scope: {oos}\n",
    ]

    # Per-case table
    lines += [
        "## Per-case table\n",
        f"| # | Disease | Drug | Hit Target (rank) | Reviewed Rank | Score | Top10 | Strong | Status |",
        f"|---|---|---|---|---|---|---|---|---|",
    ]
    for n, c in enumerate(in_uni, 1):
        if c.get("hit_target_symbol"):
            tgt = f"{c['hit_target_symbol']} (OT rank {c.get('hit_at_target_rank')})"
        else:
            tgt = (c.get("selected_target") or {}).get("target_symbol") or "—"
        rank = c.get("rank") if c.get("rank") is not None else "—"
        comp = f"{c['composite_score']:.4f}" if c.get("composite_score") is not None else "—"
        t10  = "✓" if c.get("recovered_top10") else "—"
        sm   = "✓" if c.get("strong_match") else "—"
        lines.append(
            f"| {n} | {c['disease_name']} | {c['drug_name']} | {tgt} | {rank} | {comp} | {t10} | {sm} | **{c['status']}** |"
        )

    # Per-target breakdown for each case
    lines += ["", "## Per-target breakdown\n"]
    for c in in_uni:
        lines.append(f"### {c['drug_name']} / {c['disease_name']}\n")
        lines += [
            "| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in (c.get("per_target_results") or []):
            found_str = "✓ HIT" if r.get("found") else "—"
            rev_rank  = str(r.get("rank")) if r.get("rank") is not None else "—"
            score_str = f"{r['composite_score']:.4f}" if r.get("composite_score") is not None else "—"
            ot_score  = f"{r.get('ot_association_score', 0):.3f}"
            lines.append(
                f"| {r.get('target_rank','?')} | {r.get('target_symbol','?')} | "
                f"{r.get('uniprot_id','?')} | {ot_score} | "
                f"{r.get('n_chemist_candidates',0)} | {r.get('n_reviewed',0)} | "
                f"{found_str} | {rev_rank} | {score_str} |"
            )
        lines.append("")

    # Comparison with top-1 baseline
    lines += [
        "## Comparison: top-1 vs top-3 targets\n",
        "| # | Disease | Drug | Top-1 status | Top-3 status | Recovered by target rank |",
        "|---|---|---|---|---|---|",
    ]
    # Load top-1 baseline if available
    top1_json = os.path.join(VALIDATION_DIR, "repodb_results.json")
    top1_map: dict[tuple[str, str], dict[str, Any]] = {}
    if os.path.exists(top1_json):
        try:
            with open(top1_json, encoding="utf-8") as f:
                top1_data = json.load(f)
            for c1 in top1_data.get("cases", []):
                top1_map[(_norm_name(c1["drug_name"]),
                          _norm_name(c1["disease_name"]))] = c1
        except Exception:
            pass

    for n, c in enumerate(in_uni, 1):
        key = (_norm_name(c["drug_name"]), _norm_name(c["disease_name"]))
        c1 = top1_map.get(key)
        top1_status = f"**{c1['status']}**" if c1 else "n/a"
        top3_status = f"**{c['status']}**"
        if c.get("found"):
            recovered = f"rank {c['hit_at_target_rank']} ({c['hit_target_symbol']})"
        elif c1 and c1.get("found"):
            recovered = f"rank 1 ({(c1.get('selected_target') or {}).get('target_symbol','')})"
        else:
            recovered = "—"
        lines.append(
            f"| {n} | {c['disease_name']} | {c['drug_name']} | {top1_status} | {top3_status} | {recovered} |"
        )

    return "\n".join(lines)


def main() -> None:
    _log(f"Running top-{TOP_K} retrospective validation (10 repoDB cases)")
    done = _load_existing()
    _log(f"Loaded {len(done)} already-completed results")

    cases_ordered: list[dict[str, Any]] = []
    for csv_idx, drug, disease in TARGET_CASES:
        holdout_drugs = [drug]
        key = (_norm_name(drug), _norm_name(disease), _holdout_fp(holdout_drugs))
        if key in done:
            _log(f"  SKIP (already done): {drug} / {disease}")
            cases_ordered.append(done[key])
            continue

        _log(f"=== CASE (row {csv_idx}): {drug} / {disease} "
             f"[benchmark holdout: {holdout_drugs}] ===")
        with holdout_mod.holdout_active(holdout_drugs):
            result = _run_inline_pipeline(drug, disease)
            unresolved = holdout_mod.unresolved()
        result["benchmark_mode"] = "holdout"
        result["holdout_drugs"] = holdout_drugs
        if unresolved:
            result["holdout_unresolved"] = unresolved
        cases_ordered.append(result)
        done[key] = result
        _flush(cases_ordered)

    cases_ordered = miss_classifier.classify_cases(cases_ordered)
    _flush(cases_ordered)
    md = _build_markdown(cases_ordered)
    md += "\n\n" + "\n".join(miss_classifier.breakdown_lines(cases_ordered)) + "\n"
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(md)
    _log(f"All {len(cases_ordered)} cases done → {RESULTS_MD}")
    miss_classifier.write_combined_summary()

    # Summary line for quick review
    in_uni = [c for c in cases_ordered if c.get("in_universe")]
    hits = [c for c in in_uni if c["status"] == "hit"]
    _log(f"RESULT: {len(hits)}/{len(in_uni)} hits with top-{TOP_K} targets")
    for h in hits:
        _log(f"  HIT: {h['drug_name']} / {h['disease_name']} via "
             f"target rank {h.get('hit_at_target_rank')} ({h.get('hit_target_symbol')})")


if __name__ == "__main__":
    main()
