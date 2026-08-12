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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from data_sources.chembl import get_molecule_safety_flags, get_drug_action_type, get_molecule_data
from data_sources.safety_check import web_safety_check
from data_sources.mechanism_direction import check_mechanism_direction
from data_sources import holdout as _holdout
from data_sources.pubchem import get_compound_data

# ---- Auditable scoring constants (edit here to adjust the policy) -------------
COMPOSITE_WEIGHTS: dict[str, float] = {
    # v2: one modality-aware pharmacology term.  For legacy candidates with no
    # evidence ledger this is reconstructed as 0.6*pChEMBL + 0.4*assay
    # confidence, preserving the old 0.30 + 0.20 contribution exactly.
    "efficacy_evidence": 0.50,
    "ot_association": 0.20,   # ot_association_score direct [0, 1] — no pool normalization
    "tanimoto": 0.15,         # tanimoto_score direct [0, 1] — no pool normalization
    "no_failed_trial": 0.15,  # 1 = looked and found none; 0 = looked and found one;
                              # None = never observed -> term dropped entirely
}
# A small, bounded evidence-resolution term.  It only distinguishes candidates
# whose normalized evidence otherwise lands on the same floor; it is not a
# substitute for target or disease evidence.
QUALIFIED_DIRECTIONAL_BONUS = 0.05
LIPINSKI_PENALTY = 0.25       # flat, soft — subtracted if Lipinski violations > 1
STRONG_MATCH_THRESHOLD = 0.70
# Safety gate: withdrawn / black-box-warning compounds are capped at the same
# ceiling as unapproved compounds so they cannot reach STRONG_MATCH.
SAFETY_CAP = 0.40
# Pool-snapshot safety schema version.  Bump whenever withdrawal/black-box
# verdict semantics change; snapshots stamped with an older version are
# treated as unverified by the audit layer (api/audit.py) until refreshed
# (scripts/refresh_pool_safety.py).
SAFETY_SCHEMA_VERSION = "safety-v2"
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
MAX_REVIEWER_PREFETCH_WORKERS_PER_SOURCE = 8
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


def _candidate_chembl_ids(candidate: dict[str, Any]) -> list[str]:
    """Return only provider-qualified ChEMBL molecule IDs for a candidate."""
    ids: set[str] = set()
    direct = str(candidate.get("molecule_chembl_id") or "").strip()
    if direct.upper().startswith("CHEMBL"):
        ids.add(direct)
    records = (candidate.get("_evidence_ledger") or {}).get("records", [])
    for record in records:
        if str(record.get("provider") or "").lower() != "chembl":
            continue
        molecule_id = str(record.get("molecule_id") or "").strip()
        if molecule_id.upper().startswith("CHEMBL"):
            ids.add(molecule_id)
    return sorted(ids)


def _candidate_is_heldout(candidate: dict[str, Any]) -> bool:
    """Match held-out identity by name, structure, or ChEMBL salt family."""
    if not _holdout.is_active():
        return False
    if _holdout.matches_name(candidate.get("drug_name") or ""):
        return True
    if _holdout.matches_inchikey(candidate.get("inchikey")):
        return True
    return any(
        _holdout.matches_molecule(molecule_id)
        for molecule_id in _candidate_chembl_ids(candidate)
    )


def _modality_flag(
    molecule_type: Optional[str], oral: Optional[bool]
) -> Optional[bool]:
    """True = non-oral biologic (modality caution), False = clear, None = unresolved.

    Mirrors the tested feature_spec all_of[NOT is_small_molecule, NOT is_oral]
    from registry hypothesis run-704c0cb4-H05: missingness propagates, so an
    unknown molecule_type or unknown oral route returns None rather than a
    silent "clear".
    """
    if molecule_type is None or oral is None:
        return None
    return molecule_type != "Small molecule" and not oral


#: Seconds between liveness beats while the prefetch lanes are awaited.
#: Module-level so tests can patch it down.
_PREFETCH_HEARTBEAT_SECONDS = 120

_PREFETCH_LANES = ("openfda-adverse", "clinicaltrials", "pubchem",
                   "chembl-safety", "chembl-molecule")


class _PrefetchLiveness:
    """Time-based liveness beat for the reviewer prefetch.

    ``executor.map`` yields results in INPUT order, so a yield-based
    progress print stays silent when the first pending item is the slow
    one — the exact failure mode this exists to expose.  Lane workers
    complete out of order, so wrapping the lane callables keeps the
    counters moving as long as ANY call finishes; a wedged lane shows up
    as a frozen counter in the next beat.  Observational only: scoring is
    untouched.
    """

    def __init__(self, lanes: tuple, total: int) -> None:
        self._counts = {lane: 0 for lane in lanes}
        self._total = total
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def wrap(self, lane: str, fn):
        def counted(item):
            try:
                return fn(item)
            finally:
                with self._lock:
                    self._counts[lane] += 1
        return counted

    def _beat(self) -> None:
        while not self._stop.wait(_PREFETCH_HEARTBEAT_SECONDS):
            elapsed = time.monotonic() - self._start
            with self._lock:
                summary = ", ".join(
                    f"{lane}={count}/{self._total}"
                    for lane, count in self._counts.items()
                )
            print(f"[reviewer] prefetch alive {elapsed:.0f}s — {summary}",
                  flush=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        self._thread.join(timeout=5)
        return False


def _prefetch_candidate_context(
    candidates: list[dict[str, Any]],
    disease: str,
) -> list[dict[str, Any]]:
    """Fetch four source lanes concurrently and preserve candidate order.

    Each provider gets its own bounded pool.  A slow or rate-limited provider
    therefore cannot serialize the other three lanes, while no provider sees
    more than ``MAX_REVIEWER_PREFETCH_WORKERS_PER_SOURCE`` concurrent calls.
    ``executor.map`` preserves input order and propagates source exceptions.
    """
    if not candidates:
        return []
    total = len(candidates)
    print(f"[reviewer] prefetch start: {total} candidates for {disease}",
          flush=True)
    workers = min(MAX_REVIEWER_PREFETCH_WORKERS_PER_SOURCE, len(candidates))
    drugs = [candidate["drug_name"] for candidate in candidates]
    chembl_ids = [_candidate_chembl_ids(candidate) for candidate in candidates]

    def fetch_trials(item: tuple[str, list[str], str | None]) -> dict[str, Any]:
        drug, ids, inchikey = item
        return check_prior_trials(
            drug,
            disease,
            candidate_chembl_ids=ids,
            candidate_inchikey=inchikey,
        )

    def fetch_safety(item: tuple[str, list[str]]) -> dict[str, Any]:
        drug, ids = item
        return get_molecule_safety_flags(
            drug, ids[0] if ids else None
        )

    with _PrefetchLiveness(_PREFETCH_LANES, total) as live, \
            ThreadPoolExecutor(max_workers=workers) as adverse_pool, \
            ThreadPoolExecutor(max_workers=workers) as trials_pool, \
            ThreadPoolExecutor(max_workers=workers) as pubchem_pool, \
            ThreadPoolExecutor(max_workers=workers) as safety_pool, \
            ThreadPoolExecutor(max_workers=workers) as molecule_pool:
        # Submit every lane before awaiting any lane, so provider latency
        # overlaps across all five independent sources.
        adverse_iter = adverse_pool.map(
            live.wrap("openfda-adverse", get_adverse_events), drugs)
        trials_iter = trials_pool.map(
            live.wrap("clinicaltrials", fetch_trials),
            zip(
                drugs,
                chembl_ids,
                [candidate.get("inchikey") for candidate in candidates],
            ),
        )
        pubchem_iter = pubchem_pool.map(
            live.wrap("pubchem", get_compound_data), drugs)
        safety_iter = safety_pool.map(
            live.wrap("chembl-safety", fetch_safety), zip(drugs, chembl_ids)
        )
        molecule_iter = molecule_pool.map(
            live.wrap("chembl-molecule", get_molecule_data), drugs)
        adverse = list(adverse_iter)
        trials = list(trials_iter)
        pubchem = list(pubchem_iter)
        safety = list(safety_iter)
        molecule = list(molecule_iter)
        print(f"[reviewer] prefetch done: {total} candidates", flush=True)

    return [
        {
            "adverse": adverse[index],
            "trials": trials[index],
            "pubchem": pubchem[index],
            "safety_layer1": safety[index],
            "molecule": molecule[index],
        }
        for index in range(len(candidates))
    ]


#: Discovery methods whose ``ot_association_score`` is a STAMPED CONSTANT
#: (0.90 direct precedent / 0.70 parent-umbrella, see
#: agents/target_selection.py) rather than a measured Open Targets
#: target-disease association.
_PRECEDENT_STAMPED_DISCOVERY = {
    "pharmacological_precedent",
    "pharmacological_precedent_via_parent_umbrella",
}


def _trial_evidence_term(trials: dict[str, Any]) -> Optional[bool]:
    """Trial evidence as an OBSERVATION, or None when it was never observed.

    Three distinct states, previously collapsed into two:

      * observed, no negative repurposing result -> True  (credit earned)
      * observed, a negative repurposing result  -> False (adverse evidence,
        genuinely penalised — this term stays in the denominator)
      * NOT OBSERVED (API failure or holdout redaction) -> None

    The old behaviour returned False for the third state *while keeping the
    term in the denominator*, which scores "we never looked" identically to
    "we looked and found a failed trial".  That is not conservatism, it is a
    measurement error: it silently subtracts a fixed 0.15 of composite from
    exactly those candidates the pipeline is blind to.  Under a benchmark
    holdout the redacted candidate is the drug being measured and no
    competitor is redacted, so the penalty lands only on the drug whose rank
    IS the measurement.  ``_coverage_aware_composite`` now treats None as a
    coverage gap, exactly as an unavailable Tanimoto is already handled.
    """
    if trials.get("query_failed") or trials.get("holdout_redacted"):
        return None
    return not trials.get("has_negative_repurposing_result", False)


def _measured_ot_association(candidate: dict[str, Any]) -> Optional[float]:
    """Measured OT association, or None when the score is a stamped constant.

    Targets surfaced by pharmacological precedent carry NO measured
    target-disease association; target selection stamps a fixed constant
    (0.90 direct, 0.70 parent-umbrella) purely so those rows can be ranked
    during selection.  Feeding that constant into a 0.20-weighted scoring
    term hands every candidate in a precedent lane a flat advantage over
    candidates entering through a genuinely measured genetic association —
    an advantage that has nothing to do with the candidate drug itself, and
    which systematically buries drugs that arrive via the true causal gene.
    Treat a stamped constant as a coverage gap, not as evidence.

    The constant remains fully available for target selection, ordering and
    dossier disclosure; only the composite score stops treating it as a
    measurement.
    """
    method = str(candidate.get("target_discovery_method") or "").strip().lower()
    if method in _PRECEDENT_STAMPED_DISCOVERY:
        return None
    raw = candidate.get("ot_association_score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _coverage_aware_composite(
    efficacy_evidence: float,
    ot_association: Optional[float],
    tanimoto: Optional[float],
    no_failed_trial: Optional[bool],
) -> tuple[float, float]:
    """Score only what was actually OBSERVED, renormalized over its own weight.

    One rule, applied to every optional term: an observation that was never
    made is a COVERAGE GAP, never a measured value.  A term that is None is
    dropped from the numerator AND the denominator; the remaining observed
    terms are renormalized over the weight they actually cover.  A term that
    was measured keeps its measured value, including a genuine 0.0.

      * ``tanimoto`` None      -> no resolvable structure comparison.
        A measured 0.0 is adverse structural evidence and still counts.
      * ``ot_association`` None -> the target carries no MEASURED
        target-disease association (precedent-stamped constant, or absent).
        Scoring a stamped constant as evidence would flatly advantage every
        candidate in that lane regardless of the drug.
      * ``no_failed_trial`` None -> the trial lookup failed or was
        holdout-redacted.  Previously this was forced to 0 while staying in
        the denominator, making "we never looked" cost exactly as much as
        "we looked and found a failed trial" — a fixed 0.15 penalty imposed
        on precisely the candidates the pipeline could not see.  A measured
        failed trial is still False and still penalised.

    This is never a positive credit for missing data: dropping a term leaves
    the candidate scored on its own observed evidence rather than imputing a
    zero it never earned.

    Returns (composite, evidence_weight_coverage).
    """
    numerator = COMPOSITE_WEIGHTS["efficacy_evidence"] * efficacy_evidence
    coverage = COMPOSITE_WEIGHTS["efficacy_evidence"]

    if ot_association is not None:
        numerator += COMPOSITE_WEIGHTS["ot_association"] * ot_association
        coverage += COMPOSITE_WEIGHTS["ot_association"]
    if no_failed_trial is not None:
        numerator += COMPOSITE_WEIGHTS["no_failed_trial"] * (1 if no_failed_trial else 0)
        coverage += COMPOSITE_WEIGHTS["no_failed_trial"]
    if tanimoto is not None:
        numerator += COMPOSITE_WEIGHTS["tanimoto"] * tanimoto
        coverage += COMPOSITE_WEIGHTS["tanimoto"]
    return numerator / coverage, coverage


def _has_qualified_directional_evidence(candidate: dict[str, Any]) -> bool:
    """True when a qualified ledger record states a concrete drug action."""
    directional = {"agonist", "antagonist", "inhibitor", "activator", "modulator"}
    for record in (candidate.get("_evidence_ledger") or {}).get("records", []):
        if str(record.get("qualification_status", "")).lower() != "qualified":
            continue
        action = str(record.get("action", "")).strip().lower()
        direction = str(record.get("direction", "")).strip().lower()
        if action in directional or direction in directional:
            return True
    return False


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
    prefetched = _prefetch_candidate_context(candidates, disease)

    for c, context in zip(candidates, prefetched):
        desc = _descriptors(c.get("smiles"))
        adverse = context["adverse"]
        trials = context["trials"]
        # Fail-closed: unavailable OR holdout-redacted trial evidence cannot
        # establish the absence of a failed prior trial.
        no_failed_trial = _trial_evidence_term(trials)
        _trial_basis = (
            "observed" if no_failed_trial is not None
            else ("holdout_redacted" if trials.get("holdout_redacted")
                  else "query_failed")
        )
        if no_failed_trial is None:
            print(
                f"[reviewer] ClinicalTrials {_trial_basis} for "
                f"{c['drug_name']} / {disease} — trial term dropped from the "
                "composite as a coverage gap (NOT scored as a failed trial)"
            )

        n_pchembl = _norm_pchembl(c.get("pchembl_value"))
        # Keep unavailable similarity distinct from a measured zero.  The
        # latter is adverse structural evidence and must remain 0.0; the former
        # is coverage missingness and is handled by _coverage_aware_composite.
        raw_tanimoto = c.get("tanimoto_score")
        n_tanimoto: Optional[float] = (
            None if raw_tanimoto is None else float(raw_tanimoto)
        )
        # OT association is used directly when it is a real measurement.  A
        # precedent-stamped constant is not a measurement, so it becomes a
        # coverage gap rather than a flat lane-wide score advantage.
        n_ot = _measured_ot_association(c)
        _ot_basis = (
            "measured_open_targets" if n_ot is not None
            else (
                "precedent_stamped_constant"
                if str(c.get("target_discovery_method") or "").strip().lower()
                in _PRECEDENT_STAMPED_DISCOVERY
                else "unavailable"
            )
        )
        conf = c.get("confidence_score") or 0
        legacy_evidence = 0.6 * n_pchembl + 0.4 * (conf / 9)
        ledger_evidence = c.get("efficacy_confidence")
        n_efficacy_evidence = (
            float(ledger_evidence)
            if ledger_evidence is not None
            else legacy_evidence
        )
        n_efficacy_evidence = max(0.0, min(1.0, n_efficacy_evidence))

        composite, evidence_weight_coverage = _coverage_aware_composite(
            n_efficacy_evidence,
            n_ot,
            n_tanimoto,
            no_failed_trial,
        )
        qualified_directional = _has_qualified_directional_evidence(c)
        directional_bonus = QUALIFIED_DIRECTIONAL_BONUS if qualified_directional else 0.0
        composite = min(1.0, composite + directional_bonus)

        lipinski_violations = desc.get("lipinski_violations")
        penalty_applied = lipinski_violations is not None and lipinski_violations > 1
        if penalty_applied:
            composite -= LIPINSKI_PENALTY

        # Pre-cap composite — preserved BEFORE any cap (unapproved / mechanism /
        # DILI / safety) is applied.  All caps land tied candidates on the same
        # floor value; without this secondary sort key, a strong-but-capped
        # candidate is numerically indistinguishable from a weak one at the
        # same floor and the tie-break becomes arbitrary.  Ordering within a
        # capped tier changes nothing about STRONG_MATCH gating.
        pre_cap_score = round(composite, 4)

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

        # Lipophilicity flag: fetch PubChem XLogP (cached) and flag if >= 5.
        # Threshold of 5 matches the Lipinski Rule-of-5 logP boundary and the
        # bisociation benchmark split (run-629a01b9) which found XLogP >= 5
        # associated with 0.426x odds of repurposing success (Fisher p = 3e-9,
        # holdout-confirmed p = 0.009). Disclosure only — does NOT affect scoring.
        HIGH_XLOGP_THRESHOLD = 5.0
        _pc = context["pubchem"]
        _pubchem_xlogp: Optional[float] = _pc.get("xlogp")
        _high_lipophilicity_flag: bool = (
            _pubchem_xlogp is not None and _pubchem_xlogp >= HIGH_XLOGP_THRESHOLD
        )

        # Modality flag: ChEMBL molecule_type + oral route (cached lookup).
        # Mirrors the confirmed registry finding run-704c0cb4-H05 — non-oral
        # biologics have ~0.30x odds of repurposing success (discovery
        # q=4.7e-2, holdout confirmation p=2.8e-4, survives established-
        # maturity adjustment). Disclosure only — does NOT affect scoring.
        # Missingness propagates: unresolved lookups stay None, never
        # silently count as "not flagged".
        _mol = context.get("molecule") or {}
        _molecule_type: Optional[str] = _mol.get("molecule_type")
        _oral_raw = _mol.get("oral")
        _oral: Optional[bool] = (None if _oral_raw is None else bool(_oral_raw))
        _nonoral_biologic_flag: Optional[bool] = _modality_flag(_molecule_type, _oral)

        reviewed.append({
            "drug_name": c["drug_name"],
            "molecule_chembl_id": c.get("molecule_chembl_id"),
            "target_symbol": c.get("target_symbol"),
            "disease_name": disease,
            "smiles": c.get("smiles"),
            "pchembl_value": c.get("pchembl_value"),
            "confidence_score": c.get("confidence_score"),
            "efficacy_confidence": c.get("efficacy_confidence"),
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
            # High-lipophilicity disclosure (XLogP >= 5). Disclosure only — does NOT
            # affect any score. The bisociation analysis (run-629a01b9) found XLogP >= 5
            # is associated with 0.426x odds of repurposing success (p = 3e-9,
            # holdout-confirmed p = 0.009). The direction of effect is empirical and
            # an unresolved incumbency-confound caveat remains (see task-14 for
            # confirmation run).
            "pubchem_xlogp": _pubchem_xlogp,
            "high_lipophilicity_flag": _high_lipophilicity_flag,
            # Modality disclosure (non-oral biologic). Disclosure only — does
            # NOT affect any score. None = lookup unresolved.
            "chembl_molecule_type": _molecule_type,
            "chembl_oral": _oral,
            "nonoral_biologic_flag": _nonoral_biologic_flag,
            "adverse_events": adverse.get("adverse_events", [])[:10],
            "prior_trial_count": trials.get("trial_count", 0),
            "has_negative_repurposing_result": trials.get("has_negative_repurposing_result", False),
            "score_components": {
                "normalized_pchembl": round(n_pchembl, 4),
                "confidence_term": round(conf / 9, 4),
                "efficacy_evidence": round(n_efficacy_evidence, 4),
                "efficacy_evidence_source": (
                    "multisource_ledger"
                    if ledger_evidence is not None
                    else "legacy_pchembl_assay_confidence"
                ),
                "normalized_ot_association": (
                    round(n_ot, 4) if n_ot is not None else None
                ),
                "ot_association_available": n_ot is not None,
                "ot_association_basis": _ot_basis,
                "normalized_tanimoto": (
                    round(n_tanimoto, 4) if n_tanimoto is not None else None
                ),
                "similarity_available": n_tanimoto is not None,
                "evidence_weight_coverage": round(evidence_weight_coverage, 4),
                "no_failed_trial": (
                    None if no_failed_trial is None
                    else (1 if no_failed_trial else 0)
                ),
                "trial_evidence_observed": no_failed_trial is not None,
                "trial_evidence_basis": _trial_basis,
                "qualified_directional": qualified_directional,
                "qualified_directional_bonus": directional_bonus,
            },
            "composite_score": composite,
            "pre_cap_score": pre_cap_score,
            "unapproved_cap_applied": unapproved_cap_applied,
            # Record whether the trials query itself failed (distinct from "found
            # no trials").  When True, no_failed_trial credit was withheld (fail-closed).
            "trials_query_failed": bool(trials.get("query_failed")),
            "trials_holdout_redacted": bool(
                trials.get("holdout_redacted")
            ),
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
            "mechanism_class": c.get("mechanism_class"),
            "therapeutic_role": c.get("therapeutic_role", "disease_modifying"),
            "process_support": c.get("process_support", []),
            "process_source_status": c.get("process_source_status"),
            "process_memberships": c.get("process_memberships", []),
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
            "black_box_advisory": False,
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
            "source_activity_ids": c.get("source_activity_ids", []),
            "source_types": c.get("source_types", []),
            "source_health": c.get("source_health", {}),
            "target_memberships": c.get("target_memberships", []),
            "_evidence_ledger": c.get("_evidence_ledger", {}),
            "_prefetched_safety_layer1": context["safety_layer1"],
        })

    provenance.log_many(prov_entries)
    _sort_reviewed(reviewed)

    # ── DILI-target whole-pool pre-cap pass ───────────────────────────────────
    # For every target in the ICH S7A/S7B pharmaceutical safety-profiling panel,
    # ALL candidates whose ChEMBL mechanism is for a DIFFERENT protein
    # (source="any_mechanism") are pre-capped without an LLM call.  This handles
    # the structural problem where the ENTIRE approved-drug pool for these targets
    # may come from safety screens, and the direction-check N-at-a-time window
    # cannot cover all of them.
    #
    # ICH S7A/S7B panel — inhibition is a toxicity signal, not a therapeutic
    # action, when the ChEMBL record carries source=any_mechanism:
    #   • ABCB11/BSEP  — inhibition → cholestatic liver injury (DILI)
    #   • KCNH2/hERG   — blockade   → QT prolongation / torsades de pointes
    #   • SCN5A        — blockade   → Brugada-pattern / cardiac arrest
    #   • ABCB1/MDR1   — inhibition → multidrug-efflux DDI screening artifact
    #   • ABCC2/MRP2   — inhibition → bile-acid/drug-exporter DILI artifact
    #   • CYP3A4, CYP2D6, CYP2C9, CYP2C19, CYP1A2
    #                  — inhibition → DDI / hepatotoxicity liability artifact
    #
    # Drugs that GENUINELY target any of these proteins carry
    # source="target_specific" or similar and are passed through unchanged.
    # We re-use the canonical set from mechanism_direction.py so both panels
    # stay in sync automatically.
    from data_sources.mechanism_direction import _DILI_SAFETY_SCREEN_TARGETS as _AUTO_INCOMPATIBLE_TARGETS
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
        _sort_reviewed(reviewed)
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
        _is_heldout = _candidate_is_heldout(_top)
        _at_info = (
            {"source": "holdout_redacted", "action_type": None,
             "mechanism_of_action": None}
            if _is_heldout
            else get_drug_action_type(_top["drug_name"], _target_sym)
        )
        _action_t   = _at_info.get("action_type")
        _moa        = _at_info.get("mechanism_of_action")

        # Prefer a qualified target-specific action from the common evidence
        # ledger.  This prevents non-ChEMBL curated interactions from being
        # mislabeled as generic IC50/Ki inhibitors.
        _ledger_records = (_top.get("_evidence_ledger") or {}).get("records", [])
        _ledger_action_record = next(
            (
                rec for rec in _ledger_records
                if rec.get("qualification_status") == "qualified"
                and rec.get("evidence_role") in ("efficacy", "target_link")
                and rec.get("action")
                and (
                    not _target_sym
                    or str(rec.get("target_symbol") or "").upper() == _target_sym.upper()
                )
            ),
            None,
        )
        if _ledger_action_record:
            _action_t = _ledger_action_record.get("action")
            _moa = _ledger_action_record.get("context") or _moa
            _at_info = {
                **_at_info,
                "source": f"evidence_ledger:{_ledger_action_record.get('provider')}",
            }

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
            _top["drug_name"], _target_sym, _action_t, _moa, disease,
            candidate_chembl_ids=_candidate_chembl_ids(_top),
            candidate_inchikey=_top.get("inchikey"),
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
        _sort_reviewed(reviewed)

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
        _is_heldout = _candidate_is_heldout(_top)
        _at_info = (
            {"source": "holdout_redacted", "action_type": None,
             "mechanism_of_action": None}
            if _is_heldout
            else get_drug_action_type(_top["drug_name"], _target_sym)
        )
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
            _top["drug_name"], _target_sym, _action_t, _moa, disease,
            candidate_chembl_ids=_candidate_chembl_ids(_top),
            candidate_inchikey=_top.get("inchikey"),
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
        _sort_reviewed(reviewed)
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
        layer1 = r.pop("_prefetched_safety_layer1")
        r["safety_layer1"] = layer1

        # Layer 2 — web-search check:
        #   (a) Budget path: drug is in the pre-cap top-K strong-match shortlist.
        #   (b) Redundancy path: Layer 1 had an API error and cannot be trusted —
        #       Layer 2 always runs in this case regardless of the budget cap,
        #       so an L1 outage can never silently skip safety screening.
        #   (c) Black-box advisory path: Layer 1 found a boxed warning but did NOT
        #       confirm a market withdrawal (confirmed=False, black_box_advisory=True).
        #       A boxed warning can precede regulatory action; Layer 2 independently
        #       checks whether a post-ChEMBL withdrawal or serious alert exists that
        #       structured data missed.  This path is budget-free: black-box drugs
        #       are rare, so the extra web-search calls are minimal.
        #   (d) Withdrawal-reconciliation path: a structured withdrawn_flag can
        #       be wrong for legacy/garbled records.  Layer 2 independently
        #       reconciles every L1 withdrawal before it applies the hard cap.
        l1_error = layer1.get("api_error", False)
        l1_bbw   = layer1.get("black_box_advisory", False)
        l1_withdrawn = layer1.get("confirmed", False)
        layer2 = web_safety_check(drug) if (
            drug in top_k_names or l1_error or l1_bbw or l1_withdrawn
        ) else None
        r["safety_layer2"] = layer2

        if _reconcile_safety(r, layer1, layer2):
            needs_resort = True
    # ── End safety-disclosure pass ────────────────────────────────────────────

    if needs_resort:
        _sort_reviewed(reviewed)

    return reviewed


def _reconcile_safety(r: dict[str, Any], layer1: dict[str, Any],
                      layer2: Optional[dict[str, Any]]) -> bool:
    """Apply withdrawal / black-box reconciliation to one reviewed candidate.

    Shared by the reviewer safety pass and the pool-safety refresh script so
    badge/cap semantics can never drift between live runs and refreshed
    snapshots.  Returns True when the safety cap fired (caller re-sorts).
    """
    # A lone structured withdrawal signal is retained when the independent
    # check is unavailable/unclear (conservative safety default).  An
    # explicit Layer-2 NO is a source disagreement: disclose it and do not
    # hard-cap until a withdrawal is independently corroborated.
    l1_withdrawn = layer1.get("confirmed", False)
    l1_hit = (
        l1_withdrawn
        and (layer2 is None or layer2.get("verdict") != "NO")
    )
    l2_hit = layer2 is not None and layer2.get("confirmed", False)
    safety_triggered = l1_hit or l2_hit
    if l1_withdrawn and layer2 is not None and layer2.get("verdict") == "NO":
        r["safety_reconciliation"] = {
            "status": "disputed",
            "reason": (
                "ChEMBL structured data reports withdrawn_flag=True, but "
                "the independent web safety check returned WITHDRAWAL: NO. "
                "No hard cap was applied; this conflict requires review."
            ),
            "layer1_source": layer1.get("source_url"),
            "layer2_citation": layer2.get("citation"),
        }
    else:
        r["safety_reconciliation"] = None

    # Black-box advisory: a boxed warning was found (by L1 structured data
    # or by L2's separate BLACK_BOX verdict) but NO withdrawal was
    # confirmed.  Surface as a disclosure note; do NOT apply the hard cap.
    r["black_box_advisory"] = (
        (
            layer1.get("black_box_advisory", False)
            or (layer2 or {}).get("black_box_advisory", False)
        )
        and not safety_triggered
    )

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
            f"WITHDRAWN FROM MARKET ({layer_str}) — {cite_str}"
        )
    else:
        r["safety_cap_applied"] = False
        # Unapproved-compound badge (existing gate, unchanged)
        r["status_badge"] = (
            "EXPERIMENTAL COMPOUND — NOT YET APPROVED"
            if r.get("unapproved_cap_applied") else None
        )
    return safety_triggered


def _sort_reviewed(reviewed: list[dict[str, Any]]) -> None:
    """Sort by composite, breaking cap-floor ties by pre-cap score.

    Every cap (unapproved / mechanism-direction / DILI pre-cap / safety) pins
    candidates to the same floor value.  Sorting capped ties by the composite
    computed BEFORE any cap keeps a genuinely strong-but-capped candidate
    ranked above a weak one at the same floor, without changing which
    candidates pass STRONG_MATCH.
    """
    reviewed.sort(
        key=lambda r: (r["composite_score"], r.get("pre_cap_score") or 0.0),
        reverse=True,
    )


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
