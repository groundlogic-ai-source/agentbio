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

from rdkit import Chem, DataStructs
from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
from rdkit.Chem.SaltRemover import SaltRemover

# Singleton salt remover shared with chemist.py logic — used here to deduplicate
# the direction-check candidate shortlist so salt-form duplicates (e.g. VARDENAFIL
# and VARDENAFIL HCl) don't occupy two of the MAX_MECHANISM_DIRECTION_CANDIDATES
# slots, displacing a genuinely distinct third compound from review.
_MDC_SALT_REMOVER = SaltRemover()


def _mdc_desalted_fp(smiles: Optional[str]):
    """Return Morgan desalted fingerprint for direction-check dedup, or None."""
    if not smiles:
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        stripped = _MDC_SALT_REMOVER.StripMol(mol, dontRemoveEverything=True)
        if stripped is None or stripped.GetNumAtoms() == 0:
            stripped = mol
        return rdMolDescriptors.GetMorganFingerprintAsBitVect(stripped, radius=2, nBits=2048)
    except Exception:
        return None

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from data_sources.openfda import get_adverse_events
from data_sources.clinicaltrials import check_prior_trials
from data_sources.chembl import get_molecule_safety_flags, get_drug_action_type
from data_sources.safety_check import web_safety_check
from data_sources.mechanism_direction import check_mechanism_direction

# ---- Auditable scoring constants (edit here to adjust the policy) -------------
COMPOSITE_WEIGHTS: dict[str, float] = {
    "pchembl": 0.30,          # _norm_pchembl(pchembl_value) — fixed range [3.0, 10.0]
    "confidence": 0.20,       # confidence_score / 9
    "ot_association": 0.20,   # ot_association_score direct [0, 1] — no pool normalization
    "tanimoto": 0.15,         # tanimoto_score direct [0, 1] — no pool normalization
    "no_failed_trial": 0.15,  # 1 if no prior failed trial and query succeeded; 0 otherwise
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


# Fixed reference ranges for score normalization.
# Using absolute ranges instead of per-run pool min-max so that a composite
# score means the same thing across different disease cases.
#
# pChEMBL (= -log10 IC50 in mol/L):
#   3.0 → IC50 of 1 mM  (barely detectable, noise floor)
#   10.0 → IC50 of 100 pM (ultra-potent)
#   Values outside this range are clamped to [0, 1].
PCHEMBL_NORM_MIN = 3.0
PCHEMBL_NORM_MAX = 10.0

# Tanimoto similarity (Morgan fingerprint vs approved drugs): already bounded
# [0, 1] by definition — used directly, never pool-normalized.

# Open Targets association score: already bounded [0, 1] by OT's own
# aggregation — used directly, never pool-normalized.


def _norm_pchembl(value: Optional[float]) -> float:
    """Normalize pChEMBL to [0, 1] using fixed pharmacology reference range."""
    if value is None:
        return 0.0
    return max(0.0, min(1.0, (value - PCHEMBL_NORM_MIN) / (PCHEMBL_NORM_MAX - PCHEMBL_NORM_MIN)))


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

    # Normalization:
    #   pChEMBL  → fixed range [3.0, 10.0] (pharmacological reference, run-independent)
    #   Tanimoto → already 0-1 by definition, used directly
    #   OT score → already 0-1 by OT's aggregation, used directly
    # No per-run pool min-max: a composite score now means the same thing
    # across different disease cases.

    counted_sources: set[tuple[str, str]] = set()  # for cross-candidate dedup
    prov_entries: list[dict[str, Any]] = []
    reviewed: list[dict[str, Any]] = []

    for c in candidates:
        desc = _descriptors(c.get("smiles"))
        adverse = get_adverse_events(c["drug_name"])
        trials = check_prior_trials(c["drug_name"], disease)
        # Fail-closed: if the ClinicalTrials API was unreachable, do NOT award
        # the no-failed-trial credit — we cannot verify the claim.
        if trials.get("query_failed"):
            no_failed_trial = False
            print(f"[reviewer] ClinicalTrials query failed for {c['drug_name']} / {disease} "
                  f"— withholding no_failed_trial credit (fail-closed)")
        else:
            no_failed_trial = not trials.get("has_negative_repurposing_result", False)

        n_pchembl = _norm_pchembl(c.get("pchembl_value"))
        # Tanimoto is inherently [0, 1]; use directly without pool normalization.
        n_tanimoto = float(c.get("tanimoto_score") or 0.0)
        # OT association score is inherently [0, 1]; use directly.
        n_ot = float(c.get("ot_association_score") or 0.0)
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
            # Record whether the trials query itself failed (distinct from "found
            # no trials").  When True, no_failed_trial credit was withheld (fail-closed).
            "trials_query_failed": bool(trials.get("query_failed")),
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
            # Carry the candidate's UniProt accession through to structure_validation_node
            # so Boltz always folds the correct protein.  Without this field, the node
            # falls back to the PRIMARY target's UniProt for ALL pathway_neighbor
            # candidates — silently folding the wrong protein for every pathway hit.
            "uniprot_id": c.get("uniprot_id"),
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

    # ── DILI-target whole-pool pre-cap pass ───────────────────────────────────
    # For ABCB11/BSEP and KCNH2/hERG — the two pharmaceutical safety-profiling
    # targets where ANY inhibition is a hepatotoxicity / QT-prolongation liability
    # rather than a therapeutic action — ALL candidates whose ChEMBL mechanism is
    # for a DIFFERENT protein (source="any_mechanism") are pre-capped without an
    # LLM call.  This handles the structural problem where the ENTIRE approved-drug
    # pool for these targets comes from BSEP/hERG safety screens (e.g. BRIC2 with
    # 199 compounds), and the direction-check N-at-a-time window cannot cover all of
    # them.  The shortcut is safe because:
    #   • ABCB11/BSEP inhibition CAUSES cholestatic liver injury — it can never treat
    #     a disease caused by deficient BSEP function.
    #   • KCNH2/hERG blockade CAUSES long-QT arrhythmia — no approved cardiac drug
    #     works by hERG blockade therapeutically.
    # Drugs that genuinely target ABCB11 or KCNH2 (none exist in the current approved
    # pool) would have source="target_specific" or similar and are passed through.
    _AUTO_INCOMPATIBLE_TARGETS: frozenset[str] = frozenset({
        "ABCB11", "BSEP",   # bile salt export pump — BSEP inhibition = cholestasis
        "KCNH2", "HERG",    # hERG K⁺ channel — blockade = QT prolongation / torsades
    })
    _pre_cap_resort = False
    for _cand in reviewed:
        _ts = (_cand.get("target_symbol") or "").upper()
        if _ts not in _AUTO_INCOMPATIBLE_TARGETS:
            continue
        if _cand.get("mechanism_direction") is not None:
            continue  # already checked (shouldn't happen at this stage, but guard)
        _at_pre = get_drug_action_type(_cand["drug_name"], _ts) or {}
        if _at_pre.get("source") != "any_mechanism":
            continue  # has a target-specific mechanism record — let LLM decide
        # Apply automatic INCOMPATIBLE cap (no LLM call)
        _cand["mechanism_direction"] = {
            "verdict": "DIRECTIONALLY_INCOMPATIBLE",
            "incompatible": True,
            "compatible": False,
            "reason": (
                f"{_ts} is a pharmaceutical safety-profiling endpoint: inhibition of "
                f"{_ts} causes DILI/cardiac toxicity (not a therapeutic action). "
                f"This drug's ChEMBL mechanism record is for a different protein "
                f"(source=any_mechanism), confirming it was assayed here for safety "
                f"screening, not therapeutic intent against {_ts}."
            ),
            "action_type_used": _at_pre.get("action_type"),
            "target_symbol_used": _ts,
            "auto_precap": True,
        }
        _cand["composite_score"]       = min(_cand["composite_score"], MECHANISM_DIRECTION_CAP)
        _cand["mechanism_cap_applied"] = True
        _cand["strong_match"]          = _cand["composite_score"] >= STRONG_MATCH_THRESHOLD
        _pre_cap_resort = True

    if _pre_cap_resort:
        reviewed.sort(key=lambda r: r["composite_score"], reverse=True)
        n_precap = sum(1 for c in reviewed if (c.get("mechanism_direction") or {}).get("auto_precap"))
        print(f"[reviewer] DILI-target pre-cap: {n_precap} candidate(s) auto-capped "
              f"(source=any_mechanism on safety-screen target)")
    # ── End DILI-target pre-cap pass ──────────────────────────────────────────

    # ── Mechanism-direction check (top-MAX_MECHANISM_DIRECTION_CANDIDATES) ──────
    # Runs AFTER initial composite-score sort so the top candidates are stable.
    # Only DIRECTIONALLY_INCOMPATIBLE triggers a cap; COMPATIBLE and
    # INSUFFICIENT_INFO leave the score unchanged (fail-open, same philosophy
    # as safety Layer 2's NO/UNCLEAR outcomes).
    #
    # Checks the top-K candidates (not just top-1) because a DILI-screening
    # assay artifact or salt-form-inflated score may place the real dangerous
    # candidate at position #2 or #3.
    # Build direction-check shortlist: up to MAX_MECHANISM_DIRECTION_CANDIDATES
    # chemically DISTINCT candidates (by desalted Morgan fingerprint).
    # Without this, salt-form pairs like VARDENAFIL / VARDENAFIL HCl occupy two
    # of the three slots, displacing a genuinely different third compound.
    _mdc_candidates: list[dict] = []
    _mdc_seen_fps: list = []
    for _cand in reviewed:
        if len(_mdc_candidates) >= MAX_MECHANISM_DIRECTION_CANDIDATES:
            break
        _cand_fp = _mdc_desalted_fp(_cand.get("smiles"))
        _is_dup = False
        if _cand_fp is not None:
            for _seen_fp in _mdc_seen_fps:
                if _seen_fp is not None and DataStructs.TanimotoSimilarity(_cand_fp, _seen_fp) >= 0.99:
                    _is_dup = True
                    break
        if not _is_dup:
            _mdc_candidates.append(_cand)
            _mdc_seen_fps.append(_cand_fp)

    _mdc_needs_resort = False
    for _top in _mdc_candidates:
        _target_sym = _top.get("target_symbol") or ""
        _at_info    = get_drug_action_type(_top["drug_name"], _target_sym)
        _action_t   = _at_info.get("action_type")
        _moa        = _at_info.get("mechanism_of_action")

        # Detect when the mechanism record is for a DIFFERENT protein than the
        # candidate target being evaluated.  get_drug_action_type returns
        # source="any_mechanism" when it could not find a mechanism record that
        # mentions target_symbol — meaning the returned action_type reflects the
        # drug's PRIMARY pharmacology (e.g. verapamil → "BLOCKER / Voltage-gated
        # L-type calcium channel blocker" for CACNA1C, not ABCB11/BSEP).
        # In that case, passing the wrong action_type to the direction check
        # causes the LLM to reason about calcium channels instead of BSEP, and
        # may produce INSUFFICIENT_INFO instead of the correct INCOMPATIBLE verdict.
        # Fix: override with an IC50/Ki-inferred inhibitory label so the LLM
        # reasons about the actual target-specific interaction.
        if _at_info.get("source") == "any_mechanism" and _action_t:
            _action_t = (
                f"INHIBITOR (inferred from IC50/Ki bioactivity assay data; "
                f"ChEMBL primary registered mechanism is '{_action_t} / {_moa}' "
                f"which is for a DIFFERENT protein target — do NOT use this as "
                f"the drug's action on {_target_sym}; instead reason from the "
                f"fact that this drug has IC50/Ki binding activity against "
                f"{_target_sym} in ChEMBL assays, which implies inhibitory interaction)"
            )
        elif _at_info.get("source") == "not_found":
            _action_t = (
                f"INHIBITOR (inferred: no ChEMBL mechanism record found; "
                f"drug has IC50/Ki binding activity against {_target_sym})"
            )

        _direction = check_mechanism_direction(
            _top["drug_name"], _target_sym, _action_t, _moa, disease
        )
        _top["mechanism_direction"] = _direction
        if _direction.get("incompatible"):
            _top["composite_score"]       = min(_top["composite_score"], MECHANISM_DIRECTION_CAP)
            _top["mechanism_cap_applied"] = True
            _top["strong_match"]          = _top["composite_score"] >= STRONG_MATCH_THRESHOLD
            _mdc_needs_resort = True
        print(
            f"[reviewer] mechanism-direction: {_top['drug_name']} / {disease} "
            f"→ {_direction.get('verdict')} "
            f"(action_src={_at_info.get('source')!r}, "
            f"cap={'YES' if _direction.get('incompatible') else 'no'})"
        )

    if _mdc_needs_resort:
        reviewed.sort(key=lambda r: r["composite_score"], reverse=True)

    # ── Post-cap direction-check pass ─────────────────────────────────────────
    # Problem: if the initial top-K candidates ALL get capped (e.g. three
    # BSEP-inhibitor drugs in a BRIC2 run), the list re-sorts and previously
    # lower-ranked candidates rise to the top — but they were never direction-
    # checked.  Those newly promoted candidates may also be DIRECTIONALLY_
    # INCOMPATIBLE (e.g. calcium-channel blockers that are also BSEP safety-
    # screen compounds) and would reach STRONG_MATCH without any gate.
    #
    # Fix: after the re-sort, collect the new top-MAX_MECHANISM_DIRECTION_CANDIDATES
    # STRONG_MATCH candidates that were NOT in the original shortlist and run
    # direction checks on them.  Total LLM calls are bounded at 2×MAX (= 6).
    _mdc_checked_names: set[str] = {c["drug_name"] for c in _mdc_candidates}
    _mdc_second_pass: list[dict] = []
    _mdc_second_fps: list = []
    for _cand in reviewed:
        if len(_mdc_second_pass) >= MAX_MECHANISM_DIRECTION_CANDIDATES:
            break
        if not _cand.get("strong_match"):
            break  # below threshold — no point checking further
        if _cand["drug_name"] in _mdc_checked_names:
            continue  # already checked in first pass
        _cand_fp = _mdc_desalted_fp(_cand.get("smiles"))
        _is_dup = False
        if _cand_fp is not None:
            for _seen_fp in _mdc_seen_fps + _mdc_second_fps:
                if _seen_fp is not None and DataStructs.TanimotoSimilarity(_cand_fp, _seen_fp) >= 0.99:
                    _is_dup = True
                    break
        if not _is_dup:
            _mdc_second_pass.append(_cand)
            _mdc_second_fps.append(_cand_fp)

    _mdc_second_resort = False
    for _top in _mdc_second_pass:
        _target_sym = _top.get("target_symbol") or ""
        _at_info    = get_drug_action_type(_top["drug_name"], _target_sym)
        _action_t   = _at_info.get("action_type")
        _moa        = _at_info.get("mechanism_of_action")
        if _at_info.get("source") == "any_mechanism" and _action_t:
            _action_t = (
                f"INHIBITOR (inferred from IC50/Ki bioactivity assay data; "
                f"ChEMBL primary registered mechanism is '{_action_t} / {_moa}' "
                f"which is for a DIFFERENT protein target — do NOT use this as "
                f"the drug's action on {_target_sym}; instead reason from the "
                f"fact that this drug has IC50/Ki binding activity against "
                f"{_target_sym} in ChEMBL assays, which implies inhibitory interaction)"
            )
        elif _at_info.get("source") == "not_found":
            _action_t = (
                f"INHIBITOR (inferred: no ChEMBL mechanism record found; "
                f"drug has IC50/Ki binding activity against {_target_sym})"
            )
        _direction = check_mechanism_direction(
            _top["drug_name"], _target_sym, _action_t, _moa, disease
        )
        _top["mechanism_direction"] = _direction
        if _direction.get("incompatible"):
            _top["composite_score"]       = min(_top["composite_score"], MECHANISM_DIRECTION_CAP)
            _top["mechanism_cap_applied"] = True
            _top["strong_match"]          = _top["composite_score"] >= STRONG_MATCH_THRESHOLD
            _mdc_second_resort = True
        print(
            f"[reviewer] mechanism-direction (post-cap): {_top['drug_name']} / {disease} "
            f"→ {_direction.get('verdict')} "
            f"(action_src={_at_info.get('source')!r}, "
            f"cap={'YES' if _direction.get('incompatible') else 'no'})"
        )

    if _mdc_second_resort:
        reviewed.sort(key=lambda r: r["composite_score"], reverse=True)
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
            "normalization": (
                f"pChEMBL: fixed range [{PCHEMBL_NORM_MIN}, {PCHEMBL_NORM_MAX}] "
                "(pharmacological reference — run-independent); "
                "Tanimoto: direct [0, 1] — no normalization applied; "
                "OT association: direct [0, 1] — no normalization applied"
            ),
            "pchembl_norm_min": PCHEMBL_NORM_MIN,
            "pchembl_norm_max": PCHEMBL_NORM_MAX,
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
