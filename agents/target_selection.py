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

from cache.cache import get, set as cache_set, make_key
from data_sources.orphadata import get_rare_disease_list, get_who_ntd_list, get_disease_xrefs
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


class DiseaseNotInUniverse(Exception):
    """Raised when a manually requested disease is not in the rare/NTD universe."""


def _log(msg: str) -> None:
    print(f"[target_selection] {msg}", flush=True)


def _norm(value: Any) -> str:
    """Normalize a name/xref for case-insensitive matching."""
    return str(value).strip().lower() if value is not None else ""


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


def _load_existing_top_candidates() -> list[dict[str, Any]]:
    """Read the prior ranking-sweep output (if any) for its enriched cross-refs."""
    path = os.path.join(OUTPUT_DIR, "top_candidates.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _diseases_from_top_candidates() -> list[dict[str, Any]]:
    """
    Disease records reconstructed from the prior ranking-sweep output. These were
    genuinely "pulled in Stage 1" and may carry names/cross-refs that a fresh
    Orphanet rebuild no longer surfaces, so they extend the matchable universe.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in _load_existing_top_candidates():
        name = row.get("disease_name")
        key = _norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "orpha_code": row.get("orpha_code"),
            "icd10": row.get("icd10"),
            "omim": row.get("omim"),
            "mesh": row.get("mesh"),
            "source": row.get("disease_source", "orphanet"),
            "has_approved_treatment": row.get("has_approved_treatment"),
            "prevalence": row.get("prevalence_per_million"),
        })
    return out


def _matchable_universe() -> list[dict[str, Any]]:
    """
    The full set of diseases a manual query may resolve to: the live Orphanet/WHO
    universe PLUS any diseases already pulled into the ranking-sweep output (which
    may use names/cross-refs the fresh rebuild no longer exposes). Deduped by name.
    """
    universe = _build_candidate_universe()
    seen = {_norm(d.get("name")) for d in universe}
    for d in _diseases_from_top_candidates():
        if _norm(d.get("name")) not in seen:
            universe.append(d)
            seen.add(_norm(d.get("name")))
    return universe


def _match_disease(query: str, candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """
    Resolve a free-text query to a disease in the rare/NTD universe.

    Tries, in order:
      1. exact case-insensitive name match
      2. ICD-10 / OMIM / MeSH cross-ref present on a universe entry (WHO NTDs carry
         these inline; the ranking-sweep output enriches its top-30 with them)
      3. a UNIQUE case-insensitive substring match on the name (convenience)

    Returns the matched disease dict, or None if nothing matches.
    """
    q = _norm(query)
    if not q:
        return None

    by_name: dict[str, dict[str, Any]] = {}
    for d in candidates:
        by_name.setdefault(_norm(d.get("name")), d)

    # 1. exact name
    if q in by_name:
        return by_name[q]

    # 2. cross-ref present on an entry
    for d in candidates:
        for key in ("icd10", "omim", "mesh"):
            if _norm(d.get(key)) == q:
                return d

    # 3. unique substring on name
    substring_hits = [d for name_key, d in by_name.items() if q in name_key]
    if len(substring_hits) == 1:
        return substring_hits[0]

    return None


def select_for_disease(query: str) -> list[dict[str, Any]]:
    """
    Manual mode: look up a single disease in the rare/NTD universe and score its
    top targets with the EXACT SAME formulas used by the ranking sweep.

    Returns scored (disease, target) rows sorted best-first. Does NOT overwrite the
    shared ranking-sweep cache (output/top_candidates.json), so a manual run never
    forces a 15-60 min re-sweep on the next blank run.

    Raises:
        DiseaseNotInUniverse — the query is not a rare/neglected disease we cover.
        RuntimeError         — the disease is in-universe but has no Open Targets
                               EFO mapping or no associated targets to score.
    """
    candidates = _matchable_universe()
    disease = _match_disease(query, candidates)
    if disease is None:
        raise DiseaseNotInUniverse(
            f"'{query}' was not found in the rare-disease / neglected-tropical-disease "
            f"universe this system covers (Orphanet rare diseases + WHO NTDs). Silver "
            f"Bullet is scoped to rare and neglected diseases. Check the spelling, try "
            f"the disease's Orphanet name, or leave the field blank to auto-explore the "
            f"ranked candidate list."
        )

    disease_name = disease["name"]
    _log(f"Manual selection: matched '{query}' → '{disease_name}'")

    efo_id = search_disease_efo(disease_name)
    if not efo_id:
        raise RuntimeError(
            f"'{disease_name}' is in the rare/NTD universe but has no Open Targets EFO "
            f"mapping, so its targets cannot be scored."
        )

    targets = get_target_disease_score(efo_id)
    top_targets = targets[:TOP_TARGETS_PER_DISEASE]
    if not top_targets:
        raise RuntimeError(
            f"Open Targets returned no associated targets for '{disease_name}' "
            f"(EFO {efo_id}); there is nothing to score."
        )

    has_approved = disease.get("has_approved_treatment")
    prevalence = disease.get("prevalence")

    rows: list[dict[str, Any]] = []
    for target in top_targets:
        rows.append(_score_pair(
            disease_name=disease_name,
            target=target,
            association_score=target.get("association_score", 0.0),
            has_approved_treatment=has_approved,
            prevalence=prevalence,
            orpha_code=disease.get("orpha_code"),
            disease_source=disease.get("source", "orphanet"),
        ))

    rows.sort(key=lambda x: (x["tractability_score"] + x["unmet_need_score"]), reverse=True)

    # Enrich with Orphanet cross-refs (same per-code lookup as the sweep), or carry
    # any cross-refs already on the matched disease (WHO NTDs ship them inline).
    for row in rows:
        code = row.get("orpha_code")
        if code and row.get("disease_source") == "orphanet":
            xrefs = get_disease_xrefs(code)
            row["icd10"] = xrefs.get("icd10")
            row["omim"] = xrefs.get("omim")
            row["mesh"] = xrefs.get("mesh")
        else:
            row["icd10"] = row.get("icd10") or disease.get("icd10")
            row["omim"] = row.get("omim") or disease.get("omim")
            row["mesh"] = row.get("mesh") or disease.get("mesh")

    return rows


def _score_pair(
    disease_name: str,
    target: dict[str, Any],
    association_score: float,
    has_approved_treatment: Optional[bool],
    prevalence: Optional[float],
    orpha_code: Optional[str] = None,
    disease_source: str = "orphanet",
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
        "orpha_code": orpha_code,
        "disease_source": disease_source,
        "icd10": None,
        "omim": None,
        "mesh": None,
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
                orpha_code=disease.get("orpha_code"),
                disease_source=disease.get("source", "orphanet"),
            )
            scored_pairs.append(pair)

    if not scored_pairs:
        _log("ERROR: no scored pairs — check API connectivity and try again.")
        sys.exit(1)

    scored_pairs.sort(key=lambda x: (x["tractability_score"] + x["unmet_need_score"]), reverse=True)
    top30 = scored_pairs[:TOP_N]

    # Enrich only the top-30 Orphanet diseases with ICD-10/OMIM/MeSH cross-refs
    # (per-code lookups; cheap at this scale, not feasible across all ~11k).
    _log("Enriching top candidates with Orphanet cross-references …")
    xref_cache: dict[str, dict[str, Any]] = {}
    for row in top30:
        code = row.get("orpha_code")
        if code and row.get("disease_source") == "orphanet":
            if code not in xref_cache:
                xref_cache[code] = get_disease_xrefs(code)
            xrefs = xref_cache[code]
            row["icd10"] = xrefs.get("icd10")
            row["omim"] = xrefs.get("omim")
            row["mesh"] = xrefs.get("mesh")

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
