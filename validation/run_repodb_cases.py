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
Out: validation/repodb_results.json  (incremental — safe to interrupt and resume)
     validation/repodb_results.md
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
from api import audit as _audit
import api.jobs_db as jobs_db

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_JSON = os.path.join(VALIDATION_DIR, "repodb_results.json")
RESULTS_MD   = os.path.join(VALIDATION_DIR, "repodb_results.md")

TOP_N = 10

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


def _diagnose_miss(drug_name: str, selected_target: dict[str, Any], n_chem: int) -> str:
    sym = selected_target.get("target_symbol")
    uni = selected_target.get("uniprot_id")
    return (
        f"'{drug_name}' was absent among the {n_chem} ChEMBL compound(s) for top "
        f"target {sym} ({uni}). The Chemist only admits Homo sapiens IC50/Ki at "
        f"assay confidence ≥ 8 against THIS target — the confirmed drug's primary "
        f"target is likely different from {sym}."
    )


def _run_inline_pipeline(
    drug_name: str, disease_name: str
) -> dict[str, Any]:
    """Drive the full biologist→chemist→reviewer pipeline for one case."""
    result: dict[str, Any] = {
        "drug_name": drug_name,
        "disease_name": disease_name,
        "status": None,
        "in_universe": None,
        "selected_target": None,
        "candidate_targets_considered": [],
        "n_chemist_candidates": None,
        "n_reviewed_candidates": None,
        "found": False,
        "rank": None,
        "recovered_top10": False,
        "composite_score": None,
        "strong_match": None,
        "is_approved_drug": None,
        "match_method": None,
        "miss_reason": None,
        "interpretation": None,
        "audit_rank": None,
        "audit_status": None,
        "audit_cap": None,
    }

    # 1. Target selection
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
    top = rows[0]
    target = {
        "target_symbol": top["target_symbol"],
        "uniprot_id": top.get("uniprot_id"),
        "ensembl_id": top.get("ensembl_id"),
        "disease_name": top["disease_name"],
        "orpha_code": top.get("orpha_code"),
        "ot_association_score": top.get("ot_association_score", 0.0),
        "tractability_score": top.get("tractability_score"),
        "unmet_need_score": top.get("unmet_need_score"),
    }
    result["selected_target"] = {
        "target_symbol": target["target_symbol"],
        "uniprot_id": target["uniprot_id"],
        "ot_association_score": target["ot_association_score"],
    }
    _log(f"  top target: {target['target_symbol']} ({target['uniprot_id']})")

    # 2. Biologist → Chemist → Reviewer
    try:
        bio      = run_biologist(target)
        chem     = run_chemist(bio)
        reviewed = run_reviewer(chem, bio)
    except Exception as e:
        result.update(status="error", miss_reason=f"pipeline failed: {e}",
                      interpretation="Pipeline error after target selection.")
        _log(f"  ERROR in pipeline: {e}")
        return result

    chem_cands = chem.get("candidates", [])
    result["n_chemist_candidates"] = len(chem_cands)
    result["n_reviewed_candidates"] = len(reviewed)
    _log(f"  chemist candidates: {len(chem_cands)}; reviewed: {len(reviewed)}")

    # 3. Locate the confirmed drug in the ranked list
    rank, matched, method = _match_in_pipeline(drug_name, chem_cands, reviewed)

    # 4. Audit: try existing job first, then synthesise inline
    try:
        audit_res = _audit.run_audit(disease_name, drug_name)
        if audit_res.get("status") in ("no_case", "no_candidates"):
            # Build inline audit from just-computed pool
            if rank is not None:
                audit_res = {"status": "found", "rank": rank,
                             "total_candidates": len(reviewed), "cap_reason": None}
            else:
                audit_res = {"status": "absent",
                             "total_candidates": len(reviewed), "cap_reason": None}
    except Exception as e:
        audit_res = {"status": "error", "error": str(e)}

    result["audit_status"] = audit_res.get("status")
    result["audit_rank"]   = audit_res.get("rank")
    result["audit_cap"]    = audit_res.get("cap_reason")

    if matched is None:
        result.update(
            status="miss", found=False,
            miss_reason=_diagnose_miss(drug_name, result["selected_target"], len(chem_cands)),
        )
        result["interpretation"] = f"MISS — {drug_name} was not surfaced. " + result["miss_reason"]
        _log(f"  MISS: {drug_name} not in ranked list")
        return result

    top10 = "within top 10" if rank <= TOP_N else f"below top 10 (rank {rank})"
    strong = "reached STRONG_MATCH" if matched.get("strong_match") else "did NOT reach STRONG_MATCH"
    result.update(
        status="hit", found=True, rank=rank, recovered_top10=(rank <= TOP_N),
        composite_score=matched.get("composite_score"),
        strong_match=bool(matched.get("strong_match")),
        is_approved_drug=matched.get("is_approved_drug"),
        match_method=method,
        interpretation=(
            f"HIT — {drug_name} at rank {rank}/{len(reviewed)} "
            f"(composite={matched.get('composite_score')}, {top10}, {strong}) "
            f"against {result['selected_target']['target_symbol']}, by {method}."
        ),
    )
    _log(f"  HIT: rank {rank}, composite={matched.get('composite_score')}, "
         f"strong_match={matched.get('strong_match')}")
    return result


def _load_existing() -> dict[tuple[str, str], dict[str, Any]]:
    if not os.path.exists(RESULTS_JSON):
        return {}
    try:
        with open(RESULTS_JSON, encoding="utf-8") as f:
            data = json.load(f)
        return {
            (_norm_name(c["drug_name"]), _norm_name(c["disease_name"])): c
            for c in data.get("cases", [])
        }
    except (json.JSONDecodeError, OSError):
        return {}


def _flush(cases: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "top_n": TOP_N,
        "strong_match_threshold": STRONG_MATCH_THRESHOLD,
        "cases": cases,
    }
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    _log(f"  saved {len(cases)} cases → {RESULTS_JSON}")


def _build_markdown(cases: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# repoDB Retrospective — 10 In-Universe Cases\n",
                         f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n"]
    in_uni = [c for c in cases if c.get("in_universe")]
    hits   = sum(1 for c in in_uni if c["status"] == "hit")
    misses = sum(1 for c in in_uni if c["status"] == "miss")
    errors = sum(1 for c in in_uni if c["status"] == "error")
    oos    = sum(1 for c in cases  if c["status"] == "out_of_scope")
    lines += [
        "## Summary\n",
        f"- In-universe cases: {len(in_uni)}/10",
        f"- Hits: {hits}  |  Misses: {misses}  |  Errors: {errors}  |  Out-of-scope: {oos}\n",
        "## Per-case table\n",
        "| # | Disease | Drug | Target | Rank | Score | Top10 | Strong | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, c in enumerate(in_uni, 1):
        tgt  = (c.get("selected_target") or {}).get("target_symbol") or "—"
        rank = c.get("rank") if c.get("rank") is not None else "—"
        comp = f"{c['composite_score']:.4f}" if c.get("composite_score") is not None else "—"
        t10  = "✓" if c.get("recovered_top10") else "—"
        sm   = "✓" if c.get("strong_match") else "—"
        lines.append(
            f"| {n} | {c['disease_name']} | {c['drug_name']} | {tgt} | {rank} | {comp} | {t10} | {sm} | **{c['status']}** |"
        )
    return "\n".join(lines)


def main() -> None:
    done = _load_existing()
    _log(f"Loaded {len(done)} already-completed results")

    cases_ordered: list[dict[str, Any]] = []
    # Preserve order from TARGET_CASES
    for csv_idx, drug, disease in TARGET_CASES:
        key = (_norm_name(drug), _norm_name(disease))
        if key in done:
            _log(f"  SKIP (already done): {drug} / {disease}")
            cases_ordered.append(done[key])
            continue

        _log(f"=== CASE (row {csv_idx}): {drug} / {disease} ===")
        result = _run_inline_pipeline(drug, disease)
        cases_ordered.append(result)
        done[key] = result
        _flush(cases_ordered)

    _flush(cases_ordered)
    md = _build_markdown(cases_ordered)
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(md)
    _log(f"All {len(cases_ordered)} cases done.")


if __name__ == "__main__":
    main()
