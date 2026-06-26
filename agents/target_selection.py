"""
Target Selection Agent — Stage 1 of the drug-repurposing pipeline.

Scoring is purely numeric (no LLM). A single LLM call is made at the end
to narrate the top 5 candidates using only numbers already in the table.

Run:
    python -m agents.target_selection
"""

import csv
import json
import math
import os
import sys
import time
from typing import Any, Optional

import anthropic

from cache.cache import get, set, make_key
from data_sources.orphadata import get_rare_disease_list, get_who_ntd_list
from data_sources.open_targets import search_disease_efo, get_target_disease_score
from data_sources.chembl import get_target_bioactivity_count
from data_sources.afdb import get_structure_confidence
from data_sources.clinicaltrials import check_prior_trials

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
TOP_N = 30
TOP_TARGETS_PER_DISEASE = 5

TRACTABILITY_WEIGHTS = {
    "chembl_log_count": 0.40,
    "afdb_plddt": 0.35,
    "trial_penalty": 0.25,
}

CHEMBL_COUNT_CAP = 500


def _log(msg: str) -> None:
    print(f"[target_selection] {msg}", flush=True)


def _safe_log_scale(count: int, cap: int = CHEMBL_COUNT_CAP) -> float:
    """Log-scale a count, capped, normalised to [0, 1]."""
    if count <= 0:
        return 0.0
    return math.log1p(min(count, cap)) / math.log1p(cap)


def _normalise_plddt(plddt: Optional[float]) -> float:
    """Map pLDDT (0-100) to [0, 1]. Missing → 0."""
    if plddt is None:
        return 0.0
    return max(0.0, min(1.0, plddt / 100.0))


def compute_tractability_score(
    chembl_count: int,
    median_pchembl: Optional[float],
    plddt: Optional[float],
    has_prior_failure: bool,
) -> float:
    """
    tractability_score = weighted combination of:
      - ChEMBL bioactivity count (log-scaled, capped)
      - AFDB mean pLDDT
      - prior trial failure penalty (large negative if failed)

    Returns a float in approximately [-1, 1].
    """
    chembl_component = _safe_log_scale(chembl_count)
    plddt_component = _normalise_plddt(plddt)
    penalty = -1.0 if has_prior_failure else 0.0

    score = (
        TRACTABILITY_WEIGHTS["chembl_log_count"] * chembl_component
        + TRACTABILITY_WEIGHTS["afdb_plddt"] * plddt_component
        + TRACTABILITY_WEIGHTS["trial_penalty"] * penalty
    )
    return round(score, 4)


def compute_unmet_need_score(
    has_approved_treatment: Optional[bool],
    prevalence: Optional[float],
) -> float:
    """
    unmet_need_score:
      - diseases with no approved treatments score higher
      - higher prevalence = higher unmet need (log-scaled)
      - unknown treatment status → 0.5 (flagged for manual review)

    Returns a float in [0, 1].
    """
    if has_approved_treatment is None:
        treatment_component = 0.5
    elif not has_approved_treatment:
        treatment_component = 1.0
    else:
        treatment_component = 0.0

    prevalence_component = 0.0
    if prevalence and prevalence > 0:
        prevalence_component = min(1.0, math.log1p(prevalence) / math.log1p(1_000_000))

    score = 0.7 * treatment_component + 0.3 * prevalence_component
    return round(score, 4)


def _extract_orphanet_fields(disease: dict) -> dict[str, Any]:
    """Pull treatment/prevalence hints from an Orphanet disease record."""
    has_approved = None
    prevalence = None

    type_of_inheritance = disease.get("type_of_inheritance")
    avg_age = disease.get("averageAgeOfOnset")

    orphanet_prevalence = disease.get("prevalence") or disease.get("Prevalence")
    if isinstance(orphanet_prevalence, list) and orphanet_prevalence:
        pv = orphanet_prevalence[0]
        val_per_million = pv.get("ValMoy") or pv.get("prevalence_per_million")
        if val_per_million is not None:
            try:
                prevalence = float(val_per_million)
            except (TypeError, ValueError):
                pass

    return {"has_approved_treatment": has_approved, "prevalence": prevalence}


def _build_candidate_universe() -> list[dict[str, Any]]:
    """
    Returns a unified list of disease dicts from Orphanet + WHO NTDs.
    Each dict has: {name, orpha_code, icd10, omim, mesh, source}
    """
    _log("Fetching Orphanet rare disease list …")
    orphanet = get_rare_disease_list()
    _log(f"  Orphanet: {len(orphanet)} diseases")

    ntds = get_who_ntd_list()
    _log(f"  WHO NTDs: {len(ntds)} diseases")

    candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for d in orphanet:
        name = d.get("name", "").strip()
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            candidates.append({
                "name": name,
                "orpha_code": d.get("orpha_code"),
                "icd10": d.get("icd10"),
                "omim": d.get("omim"),
                "mesh": d.get("mesh"),
                "source": "orphanet",
                "has_approved_treatment": None,
                "prevalence": None,
            })

    for d in ntds:
        name = d.get("name", "").strip()
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            candidates.append({
                "name": name,
                "orpha_code": None,
                "icd10": d.get("icd10"),
                "omim": None,
                "mesh": d.get("mesh"),
                "source": "who_ntd",
                "has_approved_treatment": None,
                "prevalence": None,
            })

    return candidates


def _score_pair(
    disease_name: str,
    target: dict[str, Any],
    association_score: float,
    has_approved_treatment: Optional[bool],
    prevalence: Optional[float],
) -> dict[str, Any]:
    """Compute all raw numbers and both scores for one (disease, target) pair."""

    target_symbol = target.get("target_symbol", "")
    uniprot_id = target.get("uniprot_id")

    chembl_data: dict[str, Any] = {"count": 0, "median_pchembl": None}
    afdb_data: dict[str, Any] = {"has_structure": False, "mean_pLDDT": None}
    trial_data: dict[str, Any] = {"has_negative_repurposing_result": False, "trial_count": 0}

    if uniprot_id:
        try:
            chembl_data = get_target_bioactivity_count(uniprot_id)
        except Exception as e:
            _log(f"  WARN chembl {uniprot_id}: {e}")

        try:
            afdb_data = get_structure_confidence(uniprot_id)
        except Exception as e:
            _log(f"  WARN afdb {uniprot_id}: {e}")

        try:
            trial_data = check_prior_trials(target_symbol, disease_name)
        except Exception as e:
            _log(f"  WARN trials {target_symbol}/{disease_name}: {e}")

    tractability = compute_tractability_score(
        chembl_count=chembl_data.get("count", 0),
        median_pchembl=chembl_data.get("median_pchembl"),
        plddt=afdb_data.get("mean_pLDDT"),
        has_prior_failure=trial_data.get("has_negative_repurposing_result", False),
    )
    unmet_need = compute_unmet_need_score(
        has_approved_treatment=has_approved_treatment,
        prevalence=prevalence,
    )

    return {
        "disease_name": disease_name,
        "target_symbol": target_symbol,
        "ensembl_id": target.get("ensembl_id"),
        "uniprot_id": uniprot_id,
        "ot_association_score": round(association_score, 4),
        "chembl_activity_count": chembl_data.get("count", 0),
        "median_pchembl": chembl_data.get("median_pchembl"),
        "chembl_pooled_multi_target": chembl_data.get("pooled_across_multiple_targets", False),
        "afdb_has_structure": afdb_data.get("has_structure", False),
        "afdb_mean_plddt": afdb_data.get("mean_pLDDT"),
        "prior_trial_count": trial_data.get("trial_count", 0),
        "has_negative_repurposing_result": trial_data.get("has_negative_repurposing_result", False),
        "has_approved_treatment": has_approved_treatment,
        "prevalence_per_million": prevalence,
        "treatment_status_needs_review": has_approved_treatment is None,
        "tractability_score": tractability,
        "unmet_need_score": unmet_need,
    }


def _narrate_top5(top5: list[dict[str, Any]]) -> str:
    """Single LLM call to narrate the top 5 candidates."""
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")

    if not base_url or not api_key:
        return "[LLM narration skipped — AI_INTEGRATIONS_ANTHROPIC_BASE_URL or API_KEY not set]"

    client = anthropic.Anthropic(base_url=base_url, api_key=api_key)

    summary_rows = []
    for i, row in enumerate(top5, 1):
        summary_rows.append(
            f"{i}. {row['disease_name']} / {row['target_symbol']}: "
            f"tractability={row['tractability_score']}, "
            f"unmet_need={row['unmet_need_score']}, "
            f"OT_association={row['ot_association_score']}, "
            f"ChEMBL_count={row['chembl_activity_count']}, "
            f"median_pChEMBL={row['median_pchembl']}, "
            f"pLDDT={row['afdb_mean_plddt']}, "
            f"has_structure={row['afdb_has_structure']}, "
            f"prior_trial_failure={row['has_negative_repurposing_result']}"
        )
    table_text = "\n".join(summary_rows)

    prompt = (
        "You are a drug-repurposing analyst. Below are the top 5 scored disease-target pairs "
        "from a computational pipeline. Write a 2-3 sentence plain-English summary of why "
        "these candidates are scientifically interesting. Reference only the numbers provided "
        "in the table below. Do NOT generate, invent, or revise any scores — only interpret "
        "what is already present.\n\n"
        f"Top 5 candidates:\n{table_text}\n\n"
        "Summary (2-3 sentences):"
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        block = message.content[0]
        return block.text if block.type == "text" else str(block)
    except Exception as e:
        return f"[LLM narration failed: {e}]"


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    candidates = _build_candidate_universe()

    _log(f"Total candidate diseases: {len(candidates)}")
    _log("Resolving EFO IDs and fetching Open Targets associations …")

    scored_pairs: list[dict[str, Any]] = []
    total = len(candidates)

    for idx, disease in enumerate(candidates, 1):
        disease_name = disease["name"]
        _log(f"  [{idx}/{total}] {disease_name}")

        efo_id = search_disease_efo(disease_name)
        if not efo_id:
            _log(f"    → no EFO ID found, skipping")
            continue

        targets = get_target_disease_score(efo_id)
        top_targets = targets[:TOP_TARGETS_PER_DISEASE]

        if not top_targets:
            _log(f"    → no targets found for EFO {efo_id}")
            continue

        _log(f"    → EFO {efo_id}, {len(top_targets)} targets")

        has_approved = disease.get("has_approved_treatment")
        prevalence = disease.get("prevalence")

        for target in top_targets:
            pair = _score_pair(
                disease_name=disease_name,
                target=target,
                association_score=target.get("association_score", 0.0),
                has_approved_treatment=has_approved,
                prevalence=prevalence,
            )
            scored_pairs.append(pair)

    if not scored_pairs:
        _log("ERROR: no scored pairs — check API connectivity and try again.")
        sys.exit(1)

    scored_pairs.sort(key=lambda x: (x["tractability_score"] + x["unmet_need_score"]), reverse=True)
    top30 = scored_pairs[:TOP_N]

    json_path = os.path.join(OUTPUT_DIR, "top_candidates.json")
    csv_path = os.path.join(OUTPUT_DIR, "top_candidates.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(top30, f, indent=2, default=str)
    _log(f"Saved JSON → {json_path}")

    if top30:
        fieldnames = list(top30[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(top30)
        _log(f"Saved CSV  → {csv_path}")

    _log("\n=== TOP 5 CANDIDATES ===")
    for i, row in enumerate(top30[:5], 1):
        _log(
            f"  #{i}: {row['disease_name']} / {row['target_symbol']} "
            f"[tractability={row['tractability_score']}, unmet_need={row['unmet_need_score']}]"
        )

    _log("\nGenerating LLM narration for top 5 …")
    narration = _narrate_top5(top30[:5])
    _log("\n=== LLM NARRATION ===")
    print(narration)
    print()

    narration_path = os.path.join(OUTPUT_DIR, "narration.txt")
    with open(narration_path, "w", encoding="utf-8") as f:
        f.write(narration + "\n")
    _log(f"Saved narration → {narration_path}")

    _log(f"\nDone. {len(scored_pairs)} pairs scored, top {len(top30)} written to {OUTPUT_DIR}/")


if __name__ == "__main__":
    run()
