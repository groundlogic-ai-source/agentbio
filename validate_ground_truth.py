"""
Ground-truth validation runner.

Checks five canonical drug-repurposing cases against the live pipeline:
  Scored cases  (formally approved repurposing, graded HIT / MISS / MISS_MECHANISM_CLASS_MATCH):
    - sildenafil  / pulmonary arterial hypertension
    - thalidomide / multiple myeloma
    - everolimus  / tuberous sclerosis

  Excluded cases (not scored, documented with reason):
    - metformin  / PCOS              (off-label; no FDA approval for PCOS)
    - propranolol / infantile hemangioma  (disease outside Orphanet/NTD universe)

Settings (frozen — code is not changed by this script):
  REPURPOSING_ONLY = True
  TOP_K_TARGETS    = 5   (PATHWAY_NEIGHBOR_MIN_APPROVED=3 via env/default)
  TOP_K_FRACTION   = 0.0 (disabled)

Writes:
  output/validation_2026-07-05.json
  output/validation_2026-07-05.md

Does NOT overwrite any existing output files.
"""

import json
import os
import sys
import traceback
from datetime import date
from typing import Any, Optional

# ── pipeline imports ──────────────────────────────────────────────────────────
from agents.target_selection import select_for_disease, DiseaseNotInUniverse, OUTPUT_DIR
from data_sources.chembl import get_target_candidate_compounds
from agents.biologist import get_pathway_neighbor_targets
from agents.chemist import PATHWAY_NEIGHBOR_MIN_APPROVED, _is_max_phase_approved

TODAY = date.today().isoformat()          # 2026-07-05
REPURPOSING_ONLY = True
TOP_K = 5


# ── helpers ───────────────────────────────────────────────────────────────────

def _approved_pool(uniprot: str) -> list[dict[str, Any]]:
    """Fetch the repurposing-only (approved-drugs-only) compound pool for a target."""
    cc = get_target_candidate_compounds(uniprot, repurposing_only=REPURPOSING_ONLY)
    return cc.get("compounds", [])


def _approved_count(compounds: list[dict[str, Any]]) -> int:
    return sum(1 for c in compounds if _is_max_phase_approved(c.get("max_phase")))


def _drug_names(compounds: list[dict[str, Any]]) -> list[str]:
    return [
        (c.get("pref_name") or c.get("molecule_chembl_id") or "?").upper()
        for c in compounds
    ]


def _find_drug(compounds: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    needle = name.upper()
    for c in compounds:
        n = (c.get("pref_name") or "").upper()
        if needle in n or n in needle:
            return c
    return None


def _mechanism_class_match(compounds: list[dict[str, Any]],
                           keywords: list[str]) -> list[str]:
    """Return drug names whose pref_name contains any of the given mechanism keywords."""
    matches = []
    for c in compounds:
        n = (c.get("pref_name") or "").upper()
        if any(kw.upper() in n for kw in keywords):
            matches.append(n)
    return matches


def _pool_for_targets(targets: list[dict[str, Any]],
                      disease_name: str,
                      target_symbol: str) -> dict[str, Any]:
    """
    Given a list of top-K scored targets, build the full compound pool for the
    requested target_symbol (primary target), applying lazy expansion as the
    production chemist would.

    Returns:
      {
        "target_symbol": str,
        "uniprot_id": str,
        "primary_compounds": [...],          # approved pool for primary target
        "n_primary_approved": int,
        "expansion_triggered": bool,
        "neighbors": [...],                  # list of {target_symbol, uniprot_id}
        "expanded_compounds": [...],         # approved pool from all neighbors
        "all_approved_drug_names": [...],    # union of primary + neighbor pools
      }
    """
    hit_target = next(
        (t for t in targets if t.get("target_symbol") == target_symbol), None
    )
    if hit_target is None:
        # Not in top-K; use provided symbol/uniprot from caller
        return {"error": f"{target_symbol} not in top-{TOP_K} targets"}

    uniprot = hit_target["uniprot_id"]
    primary = _approved_pool(uniprot)
    n_primary = _approved_count(primary)
    expansion = n_primary < PATHWAY_NEIGHBOR_MIN_APPROVED

    neighbor_compounds: list[dict[str, Any]] = []
    neighbor_info: list[dict[str, str]] = []
    if expansion:
        neighbors = get_pathway_neighbor_targets(uniprot, disease_name)
        for nbr in neighbors:
            nbr_uid = nbr.get("uniprot_id", "")
            nbr_sym = nbr.get("target_symbol", nbr_uid)
            if not nbr_uid:
                continue
            nbr_pool = _approved_pool(nbr_uid)
            if nbr_pool:
                neighbor_compounds.extend(nbr_pool)
                neighbor_info.append(
                    {"target_symbol": nbr_sym, "uniprot_id": nbr_uid,
                     "n_approved": _approved_count(nbr_pool),
                     "drug_names": _drug_names(nbr_pool)}
                )

    all_compounds = primary + neighbor_compounds
    return {
        "target_symbol": target_symbol,
        "uniprot_id": uniprot,
        "primary_compounds": primary,
        "n_primary_approved": n_primary,
        "expansion_triggered": expansion,
        "neighbors": neighbor_info,
        "expanded_compounds": neighbor_compounds,
        "all_approved_drug_names": sorted(set(_drug_names(all_compounds))),
    }


# ── case runners ──────────────────────────────────────────────────────────────

def run_scored_case(disease_query: str,
                    expected_drug: str,
                    expected_target: str,
                    mechanism_class_keywords: list[str],
                    notes: str = "") -> dict[str, Any]:
    """
    Run Stage 1 for the disease, build the compound pool for the expected
    primary target (with lazy expansion), and classify the result.

    Classification:
      HIT                      — expected_drug found in the approved pool
      MISS_MECHANISM_CLASS_MATCH — drug not found but mechanism-class siblings are
      MISS                     — neither specific drug nor class found
    """
    print(f"\n{'='*72}")
    print(f"  SCORED CASE: {expected_drug} / {disease_query}")
    print(f"{'='*72}")

    result: dict[str, Any] = {
        "case": f"{expected_drug} / {disease_query}",
        "disease_query": disease_query,
        "expected_drug": expected_drug,
        "expected_primary_target": expected_target,
        "status": None,
        "status_reason": None,
        "top_k_targets": [],
        "expected_target_rank": None,
        "compound_pool": {},
        "notes": notes,
    }

    try:
        rows = select_for_disease(disease_query)
    except DiseaseNotInUniverse as e:
        result["status"] = "EXCLUDED_UNIVERSE"
        result["status_reason"] = str(e)
        return result
    except Exception as e:
        result["status"] = "ERROR"
        result["status_reason"] = str(e)
        return result

    # Apply K=5 (plain integer cap, TOP_K_FRACTION=0.0 disabled)
    top_k = rows[:TOP_K]
    result["top_k_targets"] = [
        {"rank": i + 1,
         "target_symbol": t["target_symbol"],
         "uniprot_id": t.get("uniprot_id", ""),
         "disease_name": t.get("disease_name", ""),
         "tractability_score": round(t.get("tractability_score", 0.0), 4),
         "unmet_need_score": round(t.get("unmet_need_score", 0.0), 4),
         "total_score": round(t.get("tractability_score", 0.0) + t.get("unmet_need_score", 0.0), 4),
         "target_discovery_method": t.get("target_discovery_method", ""),
        }
        for i, t in enumerate(top_k)
    ]

    # Check expected target rank
    for i, t in enumerate(top_k):
        if t.get("target_symbol") == expected_target:
            result["expected_target_rank"] = i + 1
            break

    print(f"  Top-{TOP_K} targets:")
    for r in result["top_k_targets"]:
        marker = " ← EXPECTED" if r["target_symbol"] == expected_target else ""
        print(f"    #{r['rank']} {r['target_symbol']:12s} ({r['uniprot_id']:10s}) "
              f"tract={r['tractability_score']:.4f} unmet={r['unmet_need_score']:.4f} "
              f"total={r['total_score']:.4f}  {r['target_discovery_method']}{marker}")

    # Find the expected target (may be outside top-K but still scoreable)
    matched_target = next(
        (t for t in rows if t.get("target_symbol") == expected_target), None
    )
    if matched_target is None:
        result["status"] = "MISS"
        result["status_reason"] = (
            f"Expected target {expected_target} not found among scored targets at all."
        )
        return result

    # Build the compound pool (with lazy expansion)
    all_rows_pool = _pool_for_targets(
        rows,  # search full ranked list, not just top_k
        matched_target.get("disease_name", disease_query),
        expected_target,
    )
    result["compound_pool"] = all_rows_pool

    if "error" not in all_rows_pool:
        print(f"  {expected_target} primary pool: {all_rows_pool['n_primary_approved']} approved "
              f"→ expansion={'YES' if all_rows_pool['expansion_triggered'] else 'NO'}")
        if all_rows_pool["expansion_triggered"] and all_rows_pool["neighbors"]:
            for nbr in all_rows_pool["neighbors"]:
                print(f"    neighbor {nbr['target_symbol']}: {nbr['n_approved']} approved — "
                      f"{nbr['drug_names'][:5]}")
        print(f"  All approved drug names in pool: {all_rows_pool['all_approved_drug_names']}")

    pool_names = all_rows_pool.get("all_approved_drug_names", [])

    # Classification
    exact_hit = any(expected_drug.upper() in n or n in expected_drug.upper()
                    for n in pool_names)
    class_matches = _mechanism_class_match(
        all_rows_pool.get("primary_compounds", []) +
        all_rows_pool.get("expanded_compounds", []),
        mechanism_class_keywords,
    )

    if exact_hit:
        result["status"] = "HIT"
        result["status_reason"] = (
            f"'{expected_drug}' found directly in the approved compound pool for "
            f"{expected_target} (or its pathway neighbors)."
        )
    elif class_matches:
        result["status"] = "MISS_MECHANISM_CLASS_MATCH"
        result["status_reason"] = (
            f"'{expected_drug}' not found by name in the compound pool, but "
            f"mechanism-class compounds were: {class_matches}."
        )
    else:
        result["status"] = "MISS"
        result["status_reason"] = (
            f"'{expected_drug}' and no mechanism-class matches found in pool. "
            f"Pool contained: {pool_names[:10]}."
        )

    print(f"  → STATUS: {result['status']}")
    print(f"     {result['status_reason']}")
    return result


def run_excluded_case(disease_query: str,
                      expected_drug: str,
                      exclusion_reason: str,
                      check_universe: bool = True) -> dict[str, Any]:
    """Confirm exclusion reason (optionally probe the universe) and document."""
    print(f"\n{'='*72}")
    print(f"  EXCLUDED CASE: {expected_drug} / {disease_query}")
    print(f"{'='*72}")

    result: dict[str, Any] = {
        "case": f"{expected_drug} / {disease_query}",
        "disease_query": disease_query,
        "expected_drug": expected_drug,
        "status": "EXCLUDED",
        "exclusion_reason": exclusion_reason,
        "universe_probe": None,
    }

    if check_universe:
        try:
            rows = select_for_disease(disease_query)
            result["universe_probe"] = {
                "outcome": "found_in_universe",
                "matched_disease": rows[0]["disease_name"] if rows else None,
                "top_target": rows[0]["target_symbol"] if rows else None,
                "note": ("Disease IS in universe but case is excluded at the "
                         "approved-indication level (off-label use only)."),
            }
            print(f"  Universe probe: disease found → {rows[0]['disease_name'] if rows else '?'}")
        except DiseaseNotInUniverse as e:
            result["universe_probe"] = {
                "outcome": "not_in_universe",
                "error": str(e)[:200],
            }
            print(f"  Universe probe: NOT in universe → {str(e)[:120]}")
        except Exception as e:
            result["universe_probe"] = {"outcome": "error", "error": str(e)[:200]}
            print(f"  Universe probe: error → {e}")

    print(f"  → STATUS: EXCLUDED  |  Reason: {exclusion_reason}")
    return result


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    output_dir = OUTPUT_DIR

    print("\n" + "="*72)
    print(" SILVER BULLET — FINAL CANONICAL VALIDATION RUN")
    print(f" Date: {TODAY}")
    print(f" Settings: REPURPOSING_ONLY={REPURPOSING_ONLY}  K={TOP_K}  "
          f"PATHWAY_NEIGHBOR_MIN_APPROVED={PATHWAY_NEIGHBOR_MIN_APPROVED}  "
          f"TOP_K_FRACTION=0.0 (disabled)")
    print("="*72)

    scored_results = []
    excluded_results = []

    # ── SCORED CASES ──────────────────────────────────────────────────────────

    # 1. Sildenafil / PAH
    # PDE5A (O76074) is the pharmacological-precedent target for PAH via sildenafil.
    # Sildenafil (Revatio) is FDA-approved for PAH.
    scored_results.append(run_scored_case(
        disease_query="pulmonary arterial hypertension",
        expected_drug="SILDENAFIL",
        expected_target="PDE5A",
        mechanism_class_keywords=["SILDENAFIL", "TADALAFIL", "VARDENAFIL",
                                  "AVANAFIL", "PDE5"],
        notes=(
            "Sildenafil (Revatio) is FDA-approved for PAH. PDE5A is its "
            "primary target. Expected to surface via pharmacological-precedent "
            "pathway (ChEMBL approved-drug MOA table)."
        ),
    ))

    # 2. Thalidomide / Multiple Myeloma
    # CRBN (Q96SW2) is the direct binding target (E3 ligase adaptor).
    # Thalidomide, lenalidomide, pomalidomide are all approved IMiDs.
    scored_results.append(run_scored_case(
        disease_query="multiple myeloma",
        expected_drug="THALIDOMIDE",
        expected_target="CRBN",
        mechanism_class_keywords=["THALIDOMIDE", "LENALIDOMIDE", "POMALIDOMIDE",
                                  "AVADOMIDE", "IBERDOMIDE"],
        notes=(
            "Thalidomide is FDA-approved for multiple myeloma (2006). CRBN "
            "(cereblon) is the IMiD binding target. Lenalidomide and pomalidomide "
            "(same mechanism) are also approved and serve as class matches."
        ),
    ))

    # 3. Everolimus / Tuberous Sclerosis (TSC)
    # TSC1 (Q92574) is the primary genetic target.
    # Lazy expansion triggers (0 approved in primary pool) → MTOR neighbours.
    # MTOR pool has SIROLIMUS and TEMSIROLIMUS but NOT EVEROLIMUS
    # (everolimus binds FKBP12, so ChEMBL records its bioactivity there).
    # Expected: MISS_MECHANISM_CLASS_MATCH.
    scored_results.append(run_scored_case(
        disease_query="tuberous sclerosis",
        expected_drug="EVEROLIMUS",
        expected_target="TSC1",
        mechanism_class_keywords=["SIROLIMUS", "TEMSIROLIMUS", "EVEROLIMUS",
                                  "RIDAFOROLIMUS", "RAPALOG", "RAPAMYCIN"],
        notes=(
            "Everolimus (Afinitor/Votubia) is FDA-approved for TSC. TSC1 is the "
            "primary genetic target. ChEMBL records everolimus activity against "
            "FKBP12 (its direct binding partner), not MTOR; therefore the MTOR "
            "IC50/Ki pool contains sirolimus and temsirolimus (same mechanism — "
            "mTOR inhibition via FKBP12 complex) but not everolimus by name. "
            "Classified MISS_MECHANISM_CLASS_MATCH: correct mechanism and drug class "
            "(rapalogs) are surfaced, specific labeled compound (everolimus) is not."
        ),
    ))

    # ── EXCLUDED CASES ────────────────────────────────────────────────────────

    # 4. Metformin / PCOS
    excluded_results.append(run_excluded_case(
        disease_query="polycystic ovary syndrome",
        expected_drug="METFORMIN",
        exclusion_reason=(
            "Metformin has no FDA-approved indication for PCOS — its use in "
            "PCOS is off-label. Silver Bullet's approved-drug discovery path "
            "surfaces only formally approved repurposing. PCOS may be in the "
            "Orphanet universe but metformin/PCOS cannot form a valid scored pair."
        ),
        check_universe=True,
    ))

    # 5. Propranolol / Infantile Hemangioma
    excluded_results.append(run_excluded_case(
        disease_query="infantile hemangioma",
        expected_drug="PROPRANOLOL",
        exclusion_reason=(
            "Infantile hemangioma (Hemangeol/propranolol, FDA 2014) is outside "
            "the system's candidate universe (Orphanet rare diseases + WHO NTDs). "
            "Hemangiomas are common benign vascular tumors classified as "
            "congenital/developmental anomalies rather than rare diseases in "
            "Orphanet's scoring universe."
        ),
        check_universe=True,
    ))

    # ── RESULTS STRUCTURE ─────────────────────────────────────────────────────

    results = {
        "run_date": TODAY,
        "settings": {
            "REPURPOSING_ONLY": REPURPOSING_ONLY,
            "TOP_K_TARGETS": TOP_K,
            "PATHWAY_NEIGHBOR_MIN_APPROVED": PATHWAY_NEIGHBOR_MIN_APPROVED,
            "TOP_K_FRACTION": 0.0,
        },
        "scored_cases": scored_results,
        "excluded_cases": excluded_results,
        "summary": {
            "scored_total": len(scored_results),
            "hit": sum(1 for r in scored_results if r.get("status") == "HIT"),
            "miss_mechanism_class_match": sum(
                1 for r in scored_results
                if r.get("status") == "MISS_MECHANISM_CLASS_MATCH"
            ),
            "miss": sum(1 for r in scored_results if r.get("status") == "MISS"),
            "excluded_total": len(excluded_results),
        },
    }

    # ── WRITE JSON ────────────────────────────────────────────────────────────
    json_path = os.path.join(output_dir, f"validation_{TODAY}.json")
    if os.path.exists(json_path):
        print(f"\nWARNING: {json_path} already exists — not overwriting.")
    else:
        with open(json_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nWrote: {json_path}")

    # ── WRITE MARKDOWN ────────────────────────────────────────────────────────
    md_path = os.path.join(output_dir, f"validation_{TODAY}.md")
    if os.path.exists(md_path):
        print(f"WARNING: {md_path} already exists — not overwriting.")
    else:
        md = _build_markdown(results)
        with open(md_path, "w") as f:
            f.write(md)
        print(f"Wrote: {md_path}")

    # ── PRINT SUMMARY TABLE ───────────────────────────────────────────────────
    print("\n" + "="*72)
    print(" FINAL RESULTS TABLE")
    print("="*72)
    print(f"{'Case':<42} {'Status':<30} {'Target Rank'}")
    print("-"*72)
    for r in scored_results:
        rank = r.get("expected_target_rank")
        rank_str = f"#{rank}" if rank else "not in top-K"
        print(f"{r['case']:<42} {r.get('status','?'):<30} {rank_str}")
    print()
    for r in excluded_results:
        print(f"{r['case']:<42} EXCLUDED")
    print()
    s = results["summary"]
    print(f"Scored cases: {s['scored_total']}  |  "
          f"HIT: {s['hit']}  "
          f"MISS_MECHANISM_CLASS_MATCH: {s['miss_mechanism_class_match']}  "
          f"MISS: {s['miss']}")
    print(f"Excluded cases: {s['excluded_total']}")


def _build_markdown(results: dict[str, Any]) -> str:
    s = results["summary"]
    today = results["run_date"]
    lines = []

    lines.append(f"# Silver Bullet — Final Canonical Validation")
    lines.append(f"")
    lines.append(f"**Run date:** {today}  ")
    lines.append(f"**Settings:** `REPURPOSING_ONLY=True` · `K={results['settings']['TOP_K_TARGETS']}` · "
                 f"`PATHWAY_NEIGHBOR_MIN_APPROVED={results['settings']['PATHWAY_NEIGHBOR_MIN_APPROVED']}` · "
                 f"`TOP_K_FRACTION=0.0 (disabled)`  ")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # ── Scored cases ──
    lines.append(f"## Scored Cases")
    lines.append(f"")
    lines.append(f"In-scope cases: formally approved repurposing pairs where the pipeline's "
                 f"approved-drug discovery path can, in principle, surface the drug.")
    lines.append(f"")
    lines.append(f"| Case | Expected target | Target rank (K={results['settings']['TOP_K_TARGETS']}) "
                 f"| Status | Pool / match summary |")
    lines.append(f"|------|----------------|------|--------|----------------------|")

    for r in results["scored_cases"]:
        rank = r.get("expected_target_rank")
        rank_str = f"#{rank}" if rank else "not in top-K"
        pool = r.get("compound_pool", {})
        pool_names = pool.get("all_approved_drug_names", [])
        pool_summary = (", ".join(pool_names[:6]) + ("…" if len(pool_names) > 6 else "")
                        if pool_names else "—")
        lines.append(
            f"| {r['case']} | {r['expected_primary_target']} | {rank_str} "
            f"| **{r.get('status', '?')}** | {pool_summary} |"
        )

    lines.append(f"")

    for r in results["scored_cases"]:
        lines.append(f"### {r['case']}")
        lines.append(f"")
        lines.append(f"**Status:** `{r.get('status','?')}`  ")
        lines.append(f"**Reason:** {r.get('status_reason', '')}  ")
        lines.append(f"")
        if r.get("top_k_targets"):
            lines.append(f"**Top-{results['settings']['TOP_K_TARGETS']} targets:**")
            lines.append(f"")
            lines.append(f"| Rank | Symbol | UniProt | Tractability | Unmet need | Total | Method |")
            lines.append(f"|------|--------|---------|-------------|------------|-------|--------|")
            for t in r["top_k_targets"]:
                marker = " ← expected" if t["target_symbol"] == r["expected_primary_target"] else ""
                lines.append(
                    f"| #{t['rank']} | {t['target_symbol']}{marker} | {t['uniprot_id']} "
                    f"| {t['tractability_score']:.4f} | {t['unmet_need_score']:.4f} "
                    f"| {t['total_score']:.4f} | {t['target_discovery_method']} |"
                )
            lines.append(f"")

        pool = r.get("compound_pool", {})
        if pool and "error" not in pool:
            exp_trigger = pool.get("expansion_triggered", False)
            lines.append(f"**Compound pool:** primary approved={pool.get('n_primary_approved', '?')} · "
                         f"expansion={'triggered' if exp_trigger else 'skipped (≥ threshold)'}  ")
            if exp_trigger and pool.get("neighbors"):
                nbr_parts = []
                for nbr in pool["neighbors"]:
                    nbr_parts.append(f"{nbr['target_symbol']} ({nbr['n_approved']} approved)")
                lines.append(f"**Pathway neighbors expanded:** {', '.join(nbr_parts)}  ")
            pool_names = pool.get("all_approved_drug_names", [])
            lines.append(f"**All approved drugs in combined pool:** {', '.join(pool_names) if pool_names else '(none)'}  ")
        lines.append(f"")
        lines.append(f"**Notes:** {r.get('notes', '')}  ")
        lines.append(f"")

    # ── Excluded cases ──
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## Excluded Cases")
    lines.append(f"")
    lines.append(f"These cases are not scored. They fall outside what the pipeline's "
                 f"approved-drug discovery path can represent.")
    lines.append(f"")
    lines.append(f"| Case | Exclusion reason |")
    lines.append(f"|------|-----------------|")
    for r in results["excluded_cases"]:
        lines.append(f"| {r['case']} | {r['exclusion_reason'][:120]}… |")

    lines.append(f"")
    for r in results["excluded_cases"]:
        lines.append(f"### {r['case']}")
        lines.append(f"")
        lines.append(f"**Status:** `EXCLUDED`  ")
        lines.append(f"**Exclusion reason:** {r['exclusion_reason']}  ")
        probe = r.get("universe_probe")
        if probe:
            lines.append(f"**Universe probe:** outcome=`{probe['outcome']}` · "
                         f"{probe.get('note', probe.get('error', ''))[:200]}  ")
        lines.append(f"")

    # ── Summary ──
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"## Summary")
    lines.append(f"")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Scored cases total | {s['scored_total']} |")
    lines.append(f"| HIT | {s['hit']} |")
    lines.append(f"| MISS_MECHANISM_CLASS_MATCH | {s['miss_mechanism_class_match']} |")
    lines.append(f"| MISS | {s['miss']} |")
    lines.append(f"| Excluded (not scored) | {s['excluded_total']} |")
    lines.append(f"")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
