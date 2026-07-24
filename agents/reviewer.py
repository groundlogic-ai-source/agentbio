"""
Reviewer Agent (Stage 2).

Takes the Chemist's ranked candidate list and produces the final scored table:
  - RDKit Lipinski/Veber descriptors (MW, logP, HBD, HBA, TPSA, rotatable bonds)
  - openFDA real-world adverse-event signal
  - ClinicalTrials.gov prior-trial check for this exact drug+disease pair
  - Provenance de-duplication: the SAME pmid or ChEMBL activity id is counted only
    once across the whole scoring pass (audit integrity)
  - A single composite_score from the EXACT fixed formula below (auditable weights),
    minus a flat soft penalty for >1 Lipinski violation
  - STRONG_MATCH flag at a fixed threshold

Run:  python -m agents.reviewer
Input:  output/chemist_output.json
Output: output/reviewed_candidates.json
"""

import json
import os
import sys
from typing import Any, Optional

from rdkit import Chem
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from data_sources.openfda import get_adverse_events
from data_sources.clinicaltrials import check_prior_trials
from data_sources.chembl import get_molecule_safety_flags, get_drug_action_type
from data_sources.safety_check import web_safety_check
from data_sources.mechanism_direction import check_mechanism_direction

# ---- Auditable scoring constants (edit here to adjust the policy) -------------
COMPOSITE_WEIGHTS: dict[str, float] = {
    "pchembl": 0.30,          # normalized(pchembl_value)
    "confidence": 0.20,       # confidence_score / 9
    "ot_association": 0.20,   # normalized(open_targets_association_score)
    "tanimoto": 0.15,         # normalized(tanimoto_score)
    "no_failed_trial": 0.15,  # 1 if no prior failed trial else 0
}
LIPINSKI_PENALTY = 0.25       # flat, soft — subtracted if Lipinski violations > 1
STRONG_MATCH_THRESHOLD = 0.70
# Safety gate: withdrawn / black-box-warning compounds are capped at the same
# ceiling as unapproved compounds so they cannot reach STRONG_MATCH.
SAFETY_CAP = 0.40
# Mechanism-direction gate uses the same cap as the safety gate:
# a DIRECTIONALLY_INCOMPATIBLE verdict prevents STRONG_MATCH just as a
# safety flag does. COMPATIBLE and INSUFFICIENT_INFO never trigger the cap.
MECHANISM_DIRECTION_CAP = SAFETY_CAP
# Mechanism-direction check now runs on the top-3 candidates (up from 1) to
# increase coverage without a prohibitive LLM cost increase.  The check is
# the primary gate for the class of errors where a target is shared between
# two diseases that LOOK related but operate via completely unrelated mechanisms.
#
# KNOWN ARCHETYPE — recorded 2026-07 for future reference:
#   GSD1c (glucose-6-phosphate transport, SLC37A4 in ER) scored with GAA as
#   primary target (OT gave a non-zero association score).  Chemist found MIGLITOL
#   (intestinal alpha-glucosidase inhibitor) via ChEMBL GAA activity records.
#   REJECTION REASONING:
#     • GAA is the Pompe disease target (GSD type II, lysosomal glycogen storage).
#       It is NOT the causal gene for GSD1c, which is caused by SLC37A4 deficiency.
#     • MIGLITOL acts on brush-border alpha-glucosidases (MGA/MGAM), not lysosomal GAA.
#     • The Stage 1 scoring ranked (GSD1c, GAA) because: GSD1c has high unmet need
#       (no approved treatment) + GAA has high tractability (many ChEMBL compounds,
#       good pLDDT).  The OT association score for (GSD1c, GAA) was non-zero because
#       both diseases carry "glycogen storage" pathway annotations.
#     • The mechanism_direction check must return DIRECTIONALLY_INCOMPATIBLE for
#       MIGLITOL vs. GSD1c (an intestinal carbohydrate absorption inhibitor does not
#       address a glucose-6-phosphate transporter defect in the ER membrane).
#     • pathway_specificity_note is also set if GAA is discovered as a pathway_neighbor
#       via "Glycogen breakdown (glycogenolysis)" [broad_metabolic tier].
MAX_MECHANISM_DIRECTION_CANDIDATES = 3
# Layer 2 (web-search) only runs on this many top candidates to mirror the
# Boltz validation scope and keep LLM call costs bounded.
MAX_SAFETY_LAYER2_CANDIDATES = 3
# -----------------------------------------------------------------------------


def _normalize(value: Optional[float], vmin: float, vmax: float) -> float:
    """Min-max normalize across the candidate set. If all values are equal,
    map a positive value to 1.0 and a non-positive/None value to 0.0."""
    if value is None:
        return 0.0
    if vmax == vmin:
        return 1.0 if value > 0 else 0.0
    return (value - vmin) / (vmax - vmin)


def _descriptors(smiles: Optional[str]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "molecular_weight": None, "logp": None, "h_bond_donors": None,
        "h_bond_acceptors": None, "tpsa": None, "rotatable_bonds": None,
        "lipinski_violations": None, "veber_pass": None, "valid_structure": False,
    }
    if not smiles:
        return out
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return out
    mw = Descriptors.MolWt(mol)
    logp = Crippen.MolLogP(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    rot = Lipinski.NumRotatableBonds(mol)
    violations = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    out.update({
        "molecular_weight": round(mw, 2),
        "logp": round(logp, 2),
        "h_bond_donors": hbd,
        "h_bond_acceptors": hba,
        "tpsa": round(tpsa, 2),
        "rotatable_bonds": rot,
        "lipinski_violations": int(violations),
        "veber_pass": bool(rot <= 10 and tpsa <= 140),
        "valid_structure": True,
    })
    return out


def run_reviewer(chemist_output: dict[str, Any],
                 biologist_output: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    candidates = chemist_output.get("candidates", [])
    disease = chemist_output.get("target", {}).get("disease_name", "")

    # Target-level PMIDs (shared evidence) for provenance accounting.
    target_pmids = []
    if biologist_output:
        target_pmids = [h["pmid"] for h in biologist_output.get("literature_hits", [])]

    # Normalization ranges across the candidate set.
    pchembls = [c["pchembl_value"] for c in candidates if c.get("pchembl_value") is not None]
    tanimotos = [c.get("tanimoto_score", 0.0) for c in candidates]
    ot_scores = [c.get("ot_association_score", 0.0) for c in candidates]
    p_min, p_max = (min(pchembls), max(pchembls)) if pchembls else (0.0, 0.0)
    t_min, t_max = (min(tanimotos), max(tanimotos)) if tanimotos else (0.0, 0.0)
    o_min, o_max = (min(ot_scores), max(ot_scores)) if ot_scores else (0.0, 0.0)

    counted_sources: set[tuple[str, str]] = set()  # for cross-candidate dedup
    prov_entries: list[dict[str, Any]] = []
    reviewed: list[dict[str, Any]] = []

    for c in candidates:
        desc = _descriptors(c.get("smiles"))
        adverse = get_adverse_events(c["drug_name"])
        trials = check_prior_trials(c["drug_name"], disease)
        no_failed_trial = not trials.get("has_negative_repurposing_result", False)

        n_pchembl = _normalize(c.get("pchembl_value"), p_min, p_max)
        n_tanimoto = _normalize(c.get("tanimoto_score", 0.0), t_min, t_max)
        n_ot = _normalize(c.get("ot_association_score", 0.0), o_min, o_max)
        conf = c.get("confidence_score") or 0

        composite = (
            COMPOSITE_WEIGHTS["pchembl"] * n_pchembl
            + COMPOSITE_WEIGHTS["confidence"] * (conf / 9)
            + COMPOSITE_WEIGHTS["ot_association"] * n_ot
            + COMPOSITE_WEIGHTS["tanimoto"] * n_tanimoto
            + COMPOSITE_WEIGHTS["no_failed_trial"] * (1 if no_failed_trial else 0)
        )

        lipinski_violations = desc.get("lipinski_violations")
        penalty_applied = lipinski_violations is not None and lipinski_violations > 1
        if penalty_applied:
            composite -= LIPINSKI_PENALTY

        # Hard gate: unapproved/experimental compounds are capped below STRONG_MATCH.
        # Drug repurposing requires an established human safety profile from prior
        # regulatory approval. A research compound that merely binds the target is a
        # fundamentally different and weaker finding — it is NOT a repurposing candidate.
        # Cap is set at 0.40, 0.30 below the 0.70 STRONG_MATCH_THRESHOLD, so no
        # combination of other scores can push an unapproved compound past the gate.
        unapproved_cap_applied = False
        if c.get("is_approved_drug") is False:
            composite = min(composite, 0.40)
            unapproved_cap_applied = True

        composite = round(composite, 4)

        # Provenance: collapse repeated source ids (chembl activity ids + pmids).
        candidate_pairs = (
            [("chembl_activity", sid) for sid in c.get("source_activity_ids", [])]
            + [("chembl", sid) for sid in c.get("source_chembl_ids", [])]
            + [("pmid", pid) for pid in target_pmids]
        )
        deduped = provenance.dedupe_pairs(candidate_pairs)
        new_ids, collapsed_ids = [], []
        for pair in deduped:
            key = (pair["source_type"], pair["source_id"])
            if key in counted_sources:
                collapsed_ids.append(pair)
            else:
                counted_sources.add(key)
                new_ids.append(pair)

        for aid in adverse.get("adverse_events", [])[:3]:
            prov_entries.append({
                "source_type": "openfda_event", "source_id": aid["term"],
                "used_by": "reviewer", "context": f"{c['drug_name']} adverse event",
            })
        for t in trials.get("trials", []):
            if t.get("nct_id"):
                prov_entries.append({
                    "source_type": "clinical_trial", "source_id": t["nct_id"],
                    "used_by": "reviewer", "context": f"{c['drug_name']}/{disease} trial",
                })

        reviewed.append({
            "drug_name": c["drug_name"],
            "molecule_chembl_id": c.get("molecule_chembl_id"),
            "target_symbol": c.get("target_symbol"),
            "disease_name": disease,
            "smiles": c.get("smiles"),
            "pchembl_value": c.get("pchembl_value"),
            "confidence_score": c.get("confidence_score"),
            "ot_association_score": c.get("ot_association_score"),
            "tanimoto_score": c.get("tanimoto_score"),
            "most_similar_approved_drug": c.get("most_similar_approved_drug"),
            "is_approved_drug": c.get("is_approved_drug"),
            "rationale": c.get("rationale"),
            "descriptors": desc,
            "lipinski_penalty_applied": penalty_applied,
            "lipinski_note": (
                "Lipinski/Veber are soft developability flags, NOT a hard ADME "
                "prediction."
            ),
            "adverse_events": adverse.get("adverse_events", [])[:10],
            "prior_trial_count": trials.get("trial_count", 0),
            "has_negative_repurposing_result": trials.get("has_negative_repurposing_result", False),
            "score_components": {
                "normalized_pchembl": round(n_pchembl, 4),
                "confidence_term": round(conf / 9, 4),
                "normalized_ot_association": round(n_ot, 4),
                "normalized_tanimoto": round(n_tanimoto, 4),
                "no_failed_trial": 1 if no_failed_trial else 0,
            },
            "composite_score": composite,
            "unapproved_cap_applied": unapproved_cap_applied,
            # DISCLOSURE flag only — passed straight through from the Chemist,
            # never used in the composite. Tells the reviewer the drug's approved
            # indication names a specific mutation (see mutation_disclosure.py).
            "mutation_specificity": c.get("mutation_specificity"),
            # Carry the discovery method (genetic_association,
            # pharmacological_precedent, pharmacological_precedent_via_parent_umbrella,
            # pathway_neighbor) so report writer and validation scripts can record
            # HOW each primary target was surfaced. Without this the field drops here
            # and shows as None in every downstream artifact.
            "target_discovery_method": c.get("target_discovery_method"),
            # status_badge, safety_cap_applied, safety_layer1, safety_layer2 are
            # all set in the post-sort safety-disclosure pass below, after both
            # layers have been evaluated.  Placeholders here:
            "status_badge": None,
            "safety_cap_applied": False,
            "safety_layer1": None,
            "safety_layer2": None,
            "strong_match": composite >= STRONG_MATCH_THRESHOLD,
            # mechanism_direction and mechanism_cap_applied are set in the
            # post-sort mechanism-direction pass below (top-1 only).
            "mechanism_direction": None,
            "mechanism_cap_applied": False,
            "provenance": {
                "counted_once": new_ids,
                "collapsed_as_duplicate": collapsed_ids,
            },
            "source_chembl_ids": c.get("source_chembl_ids", []),
        })

    provenance.log_many(prov_entries)
    reviewed.sort(key=lambda r: r["composite_score"], reverse=True)

    # ── Mechanism-direction check (top-1 only, same scope as Boltz) ───────────
    # Runs AFTER initial composite-score sort so the top candidate is stable.
    # Only DIRECTIONALLY_INCOMPATIBLE triggers a cap; COMPATIBLE and
    # INSUFFICIENT_INFO leave the score unchanged (fail-open, same philosophy
    # as safety Layer 2's NO/UNCLEAR outcomes).
    if reviewed:
        top = reviewed[0]
        _target_sym = top.get("target_symbol") or ""
        _at_info    = get_drug_action_type(top["drug_name"], _target_sym)
        _action_t   = _at_info.get("action_type")
        _moa        = _at_info.get("mechanism_of_action")
        _direction  = check_mechanism_direction(
            top["drug_name"], _target_sym, _action_t, _moa, disease
        )
        top["mechanism_direction"] = _direction
        if _direction.get("incompatible"):
            top["composite_score"]      = min(top["composite_score"], MECHANISM_DIRECTION_CAP)
            top["mechanism_cap_applied"] = True
            top["strong_match"]          = top["composite_score"] >= STRONG_MATCH_THRESHOLD
            reviewed.sort(key=lambda r: r["composite_score"], reverse=True)
        print(
            f"[reviewer] mechanism-direction: {top['drug_name']} / {disease} "
            f"→ {_direction.get('verdict')} "
            f"(action_type={_action_t!r}, cap={'YES' if _direction.get('incompatible') else 'no'})"
        )
    # ── End mechanism-direction pass ──────────────────────────────────────────

    # ── Safety-disclosure pass (Layer 1 + Layer 2) ────────────────────────────
    # Top-K selection for Layer 2 is done BEFORE either layer applies any cap,
    # so both layers evaluate the same pre-cap shortlist independently.
    # Layer 1 (ChEMBL structured) runs on every candidate — cheap, 30-day cache.
    # Layer 2 (Anthropic web search) runs only on top-K strong-match candidates
    # to mirror Boltz validation scope and keep LLM call costs bounded.
    top_k_names: set[str] = set()
    _k_count = 0
    for r in reviewed:
        if r.get("strong_match") and _k_count < MAX_SAFETY_LAYER2_CANDIDATES:
            top_k_names.add(r["drug_name"])
            _k_count += 1

    needs_resort = False
    for r in reviewed:
        drug = r["drug_name"]
        mid = r.get("molecule_chembl_id")

        # Layer 1 — ChEMBL structured withdrawal / black-box check
        layer1 = get_molecule_safety_flags(drug, mid)
        r["safety_layer1"] = layer1

        # Layer 2 — web-search check:
        #   (a) Budget path: drug is in the pre-cap top-K strong-match shortlist.
        #   (b) Redundancy path: Layer 1 had an API error and cannot be trusted —
        #       Layer 2 always runs in this case regardless of the budget cap,
        #       so an L1 outage can never silently skip safety screening.
        l1_error = layer1.get("api_error", False)
        layer2 = web_safety_check(drug) if (drug in top_k_names or l1_error) else None
        r["safety_layer2"] = layer2

        l1_hit = layer1.get("confirmed", False)
        l2_hit = layer2 is not None and layer2.get("confirmed", False)
        safety_triggered = l1_hit or l2_hit

        if safety_triggered:
            r["composite_score"] = min(r["composite_score"], SAFETY_CAP)
            r["safety_cap_applied"] = True
            r["strong_match"] = r["composite_score"] >= STRONG_MATCH_THRESHOLD

            # Badge names every layer that independently confirmed the signal
            layer_parts: list[str] = []
            cite_parts: list[str] = []
            if l1_hit:
                layer_parts.append("ChEMBL structured data")
                cite_parts.append(
                    layer1.get("source_url") or layer1.get("chembl_id") or ""
                )
            if l2_hit:
                layer_parts.append("web search")
                cite_parts.append(
                    layer2.get("citation") or "see safety_layer2.search_summary"
                )
            layer_str = " + ".join(layer_parts)
            cite_str = "; ".join(c for c in cite_parts if c)
            r["status_badge"] = (
                f"WITHDRAWN FOR SAFETY ({layer_str}) — {cite_str}"
            )
            needs_resort = True
        else:
            r["safety_cap_applied"] = False
            # Unapproved-compound badge (existing gate, unchanged)
            r["status_badge"] = (
                "EXPERIMENTAL COMPOUND — NOT YET APPROVED"
                if r.get("unapproved_cap_applied") else None
            )
    # ── End safety-disclosure pass ────────────────────────────────────────────

    if needs_resort:
        reviewed.sort(key=lambda r: r["composite_score"], reverse=True)

    return reviewed


def main() -> None:
    path_in = os.path.join(OUTPUT_DIR, "chemist_output.json")
    if not os.path.exists(path_in):
        print(f"ERROR: {path_in} not found — run python -m agents.chemist first.")
        sys.exit(1)
    with open(path_in, "r", encoding="utf-8") as f:
        chemist_output = json.load(f)

    bio_path = os.path.join(OUTPUT_DIR, "biologist_output.json")
    biologist_output = None
    if os.path.exists(bio_path):
        with open(bio_path, "r", encoding="utf-8") as f:
            biologist_output = json.load(f)

    reviewed = run_reviewer(chemist_output, biologist_output)

    payload = {
        "formula": {
            "composite_weights": COMPOSITE_WEIGHTS,
            "lipinski_penalty": LIPINSKI_PENALTY,
            "strong_match_threshold": STRONG_MATCH_THRESHOLD,
            "normalization": "min-max across the candidate set (equal values -> 1.0 if >0)",
        },
        "n_candidates": len(reviewed),
        "n_strong_matches": sum(1 for r in reviewed if r["strong_match"]),
        "candidates": reviewed,
    }

    path_out = os.path.join(OUTPUT_DIR, "reviewed_candidates.json")
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"[reviewer] {len(reviewed)} candidates scored, "
          f"{payload['n_strong_matches']} STRONG_MATCH (>= {STRONG_MATCH_THRESHOLD})")
    for r in reviewed[:5]:
        flag = "  *STRONG*" if r["strong_match"] else ""
        print(f"  {r['drug_name'][:28]:28s} composite={r['composite_score']}{flag}")
    print(f"[reviewer] wrote {path_out}")


if __name__ == "__main__":
    main()
