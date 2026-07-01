"""
Retrospective validation harness for Silver Bullet.

Tests whether the EXISTING Biologist -> Chemist -> Reviewer scoring/ranking
pipeline would surface drug-disease pairs that are ALREADY confirmed real-world
repurposing successes — run as if the answer were unknown.

READ-ONLY: this module imports and drives the live agents unchanged. It does NOT
modify chemist.py, reviewer.py, target_selection.py, or any scoring logic. If the
pipeline misses a confirmed pair, that is reported as-is.

For each ground-truth (drug, disease) entry:
  1. Resolve the disease exactly as a normal manual case does
     (target_selection.select_for_disease), with NO knowledge of the drug.
  2. Run the SAME Biologist -> Chemist -> Reviewer pipeline on the top target
     (rows[0]) — precisely what the live graph pursues for a manual/blank case.
  3. The confirmed drug is NOT excluded from ChEMBL/PubChem results.
  4. Record whether the confirmed drug appears in the Reviewer's ranked list, at
     what rank, its composite_score, and whether it reached STRONG_MATCH.

IMPORTANT LIMITATION (stated openly in the report): this tests the SCORING AND
RANKING LOGIC against TODAY's live data, not the historical data that existed at
the time of each discovery. Reconstructing pre-approval data availability is not
feasible with current public APIs. A "hit" therefore means "the scoring logic
ranks the right compound highly among real candidates", not "the pipeline would
have made the discovery blind to history".

Run:  python -m validation.run_retrospective
Out:  validation/results.json, validation/results.md
"""

import json
import os
import sys
import time
from typing import Any, Optional

# Make the repo root importable when run as a plain script.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.target_selection import select_for_disease, DiseaseNotInUniverse
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from agents.reviewer import run_reviewer, STRONG_MATCH_THRESHOLD
from data_sources.pubchem import get_compound_data

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))
GROUND_TRUTH = os.path.join(VALIDATION_DIR, "ground_truth.json")
RESULTS_JSON = os.path.join(VALIDATION_DIR, "results.json")
RESULTS_MD = os.path.join(VALIDATION_DIR, "results.md")

TOP_N = 10  # "recovered in the top 10 candidates"

LIMITATION_TEXT = (
    "This harness tests the SCORING AND RANKING LOGIC on TODAY's live data, not "
    "the historical data available at the time of each discovery. The confirmed "
    "drug is intentionally NOT removed from ChEMBL/PubChem results — reconstructing "
    "pre-approval data availability is infeasible with current public APIs. A 'hit' "
    "means the existing scoring logic ranks the correct compound highly among real "
    "candidates for the disease's top target; it does NOT claim the pipeline would "
    "have made the discovery blind to history. The pipeline also pursues only the "
    "single top OT-associated target per disease (exactly as the live graph does), "
    "so a confirmed drug whose molecular target is not that top target cannot be "
    "surfaced — this is a real, reported limitation, not a scoring failure."
)


def _log(msg: str) -> None:
    print(f"[validation] {msg}", flush=True)


def _norm_name(s: Any) -> str:
    """Alphanumeric-only lowercase, for robust drug-name comparison."""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _inchikey_block(ik: Optional[str]) -> Optional[str]:
    """First (connectivity) block of an InChIKey — matches salts/forms."""
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
    """True when two drug names refer to the same compound (substring-aware)."""
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _match_in_pipeline(
    drug_name: str,
    chemist_candidates: list[dict[str, Any]],
    reviewed: list[dict[str, Any]],
) -> tuple[Optional[int], Optional[dict[str, Any]], Optional[str]]:
    """
    Find the confirmed drug in the Reviewer's ranked list.

    Matching priority:
      1. InChIKey connectivity block (via PubChem resolution of the confirmed
         name, cross-referenced against chemist candidates that carry inchikey),
         mapped to reviewer rows by molecule_chembl_id.
      2. Normalized drug-name match directly in the reviewer list.

    Returns (rank_1indexed, matched_reviewer_row, method) or (None, None, None).
    """
    conf_ik = _inchikey_block(_resolve_confirmed_inchikey(drug_name))

    # Step 1: collect ChEMBL ids of chemist candidates matching by InChIKey/name.
    matched_ids: set[str] = set()
    for c in chemist_candidates:
        cik = _inchikey_block(c.get("inchikey"))
        ik_hit = bool(conf_ik and cik and conf_ik == cik)
        nm_hit = _name_match(drug_name, c.get("drug_name", ""))
        if ik_hit or nm_hit:
            mid = c.get("molecule_chembl_id")
            if mid:
                matched_ids.add(mid)

    # Step 2: locate the earliest (best) rank in the reviewer list.
    for i, r in enumerate(reviewed, 1):
        id_hit = r.get("molecule_chembl_id") in matched_ids
        nm_hit = _name_match(drug_name, r.get("drug_name", ""))
        if id_hit or nm_hit:
            method = "inchikey/chembl_id" if id_hit else "name"
            return i, r, method

    return None, None, None


def _diagnose_miss(drug_name: str, selected_target: dict[str, Any],
                   n_chem: int) -> str:
    sym = selected_target.get("target_symbol")
    uni = selected_target.get("uniprot_id")
    return (
        f"'{drug_name}' did not appear among the {n_chem} ChEMBL candidate "
        f"compound(s) for the selected top target {sym} ({uni}). The Chemist only "
        f"admits compounds with Homo sapiens IC50/Ki bioactivity at assay "
        f"confidence >= 8 against THIS target. The most likely reason is that the "
        f"confirmed drug's molecular target is not {sym} (the top OT-associated "
        f"target for this disease), so it is out of the pursued target's candidate "
        f"pool — or it lacks qualifying high-confidence bioactivity records there."
    )


def run_case(entry: dict[str, Any]) -> dict[str, Any]:
    disease = entry["disease_name"]
    drug = entry["drug_name"]
    _log(f"=== CASE: {drug} / {disease} ===")

    result: dict[str, Any] = {
        "drug_name": drug,
        "disease_name": disease,
        "confirmed_year": entry.get("confirmed_year"),
        "known_target_note": entry.get("known_target_note"),
        "status": None,               # hit | miss | out_of_scope | error
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
    }

    # 1. Target selection (manual mode) — no knowledge of the confirmed drug.
    try:
        rows = select_for_disease(disease)
    except DiseaseNotInUniverse as e:
        result["status"] = "out_of_scope"
        result["in_universe"] = False
        result["miss_reason"] = str(e)
        result["interpretation"] = (
            f"'{disease}' is outside Silver Bullet's rare-disease / neglected-"
            f"tropical-disease scope, so the pipeline never evaluates it. This is a "
            f"scope boundary, not a scoring failure — the harness correctly refuses "
            f"to auto-pick an unrelated disease."
        )
        _log(f"  OUT OF SCOPE: {e}")
        return result
    except Exception as e:
        result["status"] = "error"
        result["miss_reason"] = f"target_selection failed: {e}"
        result["interpretation"] = (
            "The disease is in-universe but target selection raised an error "
            "(e.g. no Open Targets EFO mapping or no associated targets), so no "
            "candidates could be scored."
        )
        _log(f"  ERROR in target_selection: {e}")
        return result

    result["in_universe"] = True
    result["candidate_targets_considered"] = [
        {
            "target_symbol": r.get("target_symbol"),
            "uniprot_id": r.get("uniprot_id"),
            "ot_association_score": r.get("ot_association_score"),
        }
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

    # 2. Biologist -> Chemist -> Reviewer (the SAME live agents, unchanged).
    try:
        bio = run_biologist(target)
        chem = run_chemist(bio)
        reviewed = run_reviewer(chem, bio)
    except Exception as e:
        result["status"] = "error"
        result["miss_reason"] = f"pipeline failed: {e}"
        result["interpretation"] = (
            "The disease resolved to a target but the downstream pipeline raised "
            "an error, so no ranked candidate list was produced."
        )
        _log(f"  ERROR in pipeline: {e}")
        return result

    chem_cands = chem.get("candidates", [])
    result["n_chemist_candidates"] = len(chem_cands)
    result["n_reviewed_candidates"] = len(reviewed)
    _log(f"  chemist candidates: {len(chem_cands)}; reviewed: {len(reviewed)}")

    # 3. Locate the confirmed drug in the ranked list.
    rank, matched, method = _match_in_pipeline(drug, chem_cands, reviewed)

    if matched is None:
        result["status"] = "miss"
        result["found"] = False
        result["miss_reason"] = _diagnose_miss(drug, result["selected_target"],
                                               len(chem_cands))
        result["interpretation"] = (
            f"MISS — {drug} was not surfaced. {result['miss_reason']}"
        )
        _log(f"  MISS: {drug} not in ranked list")
        return result

    result["status"] = "hit"
    result["found"] = True
    result["rank"] = rank
    result["recovered_top10"] = rank <= TOP_N
    result["composite_score"] = matched.get("composite_score")
    result["strong_match"] = bool(matched.get("strong_match"))
    result["is_approved_drug"] = matched.get("is_approved_drug")
    result["match_method"] = method

    top10 = "within top 10" if result["recovered_top10"] else f"below top 10 (rank {rank})"
    strong = "reached STRONG_MATCH" if result["strong_match"] else "did NOT reach STRONG_MATCH"
    result["interpretation"] = (
        f"HIT — {drug} appears at rank {rank}/{len(reviewed)} "
        f"(composite_score={result['composite_score']}, {top10}, {strong}) "
        f"against target {result['selected_target']['target_symbol']}, matched by "
        f"{method}."
    )
    _log(f"  HIT: rank {rank}, composite={result['composite_score']}, "
         f"strong_match={result['strong_match']}")
    return result


def _build_markdown(cases: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Silver Bullet — Retrospective Validation Results\n")
    lines.append(f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}_\n")

    lines.append("## What this tests\n")
    lines.append(
        "For each confirmed real-world drug-repurposing success, we ran the "
        "existing Biologist -> Chemist -> Reviewer pipeline on the disease (with no "
        "knowledge of the confirmed drug) and checked whether the confirmed drug "
        "was surfaced in the Reviewer's ranked candidate list.\n"
    )

    lines.append("## Honest limitation (read first)\n")
    lines.append(f"> {LIMITATION_TEXT}\n")
    lines.append(
        "> **On LLM usage:** the recorded metrics (rank, composite_score, "
        "strong_match) are produced by fully deterministic numeric scoring — the "
        "Chemist ranks by (is_approved_drug, pchembl_value, tanimoto) and the "
        "Reviewer by a fixed weighted composite; neither uses an LLM. The pipeline's "
        "only LLM calls write prose rationale text, which this harness does not "
        "record. Rationale generation was therefore disabled for the harness run "
        "(via environment, with no code change) so the deterministic scoring path "
        "runs faster. Enabling it would not change a single number below.\n")

    lines.append("## Overall summary\n")
    lines.append(
        f"- **{summary['recovered_top10']}/{summary['total']}** confirmed pairs "
        f"recovered in the top {TOP_N} candidates."
    )
    lines.append(
        f"- **{summary['strong_matches']}/{summary['total']}** confirmed pairs "
        f"reached STRONG_MATCH (composite_score >= {STRONG_MATCH_THRESHOLD})."
    )
    lines.append(
        f"- **{summary['hits']}/{summary['total']}** confirmed pairs appeared "
        f"anywhere in the ranked list (any rank)."
    )
    lines.append(
        f"- **{summary['out_of_scope']}/{summary['total']}** diseases were outside "
        f"the rare/NTD universe the system covers."
    )
    lines.append(
        f"- **{summary['errors']}/{summary['total']}** cases errored before "
        f"producing a ranked list.\n"
    )

    lines.append("## Per-case results\n")
    lines.append(
        "| Drug | Disease | Confirmed | Status | Top target pursued | Rank | "
        "Composite | Top 10 | STRONG_MATCH |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in cases:
        tgt = (c.get("selected_target") or {}).get("target_symbol") or "—"
        rank = c.get("rank") if c.get("rank") is not None else "—"
        comp = c.get("composite_score") if c.get("composite_score") is not None else "—"
        t10 = "✓" if c.get("recovered_top10") else "—"
        sm = "✓" if c.get("strong_match") else "—"
        lines.append(
            f"| {c['drug_name']} | {c['disease_name']} | {c.get('confirmed_year')} "
            f"| {c['status']} | {tgt} | {rank} | {comp} | {t10} | {sm} |"
        )
    lines.append("")

    lines.append("## Per-case detail\n")
    for c in cases:
        lines.append(f"### {c['drug_name']} — {c['disease_name']} "
                     f"(confirmed {c.get('confirmed_year')})\n")
        lines.append(f"- **Status:** {c['status']}")
        if c.get("known_target_note"):
            lines.append(f"- **Known target (context only):** {c['known_target_note']}")
        if c.get("selected_target"):
            st = c["selected_target"]
            lines.append(
                f"- **Top target pursued:** {st.get('target_symbol')} "
                f"({st.get('uniprot_id')}), OT association "
                f"{st.get('ot_association_score')}"
            )
        considered = c.get("candidate_targets_considered") or []
        if considered:
            tstr = ", ".join(
                f"{t['target_symbol']} ({t.get('ot_association_score')})"
                for t in considered
            )
            lines.append(f"- **All targets considered for the disease:** {tstr}")
        if c.get("n_chemist_candidates") is not None:
            lines.append(
                f"- **Candidate pool:** {c['n_chemist_candidates']} chemist "
                f"candidates -> {c['n_reviewed_candidates']} reviewed."
            )
        if c.get("found"):
            lines.append(
                f"- **Found at rank {c['rank']}** — composite_score "
                f"{c['composite_score']}, STRONG_MATCH={c['strong_match']}, "
                f"is_approved_drug={c['is_approved_drug']}, matched by "
                f"{c['match_method']}."
            )
        if c.get("miss_reason"):
            lines.append(f"- **Reason:** {c['miss_reason']}")
        lines.append(f"- **Interpretation:** {c['interpretation']}\n")

    return "\n".join(lines)


def _load_ground_truth() -> list[dict[str, Any]]:
    with open(GROUND_TRUTH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_existing_cases() -> list[dict[str, Any]]:
    if not os.path.exists(RESULTS_JSON):
        return []
    try:
        with open(RESULTS_JSON, "r", encoding="utf-8") as f:
            return json.load(f).get("cases", [])
    except (json.JSONDecodeError, OSError):
        return []


def _order_key(gt: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    return {(_norm_name(e["drug_name"]), _norm_name(e["disease_name"])): i
            for i, e in enumerate(gt)}


def _merge_case(existing: list[dict[str, Any]], new: dict[str, Any],
                gt: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace any existing case for the same (drug, disease), then sort to gt order."""
    key = (_norm_name(new["drug_name"]), _norm_name(new["disease_name"]))
    merged = [c for c in existing
              if (_norm_name(c["drug_name"]), _norm_name(c["disease_name"])) != key]
    merged.append(new)
    order = _order_key(gt)
    merged.sort(key=lambda c: order.get(
        (_norm_name(c["drug_name"]), _norm_name(c["disease_name"])), 999))
    return merged


def main() -> None:
    gt = _load_ground_truth()

    # Optional: run a single case by 0-based index and merge it into the results
    # (keeps each invocation short so long, kill-prone runs persist per-case).
    only: Optional[int] = None
    if "--only" in sys.argv:
        only = int(sys.argv[sys.argv.index("--only") + 1])

    llm_on = bool(os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY"))
    _log(f"Loaded {len(gt)} ground-truth cases (LLM rationale {'ON' if llm_on else 'OFF'})")
    _log(LIMITATION_TEXT)

    if only is not None:
        cases = _load_existing_cases()
        _log(f"Running ONLY case {only}: {gt[only]['drug_name']} / {gt[only]['disease_name']}")
        case = run_case(gt[only])
        cases = _merge_case(cases, case, gt)
        _flush(cases, gt)
        _log(f"Case {only} done and merged.")
        return

    cases = []
    for entry in gt:
        case = run_case(entry)
        cases.append(case)
        _flush(cases, gt)  # incremental persistence
    _log("All cases complete.")


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    hits = sum(1 for c in cases if c["status"] == "hit")
    recovered_top10 = sum(1 for c in cases if c.get("recovered_top10"))
    strong_matches = sum(1 for c in cases if c.get("strong_match"))
    out_of_scope = sum(1 for c in cases if c["status"] == "out_of_scope")
    errors = sum(1 for c in cases if c["status"] == "error")
    return {
        "total": total,
        "hits": hits,
        "recovered_top10": recovered_top10,
        "strong_matches": strong_matches,
        "out_of_scope": out_of_scope,
        "errors": errors,
    }


def _flush(cases: list[dict[str, Any]], ground_truth: list[dict[str, Any]]) -> None:
    summary = _summarize(cases)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "limitation": LIMITATION_TEXT,
        "top_n": TOP_N,
        "strong_match_threshold": STRONG_MATCH_THRESHOLD,
        "n_cases": len(ground_truth),
        "summary": summary,
        "cases": cases,
    }
    with open(RESULTS_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(_build_markdown(cases, summary))


if __name__ == "__main__":
    main()
