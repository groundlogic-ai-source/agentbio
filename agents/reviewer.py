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
import math
import os
import re
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

from agents.target_selection import (
    OUTPUT_DIR, TRACTABILITY_WEIGHTS, TRACTABILITY_WEIGHTS_OVERRIDDEN)
from agents import provenance
from data_sources.openfda import get_adverse_events
from data_sources.clinicaltrials import check_prior_trials
from data_sources.chembl import (
    get_drug_mechanism_identities_for_audit,
    get_molecule_safety_flags,
    get_drug_action_type,
    get_molecule_data,
)
from data_sources.safety_check import web_safety_check
from data_sources.mechanism_direction import check_mechanism_direction
from data_sources.literature_limitation import (
    VERDICT_NOT_ASSESSED as LITERATURE_NOT_ASSESSED,
    check_literature_limitation,
)
from data_sources import holdout as _holdout
from data_sources.pubchem import get_compound_data
from data_sources.evidence_ledger import qualified_target_chembl_activity_ids
from data_sources.target_family_safety import (
    KCNH_FAMILY_TARGETS,
    assess_target_family_safety,
)
from agents.flagship_readiness import evaluate_candidate_readiness

# ---- Auditable scoring constants (edit here to adjust the policy) -------------
_DEFAULT_COMPOSITE_WEIGHTS: dict[str, float] = {
    # v2: one modality-aware pharmacology term.  For legacy candidates with no
    # evidence ledger this is reconstructed as 0.6*pChEMBL + 0.4*assay
    # confidence, preserving the old 0.30 + 0.20 contribution exactly.
    "efficacy_evidence": 0.50,
    "ot_association": 0.20,   # ot_association_score direct [0, 1] — no pool normalization
    "tanimoto": 0.15,         # tanimoto_score direct [0, 1] — no pool normalization
    "no_failed_trial": 0.15,  # 1 = looked and found none; 0 = looked and found one;
                              # None = never observed -> term dropped entirely
}


def _load_composite_weight_overrides() -> tuple:
    """Optional AGENTBIO_COMPOSITE_WEIGHTS JSON override (self-hosting knob).

    Same contract as target_selection._load_weight_overrides: exact key match,
    numeric values, never raise at import, loud warnings either way. An active
    override is stamped into the reviewer payload via main_graph
    (scoring_config_overridden) so every dossier discloses that its scores are
    not comparable to the frozen benchmark."""
    raw = os.environ.get("AGENTBIO_COMPOSITE_WEIGHTS")
    if not raw:
        return dict(_DEFAULT_COMPOSITE_WEIGHTS), False
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or set(parsed) != set(_DEFAULT_COMPOSITE_WEIGHTS):
            raise ValueError(
                f"must be a JSON object with exactly the keys "
                f"{sorted(_DEFAULT_COMPOSITE_WEIGHTS)}")
        weights = {k: float(v) for k, v in parsed.items()}
        # NaN/inf would silently poison scores; all-zero would raise
        # ZeroDivisionError in _coverage_aware_composite (numerator / coverage).
        if any(not math.isfinite(w) or w < 0 for w in weights.values()):
            raise ValueError("weights must be finite, non-negative numbers")
        if sum(weights.values()) <= 0:
            raise ValueError("at least one weight must be positive")
        # efficacy_evidence is the ONLY always-observed term.  If its weight is
        # zero and every optional observation is unavailable, covered weight is
        # zero and _coverage_aware_composite divides by zero — so a zero here
        # is an invalid configuration, not a policy choice.
        if weights["efficacy_evidence"] <= 0:
            raise ValueError("efficacy_evidence must be positive — it is the "
                             "only always-observed term; a zero weight would "
                             "allow a zero-coverage division")
    except Exception as exc:  # noqa: BLE001
        print(f"[reviewer] WARNING: AGENTBIO_COMPOSITE_WEIGHTS ignored ({exc}); "
              "using default composite weights", flush=True)
        return dict(_DEFAULT_COMPOSITE_WEIGHTS), False
    print(f"[reviewer] WARNING: AGENTBIO_COMPOSITE_WEIGHTS override active — "
          f"composite weights = {weights}. Scores are NOT comparable to the "
          f"frozen benchmark.", flush=True)
    return weights, True


COMPOSITE_WEIGHTS, COMPOSITE_WEIGHTS_OVERRIDDEN = _load_composite_weight_overrides()
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
SAFETY_SCHEMA_VERSION = "safety-v3"
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
# After both bounded passes, the promotable leader may still be unchecked if
# the passes spent their budget capping the candidates ahead of it. Each extra
# pass checks exactly one leader, so this bounds the added LLM calls.
MAX_DIRECTION_LEADER_PASSES = 3
# Layer 2 (web-search) has a hard total call budget. The shortlist prioritizes
# explicit structured safety signals and unresolved top-ranked candidates, so
# a ChEMBL outage cannot fan out one expensive web search per candidate.
MAX_SAFETY_LAYER2_CANDIDATES = 3
# This post-benchmark production gate runs only on candidates that could reach
# paid structure prediction. Unchecked candidates are never structure-eligible.
MAX_LITERATURE_LIMITATION_CANDIDATES = 3
#: Env-overridable so long-running batch contexts (prod study supervisors)
#: can soften egress pressure without touching the API server's default.
def _env_int(name: str, default: int) -> int:
    """Import-time env parsing must NEVER raise: a bad value would break
    `import agents.reviewer` and take down API startup with it."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[reviewer] WARNING: {name}={raw!r} is not an integer — "
              f"using default {default}", flush=True)
        return default


MAX_REVIEWER_PREFETCH_WORKERS_PER_SOURCE = max(
    1, _env_int("AGENTBIO_PREFETCH_WORKERS", 8))
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


def _target_matched_chembl_activity_ids(candidate: dict[str, Any]) -> list[str]:
    """Raw, target-matched ChEMBL activity observations only."""
    return qualified_target_chembl_activity_ids(
        ((candidate.get("_evidence_ledger") or {}).get("records") or []),
        target_symbol=candidate.get("target_symbol") or "",
        target_accession=candidate.get("uniprot_id") or "",
    )


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


#: Seconds between liveness beats while the prefetch lanes are awaited.
#: Module-level so tests can patch it down.
_PREFETCH_HEARTBEAT_SECONDS = 120

_PREFETCH_LANES = ("openfda-adverse", "clinicaltrials", "pubchem",
                   "chembl-safety", "chembl-molecule")

#: Seconds with zero lane progress after which the process self-terminates
#: (0 = never).  Env-overridable; the prod Study B supervisor sets this so
#: a wedged prefetch kills the run and the supervisor restarts it — resume
#: is cheap because every completed lane call is cached.  MUST stay 0 for
#: the API server: the stall handler is os._exit and would kill request
#: serving.
_PREFETCH_STALL_EXIT_SECONDS = max(
    0, _env_int("AGENTBIO_PREFETCH_STALL_EXIT_SECONDS", 0))


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
        self._last_progress = self._start
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def wrap(self, lane: str, fn):
        def counted(item):
            try:
                return fn(item)
            finally:
                with self._lock:
                    self._counts[lane] += 1
                    self._last_progress = time.monotonic()
        return counted

    def _beat(self) -> None:
        while not self._stop.wait(_PREFETCH_HEARTBEAT_SECONDS):
            now = time.monotonic()
            elapsed = now - self._start
            with self._lock:
                summary = ", ".join(
                    f"{lane}={count}/{self._total}"
                    for lane, count in self._counts.items()
                )
                idle = now - self._last_progress
            print(f"[reviewer] prefetch alive {elapsed:.0f}s — {summary}",
                  flush=True)
            if 0 < _PREFETCH_STALL_EXIT_SECONDS < idle:
                print(f"[reviewer] prefetch STALLED: no lane progress for "
                      f"{idle:.0f}s — dumping thread stacks, then "
                      f"self-terminating so the supervisor restarts from "
                      f"the per-call cache", flush=True)
                # Diagnosis-first: the stacks show WHERE the lane workers
                # are actually stuck (network read vs lock acquisition).
                import faulthandler
                faulthandler.dump_traceback()
                os._exit(86)

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


def _should_run_safety_layer2(
    drug: str,
    top_k_names: set[str],
    layer1: dict[str, Any],
) -> bool:
    """Run web safety only when structured safety is unresolved or flagged.

    ChEMBL's observed clear result is already a bounded, cached safety lane.
    Re-querying a regulator-search LLM for every strong candidate caused
    unbounded provider transcripts and rate-limit storms. Layer 2 remains
    mandatory for structured errors and explicit warning/withdrawal signals,
    and remains available for top-K candidates whose ChEMBL identity could not
    be resolved.
    """
    if (
        layer1.get("api_error")
        or layer1.get("black_box_advisory")
        or layer1.get("confirmed")
    ):
        return True
    unresolved_identity = not layer1.get("chembl_id")
    return drug in top_k_names and unresolved_identity


def _safety_layer2_shortlist(
    reviewed: list[dict[str, Any]],
    top_k_names: set[str],
) -> set[str]:
    """Select at most the bounded number of Layer 2 safety calls."""
    selected: set[str] = set()
    for row in reviewed:
        drug = row.get("drug_name")
        layer1 = row.get("_prefetched_safety_layer1") or {}
        if (
            drug
            and _should_run_safety_layer2(drug, top_k_names, layer1)
            and len(selected) < MAX_SAFETY_LAYER2_CANDIDATES
        ):
            selected.add(drug)
    return selected


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

      * trials exist, none negative      -> True  (credit earned: the pair has
        been taken into humans and did not fail there)
      * trials exist, one is negative    -> False (adverse evidence, genuinely
        penalised — this term stays in the denominator)
      * NO trial exists                  -> None  (nothing was tried, so there
        is no trial outcome to credit or penalise)
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

    An empty registry is the same kind of gap (added 2026-09-27). A successful
    query returning zero trials records that nobody has run one, which is not
    the observation "it was taken into humans and did not fail" — yet both
    used to score a flat 1.0. That paid novelty twice: a never-attempted pair
    earned the term for free, and dropping an unscorable Tanimoto then
    renormalized still more weight onto it. Capivasertib/AKT2 reached 0.9539
    that way, above the frozen benchmark's best real result (tretinoin/APL at
    0.806) despite never having been given to anyone. A drug with no trial
    history is untested, not proven-safe, so the term now drops from both
    sides of the sum rather than crediting the absence.
    """
    if trials.get("query_failed") or trials.get("holdout_redacted"):
        return None
    if not trials.get("trial_count"):
        return None
    return not trials.get("has_negative_repurposing_result", False)


def _literature_clinical_trial_rows(
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return mechanically verified, exact-use clinical-trial publications.

    ClinicalTrials.gov is only one registry.  The bounded PubMed literature
    gate can find a completed human study whose registration is outside that
    registry (or whose registration is not recoverable).  Keep that evidence
    separate from the registry rows, but do not let a registry-only zero
    masquerade as "no prior human trial".
    """
    finding = candidate.get("literature_limitation") or {}
    rows: list[dict[str, Any]] = []
    for row in finding.get("evidence", []):
        if (
            not isinstance(row, dict)
            or row.get("mechanically_verified") is not True
            or row.get("exact_applicability") is not True
        ):
            continue
        publication_types = {
            str(value).casefold()
            for value in (row.get("publication_types") or [])
        }
        if any(
            "clinical trial" in value
            or "randomized controlled trial" in value
            for value in publication_types
        ):
            rows.append(row)
    return rows


def _reconcile_literature_trial_evidence(candidate: dict[str, Any]) -> None:
    """Reconcile PubMed human-trial evidence with the prior-trial score.

    The ClinicalTrials.gov lane remains auditable as a registry-specific
    observation.  An exact-use clinical-trial publication found by the
    literature gate is a broader human-trial observation and must prevent the
    positive "no prior failed trial" credit when it reports an applicable
    explicit limitation.
    """
    rows = _literature_clinical_trial_rows(candidate)
    audit = candidate.setdefault("trial_audit", {})
    audit["registry_name"] = "ClinicalTrials.gov"
    audit["registry_query_scope"] = "exact drug+disease"
    audit["literature_trial_count"] = len(rows)
    audit["literature_trial_pmids"] = sorted(
        {str(row.get("pmid")) for row in rows if row.get("pmid") is not None}
    )
    audit["literature_trial_evidence"] = (
        "OBSERVED" if rows else "NOT_OBSERVED"
    )
    negative_rows = [
        row for row in rows
        if str(row.get("exact_use_label") or "").upper()
        == "EXPLICIT_LIMITATION"
    ]
    audit["literature_negative_trial_evidence"] = bool(negative_rows)
    if not negative_rows:
        return

    components = candidate.setdefault("score_components", {})
    if components.get("no_failed_trial") == 0:
        return

    # The literature gate runs after the initial reviewer score. Recompute the
    # complete score from persisted components so the breakdown remains exact,
    # including renormalization, bonuses, penalties, and any already-applied
    # hard caps.
    components["no_failed_trial"] = 0
    components["trial_evidence_observed"] = True
    components["trial_evidence_basis"] = (
        "literature_gate_exact_use_clinical_trial_limitation"
    )
    composite, coverage = _coverage_aware_composite(
        float(components.get("efficacy_evidence") or 0.0),
        components.get("normalized_ot_association"),
        components.get("normalized_tanimoto"),
        False,
    )
    if candidate.get("lipinski_penalty_applied"):
        composite -= LIPINSKI_PENALTY
    composite += float(components.get("qualified_directional_bonus") or 0.0)
    pre_cap_score = round(composite, 4)
    cap = 1.0
    if candidate.get("unapproved_cap_applied"):
        cap = min(cap, 0.4)
    if candidate.get("mechanism_cap_applied"):
        cap = min(cap, MECHANISM_DIRECTION_CAP)
    if candidate.get("safety_cap_applied"):
        cap = min(cap, SAFETY_CAP)
    candidate["evidence_weight_coverage"] = round(coverage, 4)
    candidate["pre_cap_score"] = pre_cap_score
    candidate["composite_score"] = round(min(composite, cap), 4)
    candidate["strong_match"] = (
        candidate["composite_score"] >= STRONG_MATCH_THRESHOLD
    )
    components["evidence_weight_coverage"] = round(coverage, 4)


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


def _known_drug_action(value: Any) -> bool:
    """Whether an action is recorded rather than guessed from assay format."""
    text = str(value or "").strip().lower()
    return bool(text and text not in {"unknown", "none", "n/a"}
                and "inferred" not in text)


def _has_qualified_directional_evidence(candidate: dict[str, Any]) -> bool:
    """Compatibility inspection seam retained for legacy callers/tests.

    This answers only whether a qualified ledger row records a concrete action.
    It does NOT earn the directional bonus; `_auditable_compatible_direction`
    is the stricter scoring gate.
    """
    directional = {"agonist", "antagonist", "inhibitor", "activator", "modulator"}
    for record in (candidate.get("_evidence_ledger") or {}).get("records", []):
        if str(record.get("qualification_status") or "").lower() != "qualified":
            continue
        action = str(record.get("action") or "").strip().lower()
        direction = str(record.get("direction") or "").strip().lower()
        if action in directional or direction in directional:
            return True
    return False


def _auditable_compatible_direction(direction: Any) -> bool:
    """Strict gate for the small directional bonus.

    A compatibility label by itself is not evidence.  The bonus is available
    only after a persisted, reviewable direction result records the actual drug
    action, disease-mechanism retrieval, a nonempty reason, and citations.
    """
    if not isinstance(direction, dict):
        return False
    return (
        direction.get("verdict") == "DIRECTIONALLY_COMPATIBLE"
        and _known_drug_action(direction.get("action_type_used"))
        and bool(str(direction.get("disease_mechanism_summary") or "").strip())
        and bool(str(direction.get("reason") or "").strip())
        and bool(str(direction.get("search_citations") or "").strip())
    )


def _apply_directional_bonus(candidate: dict[str, Any]) -> bool:
    """Apply the bonus once, only for an auditable compatible result."""
    components = candidate.setdefault("score_components", {})
    qualified = _auditable_compatible_direction(
        candidate.get("mechanism_direction")) and (
            _holdout.is_active()
            or candidate.get("target_applicability")
            not in {"PATHWAY_ONLY", "UNKNOWN"}
        )
    old_bonus = float(components.get("qualified_directional_bonus") or 0.0)
    new_bonus = QUALIFIED_DIRECTIONAL_BONUS if qualified else 0.0
    components["qualified_directional"] = qualified
    components["qualified_directional_bonus"] = new_bonus
    if old_bonus == new_bonus:
        return False
    # The initial score contains no directional bonus.  Update the pre-cap
    # score as well, then respect any cap already applied to the final score.
    delta = new_bonus - old_bonus
    candidate["pre_cap_score"] = round(
        float(candidate.get("pre_cap_score") or candidate.get("composite_score") or 0)
        + delta, 4)
    candidate["composite_score"] = round(min(
        float(candidate.get("composite_score") or 0) + delta,
        MECHANISM_DIRECTION_CAP if candidate.get("mechanism_cap_applied") else 1.0,
    ), 4)
    candidate["strong_match"] = (
        candidate["composite_score"] >= STRONG_MATCH_THRESHOLD)
    return True


def _remove_directional_bonus_for_limitation(candidate: dict[str, Any]) -> None:
    """Withdraw a coarse direction bonus without rewriting the underlying score."""
    components = candidate.setdefault("score_components", {})
    old_bonus = float(components.get("qualified_directional_bonus") or 0.0)
    if not old_bonus:
        components["qualified_directional"] = False
        components["qualified_directional_bonus"] = 0.0
        return
    components["qualified_directional"] = False
    components["qualified_directional_bonus"] = 0.0
    pre_cap = max(
        0.0,
        float(candidate.get("pre_cap_score") or 0.0) - old_bonus,
    )
    candidate["pre_cap_score"] = round(pre_cap, 4)
    cap = min(
        MECHANISM_DIRECTION_CAP
        if candidate.get("mechanism_cap_applied") else 1.0,
        SAFETY_CAP if candidate.get("safety_cap_applied") else 1.0,
        SAFETY_CAP if candidate.get("unapproved_cap_applied") else 1.0,
    )
    candidate["composite_score"] = round(min(pre_cap, cap), 4)
    candidate["strong_match"] = (
        candidate["composite_score"] >= STRONG_MATCH_THRESHOLD)


def run_reviewer(
    chemist_output: dict[str, Any],
    biologist_output: Optional[dict[str, Any]] = None,
    biologist_outputs: Optional[list[dict[str, Any]]] = None,
    *,
    flagship_use_case: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    candidates = chemist_output.get("candidates", [])
    disease = chemist_output.get("target", {}).get("disease_name", "")
    bios = biologist_outputs or ([biologist_output] if biologist_output else [])

    def _matched_bio(candidate: dict[str, Any]) -> Optional[dict[str, Any]]:
        symbol = str(candidate.get("target_symbol") or "").upper()
        accession = str(candidate.get("uniprot_id") or "").upper()
        accession_match = next((
            bio for bio in bios
            if accession and str(
                ((bio.get("target") or {}).get("uniprot_id") or "")
            ).upper() == accession
        ), None)
        symbol_match = next((
            bio for bio in bios
            if symbol and str(
                ((bio.get("target") or {}).get("target_symbol") or "")
            ).upper() == symbol
        ), None)
        return accession_match or symbol_match

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
        matched_bio = _matched_bio(c)
        target_pmids = [
            h["pmid"] for h in (matched_bio or {}).get("literature_hits", [])
            if h.get("pmid") is not None
        ]
        desc = _descriptors(c.get("smiles"))
        adverse = context["adverse"]
        trials = context["trials"]
        # Fail-closed: unavailable OR holdout-redacted trial evidence cannot
        # establish the absence of a failed prior trial.
        no_failed_trial = _trial_evidence_term(trials)
        # Three distinct reasons the term can be absent. Collapsing them into
        # "query_failed" reported a healthy registry as broken once an empty
        # result started returning None.
        _trial_basis = (
            "observed" if no_failed_trial is not None
            else "holdout_redacted" if trials.get("holdout_redacted")
            else "query_failed" if trials.get("query_failed")
            else "no_registered_trial"
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
        # A ledger action alone does not earn a directional bonus.  Compatibility
        # must be established later by the auditable direction check.
        qualified_directional = False
        directional_bonus = 0.0

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
            [("chembl_activity", sid)
             for sid in _target_matched_chembl_activity_ids(c)]
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
        # Threshold of 5 is Lipinski's Rule of Five (Lipinski et al., 1997,
        # Adv. Drug Deliv. Rev. 23:3-25): LogP > 5 is one of four criteria
        # historically associated with poor oral absorption/permeability.
        # Disclosure only — does NOT affect scoring.
        HIGH_XLOGP_THRESHOLD = 5.0
        _pc = context["pubchem"]
        _pubchem_xlogp: Optional[float] = _pc.get("xlogp")
        _high_lipophilicity_flag: bool = (
            _pubchem_xlogp is not None and _pubchem_xlogp >= HIGH_XLOGP_THRESHOLD
        )

        # Molecule identity (ChEMBL molecule_type + oral route, cached lookup).
        # No longer paired with a modality caution flag — the caution was
        # specifically the removed research module's registry finding
        # run-704c0cb4-H05 (a claim the app can no longer independently
        # re-verify now that the registry is gone), not an independent
        # judgment. Removed 2026-09-21, not just its UI surface.
        _mol = context.get("molecule") or {}
        _molecule_type: Optional[str] = _mol.get("molecule_type")
        _oral_raw = _mol.get("oral")
        _oral: Optional[bool] = (None if _oral_raw is None else bool(_oral_raw))

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
            # High-lipophilicity disclosure (XLogP >= 5), per Lipinski's Rule of
            # Five (Lipinski et al., 1997, Adv. Drug Deliv. Rev. 23:3-25).
            # Disclosure only — does NOT affect any score.
            "pubchem_xlogp": _pubchem_xlogp,
            "high_lipophilicity_flag": _high_lipophilicity_flag,
            "chembl_molecule_type": _molecule_type,
            "chembl_oral": _oral,
            "adverse_events": adverse.get("adverse_events", [])[:10],
            "prior_trial_count": trials.get("trial_count", 0),
            "has_negative_repurposing_result": trials.get("has_negative_repurposing_result", False),
            # Keep the individual, already-retrieved trial rows.  The former
            # count-only handoff made a "no prior failure" score impossible to
            # audit in a dossier without making a second network request.
            "trial_audit": {
                "query_status": (
                    "UNKNOWN" if trials.get("query_failed")
                    else ("REDACTED" if trials.get("holdout_redacted") else "OBSERVED")
                ),
                "trials": trials.get("trials", []),
                "trial_count": (
                    None if trials.get("query_failed") or trials.get("holdout_redacted")
                    else trials.get("trial_count", 0)
                ),
                "negative_repurposing_result": (
                    None if trials.get("query_failed") or trials.get("holdout_redacted")
                    else trials.get("has_negative_repurposing_result", False)
                ),
            },
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
            # This is a ReviewerCandidate handoff field, not only a score
            # component.  Keep both representations for existing consumers.
            "evidence_weight_coverage": round(evidence_weight_coverage, 4),
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
            "target_applicability": _target_applicability(c),
            # Causal-anchor tier (see _target_tier).  Disclosure + rank-only
            # demotion; never a score change.
            "target_tier": _target_tier(c.get("target_discovery_method")),
            "exploratory_rank_demoted": False,
            "causal_anchor": None,
            # How approval was positively established for this candidate
            # ("unknown" means it was NOT established — see the approval gate).
            "approval_basis": c.get("approval_basis"),
            "approval_evidence_providers": c.get("approval_evidence_providers", []),
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
            # Populated by the post-benchmark literature limitation pass. A
            # candidate must be assessed and cleared before paid validation.
            "literature_limitation": None,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": False,
            "externally_prioritizable": False,
            "availability_gate": {
                "status": "UNKNOWN",
                "blocks_prioritization": False,
                "reason": "Global active-ingredient availability was not established.",
            },
            "candidate_source_coverage": _candidate_source_coverage(
                chemist_output.get("source_status"), c),
            "exclusion_reasons": [],
            "provenance": {
                "counted_once": new_ids,
                "collapsed_as_duplicate": collapsed_ids,
            },
            "source_chembl_ids": c.get("source_chembl_ids", []),
            # Preserve only real target-matched ChEMBL activity identities.
            # Generic provider metadata must never cross the reviewer handoff
            # under the activity-id label.
            "source_activity_ids": _target_matched_chembl_activity_ids(c),
            "source_types": c.get("source_types", []),
            "source_health": c.get("source_health", {}),
            "target_memberships": c.get("target_memberships", []),
            "canonical_compound_identity": c.get("canonical_compound_identity"),
            "compound_identity_mode": c.get("compound_identity_mode"),
            "compound_aliases": c.get("compound_aliases", []),
            "parent_active_moiety_equivalence": c.get(
                "parent_active_moiety_equivalence"),
            "_evidence_ledger": c.get("_evidence_ledger", {}),
            "_prefetched_safety_layer1": context["safety_layer1"],
        })

    provenance.log_many(prov_entries)
    _rank_reviewed(reviewed)

    # ── Target-family safety-liability disclosure pass ───────────────────────
    # A related-protein mechanism can be clinically important even when the
    # pursued target assay looks directionally compatible.  This is a
    # disclosure-only check: a ChEMBL mechanism identity does not establish
    # potency, selectivity, exposure, or a clinical safety verdict.
    for _family_candidate in reviewed:
        _family_target = str(
            _family_candidate.get("target_symbol") or "").upper()
        if _family_target not in KCNH_FAMILY_TARGETS:
            _family_candidate["target_family_safety_liability"] = {
                "schema_version": "target-family-safety-v1",
                "status": "NOT_APPLICABLE",
                "pursued_target": _family_target,
                "liability_target": None,
                "relationship": None,
                "evidence": [],
                "disclosure_only": True,
                "score_effect": "none",
            }
            continue
        if _holdout.is_active():
            _family_candidate["target_family_safety_liability"] = {
                "schema_version": "target-family-safety-v1",
                "status": "REDACTED",
                "pursued_target": _family_target,
                "liability_target": None,
                "relationship": "Held-out pharmacology is redacted.",
                "evidence": [],
                "disclosure_only": True,
                "score_effect": "none",
            }
            continue
        _family_ids = _candidate_chembl_ids(_family_candidate)
        _family_mechanisms = get_drug_mechanism_identities_for_audit(
            _family_candidate["drug_name"],
            _family_ids[0] if _family_ids else None,
        )
        _family_candidate["target_family_safety_liability"] = (
            assess_target_family_safety(
                _family_target,
                mechanism_envelope=_family_mechanisms,
                ledger_records=(
                    _family_candidate.get("_evidence_ledger") or {}
                ).get("records", []),
            )
        )
    # ── End target-family safety-liability disclosure pass ───────────────────

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
        _rank_reviewed(reviewed)
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
        _direction, _at_info = _direction_check_candidate(_top, disease)
        # A direction label is bonus-eligible only when its complete,
        # citation-bearing rationale is persisted.  This is deliberately after
        # the check, never inferred from a ledger action row.
        if _apply_directional_bonus(_top):
            _mdc_needs_resort = True
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
        _rank_reviewed(reviewed)

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
    _mdc_second_pass = _postcap_direction_shortlist(
        reviewed, _mdc_checked_names, _mdc_seen_fps,
    )

    _mdc_second_resort = False
    for _top in _mdc_second_pass:
        _direction, _at_info = _direction_check_candidate(_top, disease)
        if _apply_directional_bonus(_top):
            _mdc_second_resort = True
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
        _rank_reviewed(reviewed)

    # ── Leader-convergence direction pass (post-benchmark production policy) ──
    # Both bounded passes spend their budget on the candidates they cap, and
    # every cap re-sorts the list. A pool whose entire head is directionally
    # incompatible therefore exhausts the budget capping it and leaves the lead
    # to a candidate no pass ever reached: the NR3C2 run capped six steroid
    # agonists — progesterone, dexamethasone, prednisolone, spironolactone and
    # both desoxycorticosterone esters — and then promoted a seventh compound
    # that was never direction-checked at all. The hole opens precisely because
    # the gate is working, and it opens onto the one candidate that matters.
    #
    # So keep checking the promotable leader until it has been checked, bounded
    # by MAX_DIRECTION_LEADER_PASSES. Each iteration caps at most one candidate
    # and therefore strictly shortens the list of uncapped leaders, so the loop
    # converges.
    if not _holdout.is_active():
        for _ in range(MAX_DIRECTION_LEADER_PASSES):
            _leader = _unchecked_direction_leader(reviewed)
            if _leader is None:
                break
            _direction, _at_info = _direction_check_candidate(_leader, disease)
            _leader_resort = _apply_directional_bonus(_leader)
            if _direction.get("incompatible"):
                _leader["composite_score"] = min(
                    _leader["composite_score"], MECHANISM_DIRECTION_CAP)
                _leader["mechanism_cap_applied"] = True
                _leader["strong_match"] = (
                    _leader["composite_score"] >= STRONG_MATCH_THRESHOLD)
                _leader_resort = True
            print(
                f"[reviewer] mechanism-direction (leader): "
                f"{_leader['drug_name']} / {disease} "
                f"→ {_direction.get('verdict')} "
                f"(action_src={_at_info.get('source')!r}, "
                f"cap={'YES' if _direction.get('incompatible') else 'no'})"
            )
            if _leader_resort:
                _rank_reviewed(reviewed)
    # ── End mechanism-direction pass ──────────────────────────────────────────

    # ── Safety-disclosure pass (Layer 1 + Layer 2) ────────────────────────────
    # Top-K selection for Layer 2 is done BEFORE either layer applies any cap,
    # so both layers evaluate the same pre-cap shortlist independently.
    # Layer 1 (ChEMBL structured) runs on every candidate — cheap, 30-day cache.
    # Layer 2 (Anthropic web search) is selected from the pre-cap shortlist,
    # with a hard total call budget so source outages cannot multiply cost.
    top_k_names: set[str] = set()
    _k_count = 0
    for r in reviewed:
        if r.get("strong_match") and _k_count < MAX_SAFETY_LAYER2_CANDIDATES:
            top_k_names.add(r["drug_name"])
            _k_count += 1

    layer2_names = _safety_layer2_shortlist(reviewed, top_k_names)
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
        layer2 = web_safety_check(drug) if drug in layer2_names else None
        r["safety_layer2"] = layer2

        if _reconcile_safety(r, layer1, layer2):
            needs_resort = True
        r["availability_gate"] = _availability_gate(layer1, layer2)
    # ── End safety-disclosure pass ────────────────────────────────────────────

    if needs_resort:
        _rank_reviewed(reviewed)

    # ── Literature limitation gate (post-benchmark production policy) ─────────
    # This is deliberately separate from mechanism direction: Timothy syndrome
    # showed that plausible target-level direction can coexist with an explicit
    # disease/class-level therapeutic limitation. The bounded shortlist is the
    # only set authorized to proceed to paid structure validation.
    if _holdout.is_active():
        # Frozen/holdout studies predate this gate. Do not query the held-out
        # candidate or change their ranking semantics post hoc.
        for r in reviewed:
            r["literature_limitation"] = {
                "schema_version": "literature-limitation-v2",
                "verdict": LITERATURE_NOT_ASSESSED,
                "source_status": "NOT_ASSESSED_BENCHMARK_HOLDOUT",
                "blocked": False,
                "gate_cleared": True,
                "reason": (
                    "Post-benchmark production gate was not applied to this "
                    "holdout study; frozen benchmark semantics are unchanged."
                ),
                "evidence": [],
                "post_benchmark_production_gate": True,
            }
            r["literature_limitation_gate_cleared"] = True
            r["externally_prioritizable"] = bool(
                r.get("strong_match")
                and _applicability_allows_prioritization(r)
                and not r["availability_gate"]["blocks_prioritization"]
                and r["candidate_source_coverage"]["complete"]
            )
    else:
        shortlist = reviewed[:MAX_LITERATURE_LIMITATION_CANDIDATES]
        for r in shortlist:
            direction = r.get("mechanism_direction") or {}
            action_type = direction.get("action_type_used")
            mechanism = direction.get("mechanism_of_action_used")
            if not action_type and not mechanism:
                action_info = get_drug_action_type(
                    r["drug_name"], r.get("target_symbol") or "") or {}
                action_type = action_info.get("action_type")
                mechanism = action_info.get("mechanism_of_action")
            intended_use = (
                "cardiac electrophysiology in TS1 CACNA1C p.G406R/exon 8A"
                if "timothy syndrome" in disease.casefold()
                and str(r.get("target_symbol") or "").upper() == "CACNA1C"
                else f"treatment of {disease}"
            )
            limitation = check_literature_limitation(
                r["drug_name"],
                disease,
                r.get("target_symbol") or "",
                action_type,
                mechanism,
                intended_use,
                drug_aliases=r.get("compound_aliases") or [],
                hypothesis_context=flagship_use_case,
            )
            blocked = bool(limitation.get("blocked"))
            cleared = bool(limitation.get("gate_cleared"))
            r["literature_limitation"] = limitation
            r["literature_limitation_blocked"] = blocked
            r["literature_limitation_gate_cleared"] = cleared
            _reconcile_literature_trial_evidence(r)
            if blocked:
                _remove_directional_bonus_for_limitation(r)
            r["externally_prioritizable"] = bool(
                r.get("strong_match") and cleared and not blocked
                and _applicability_allows_prioritization(r)
                and not r["availability_gate"]["blocks_prioritization"]
                and r["candidate_source_coverage"]["complete"])
            print(
                f"[reviewer] literature-limitation: {r['drug_name']} / {disease} "
                f"→ {limitation.get('verdict')} "
                f"(block={'YES' if blocked else 'no'}, "
                f"paid-gate={'clear' if cleared and not blocked else 'closed'})"
            )
        for r in reviewed[MAX_LITERATURE_LIMITATION_CANDIDATES:]:
            r["literature_limitation"] = {
                "schema_version": "literature-limitation-v4",
                "verdict": LITERATURE_NOT_ASSESSED,
                "source_status": "NOT_ASSESSED_OUTSIDE_BOUNDED_SHORTLIST",
                "blocked": False,
                "gate_cleared": False,
                "reason": (
                    "Not assessed because this candidate was outside the bounded "
                    "pre-structure shortlist; it is not authorized for paid validation."
                ),
                "evidence": [],
                "pharmacology_context": {
                    "status": "NOT_REQUESTED",
                    "queries": [],
                    "records_screened": 0,
                    "evidence": [],
                    "disclosure_only": True,
                },
                "post_benchmark_production_gate": True,
            }
            r["literature_limitation_gate_cleared"] = False
            r["externally_prioritizable"] = False

    # Re-run the deterministic eligibility projection after every score cap and
    # disclosure gate. Every row keeps explicit reasons, including rows outside
    # the paid-validation shortlist.
    _rank_reviewed(reviewed)
    for r in reviewed:
        if _holdout.is_active():
            # Compatibility projection only. Production gates are intentionally
            # absent from frozen benchmark eligibility/ranking calculations.
            r["exclusion_reasons"] = []
            r["paid_validation_eligible"] = bool(
                r.get("strong_match")
                and r.get("literature_limitation_gate_cleared") is True
            )
            r["headline_eligible"] = r["paid_validation_eligible"]
            r["externally_prioritizable"] = r["paid_validation_eligible"]
            continue
        reasons: list[str] = []
        if r.get("is_approved_drug") is not True:
            reasons.append("approval_not_established")
        if not r["candidate_source_coverage"]["complete"]:
            reasons.append("candidate_source_coverage_incomplete")
        if not _applicability_allows_prioritization(r):
            reasons.append(
                "target_applicability_" +
                str(r.get("target_applicability") or "UNKNOWN").lower())
        if (r.get("availability_gate") or {}).get("blocks_prioritization"):
            reasons.append("globally_unavailable_active_ingredient")
        if r.get("literature_limitation_blocked"):
            reasons.append("applicable_literature_limitation")
        if r.get("literature_limitation_gate_cleared") is not True:
            reasons.append("literature_gate_not_cleared")
        if not r.get("strong_match"):
            reasons.append("below_strong_match_threshold")
        r["exclusion_reasons"] = reasons
        r["paid_validation_eligible"] = not reasons
        r["headline_eligible"] = bool(
            r["paid_validation_eligible"]
            and _applicability_allows_prioritization(r))
        r["externally_prioritizable"] = r["paid_validation_eligible"]

    # This is a pure, post-gate view of existing records.  It deliberately runs
    # after both safety and mechanism passes so the dossier cannot describe a
    # pre-gate verdict as its final scientific readiness.
    for r in reviewed:
        r["dossier_evidence_contract"] = _build_dossier_evidence_contract(
            r, _matched_bio(r), reviewed)
        r["flagship_readiness"] = evaluate_candidate_readiness(
            r,
            r["dossier_evidence_contract"],
            flagship_use_case=flagship_use_case,
        )
        r["flagship_use_case"] = r["flagship_readiness"]["flagship_use_case"]
        r["dossier_evidence_contract"]["flagship_readiness"] = (
            r["flagship_readiness"]
        )
    return reviewed


def _build_dossier_evidence_contract(
    candidate: dict[str, Any],
    biologist_output: Optional[dict[str, Any]],
    reviewed_pool: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create a versioned, no-new-facts dossier handoff.

    ``UNKNOWN`` is intentional wire data, not a cosmetic fallback: consumers
    can distinguish an unmeasured property from a negative observation.
    """
    unknown = "UNKNOWN"
    direct_assay = any(
        str(record.get("source_type") or "") == "bioactivity_assay"
        and str(record.get(
            "target_evidence_scope") or "target_qualified"
        ) == "target_qualified"
        and str(record.get("qualification_status") or "").lower() == "qualified"
        and str(record.get("target_species") or "").lower() == "homo sapiens"
        and (
            str(record.get("target_symbol") or "").upper()
            == str(candidate.get("target_symbol") or "").upper()
            or (
                candidate.get("uniprot_id")
                and str(record.get("target_accession") or "").upper()
                == str(candidate.get("uniprot_id") or "").upper()
            )
        )
        for record in (candidate.get("_evidence_ledger") or {}).get("records", [])
    )
    caps = [name for name, hit in (
        ("unapproved", candidate.get("unapproved_cap_applied")),
        ("mechanism_direction", candidate.get("mechanism_cap_applied")),
        ("safety", candidate.get("safety_cap_applied")),
        ("known_literature_limitation",
         candidate.get("literature_limitation_blocked")),
    ) if hit]
    bio_identity = (biologist_output or {}).get("target") or {}
    bio_target = str(bio_identity.get("target_symbol") or "").upper()
    candidate_target = str(candidate.get("target_symbol") or "").upper()
    bio_accession = str(bio_identity.get("uniprot_id") or "").upper()
    candidate_accession = str(candidate.get("uniprot_id") or "").upper()
    target_matches = bool(
        (candidate_accession and bio_accession == candidate_accession)
        or (candidate_target and bio_target == candidate_target)
    )
    approved = (
        (biologist_output or {}).get("druggability_context", {}).get(
            "approved_drugs", [])
        if target_matches else []
    )
    candidate_name = str(candidate.get("drug_name") or "").strip().casefold()
    candidate_ids = {
        str(value).strip().casefold()
        for value in (
            candidate.get("molecule_chembl_id"),
            candidate.get("parent_chembl_id"),
            *((candidate.get("source_chembl_ids") or [])),
            *((candidate.get("source_molecule_chembl_ids") or [])),
        )
        if value
    }
    approved = [
        row for row in approved
        if str(row.get("name") or "").strip().casefold() != candidate_name
        and not ({
            str(value).strip().casefold()
            for value in (
                row.get("molecule_chembl_id"),
                row.get("parent_chembl_id"),
                *((row.get("source_molecule_chembl_ids") or [])),
            )
            if value
        } & candidate_ids)
    ]
    # One comparator identity has one explanatory row.  A drug that appears in
    # both the approved-target list and review pool is not independent support.
    seen_comparator_names: set[str] = set()
    pool_comparators = [
        {
            "drug_name": row.get("drug_name"),
            "molecule_chembl_id": row.get("molecule_chembl_id"),
            "parent_chembl_id": row.get("parent_chembl_id"),
            "active_moiety_id": row.get("active_moiety_id"),
            "source_molecule_chembl_ids": row.get(
                "source_molecule_chembl_ids", []),
            "approval_basis": row.get("approval_basis", unknown),
            "relationship": "same_target_context_only",
        }
        for row in reviewed_pool
        if (str(row.get("drug_name") or "").casefold()
            != str(candidate.get("drug_name") or "").casefold())
        and row.get("target_symbol") == candidate.get("target_symbol")
        and not (str(row.get("drug_name") or "").casefold()
                 in seen_comparator_names
                 or seen_comparator_names.add(
                     str(row.get("drug_name") or "").casefold()))
    ][:10]
    timothy_scope = None
    if ("timothy syndrome" in str(candidate.get("disease_name") or "").casefold()
            and candidate_target == "CACNA1C"):
        timothy_scope = {
            "proposed_variant_scope": "TS1 CACNA1C p.G406R",
            "proposed_exon_scope": "8A",
            "clinical_scope": "cardiac electrophysiology only",
            "genotype_confirmation_status": "UNCONFIRMED_BY_PIPELINE",
            "status": "PROPOSED_SCOPE_AWAITING_HUMAN_CONFIRMATION",
            "scope_basis": (
                "flagship review framing; canonical disease input alone does "
                "not establish that a specific case is TS1 or carries p.G406R"
            ),
            "required_tests": [
                "mutation-specific CACNA1C channel electrophysiology",
                "TS1 p.G406R exon 8A patient-derived or engineered iPSC-cardiomyocyte testing",
            ],
            "safety_exposure_plan": [
                "cardiac rhythm and QT/QTc monitoring",
                "blood-pressure and heart-rate monitoring",
                "exposure/PK assessment at a tolerable dose",
            ],
        }
    production_v2 = bool(
        not _holdout.is_active()
        and candidate.get("target_applicability")
        and candidate.get("candidate_source_coverage") is not None
        and candidate.get("paid_validation_eligible") is not None
    )
    exact_literature_rows = [
        row for row in (candidate.get("literature_limitation") or {}).get(
            "evidence", [])
        if row.get("exact_applicability") is True
    ]
    has_clinical_support = any(
        row.get("exact_use_label") == "APPLICABLE_SUPPORT"
        and row.get("evidence_level") == "case_report_clinical"
        for row in exact_literature_rows
    )
    has_clinical_limitation = any(
        row.get("exact_use_label") == "EXPLICIT_LIMITATION"
        and (
            any(
                "clinical trial" in str(value).casefold()
                or "randomized controlled trial" in str(value).casefold()
                for value in (row.get("publication_types") or [])
            )
            or row.get("evidence_level") == "case_report_clinical"
        )
        for row in exact_literature_rows
    )
    clinical_efficacy_state = (
        "MIXED_CONFLICTING"
        if has_clinical_support and has_clinical_limitation
        else ("OBSERVED_SUPPORT" if has_clinical_support else unknown)
    )

    return {
        "contract_version": (
            "flagship-dossier-evidence-v2"
            if production_v2 else "flagship-dossier-evidence-v1"
        ),
        "unknown_state": unknown,
        "evidence_stage_verdict": (
            "PRIORITIZED_HYPOTHESIS"
            if candidate.get("externally_prioritizable",
                             candidate.get("strong_match"))
            else "NOT_PRIORITIZED"
        ),
        "scientific_readiness": {
            "status": (
                "NOT_PRIORITIZABLE_KNOWN_LITERATURE_LIMITATION"
                if candidate.get("literature_limitation_blocked")
                else "HYPOTHESIS_FOR_QUALIFIED_REVIEW"
            ),
            "qualified_human_target_assay_evidence": (
                "OBSERVED" if direct_assay else unknown
            ),
            "mutation_specific_evidence": unknown,
            "disease_model_evidence": (
                "OBSERVED_SUPPORT"
                if any(row.get("evidence_level") == "disease_model"
                       for row in _observed_literature_support(candidate))
                else unknown
            ),
            "clinical_efficacy_evidence": clinical_efficacy_state,
            "observed_support": _observed_literature_support(candidate),
            "structure_prediction": (
                "NOT_YET_AVAILABLE"  # structure stage augments the rendered view
            ),
            "blocking_gates": caps,
        },
        "literature_limitation": candidate.get("literature_limitation") or {
            "schema_version": "literature-limitation-v4",
            "verdict": LITERATURE_NOT_ASSESSED,
            "source_status": "NOT_ASSESSED",
            "blocked": False,
            "gate_cleared": False,
            "evidence": [],
        },
        "disease_mechanism_context": {
            "disease_name": candidate.get("disease_name", unknown),
            "target_symbol": candidate.get("target_symbol", unknown),
            "target_discovery_method": candidate.get(
                "target_discovery_method", unknown),
            "target_applicability": candidate.get(
                "target_applicability", "UNKNOWN"),
            "therapeutic_role": candidate.get("therapeutic_role", unknown),
            "mechanism_class": candidate.get("mechanism_class", unknown),
            "process_support": candidate.get("process_support", []),
            "literature_pmids": [
                str(h.get("pmid")) for h in (biologist_output or {}).get(
                    "literature_hits", []) if h.get("pmid") is not None
            ] if target_matches else [],
        },
        "comparators": {
            "target_approved_drugs": approved,
            "selected_candidates": pool_comparators,
            "scope": "biologist approved-drug lookup plus current reviewed pool",
        },
        "target_evidence": {
            "target_qualified_records": [
                row for row in
                (candidate.get("_evidence_ledger") or {}).get("records", [])
                if row.get("target_evidence_scope", "target_qualified")
                == "target_qualified"
            ],
            "cross_target_records": [
                row for row in
                (candidate.get("_evidence_ledger") or {}).get("records", [])
                if row.get("target_evidence_scope") in {"cross_target", "off_target"}
            ],
            "cross_target_scoring_boost": 0,
        },
        "timothy_syndrome_cardiac_scope": timothy_scope,
        "trial_audit": candidate.get("trial_audit", {
            "query_status": unknown, "trials": [],
            "negative_repurposing_result": unknown,
        }),
    }


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
    if _holdout.is_active():
        l1_hit = bool(
            l1_withdrawn
            and (layer2 is None or layer2.get("verdict") != "NO")
        )
        l2_hit = bool(layer2 and layer2.get("confirmed"))
        safety_triggered = l1_hit or l2_hit
        r["safety_reconciliation"] = (
            {
                "status": "disputed",
                "reason": (
                    "Frozen safety-v2 reconciliation: structured withdrawal "
                    "conflicted with an independent NO."
                ),
                "layer1_source": layer1.get("source_url"),
                "layer2_citation": (layer2 or {}).get("citation"),
            }
            if l1_withdrawn and layer2 is not None
            and layer2.get("verdict") == "NO"
            else None
        )
        return _finish_safety_reconciliation(
            r, layer1, layer2, l1_hit, l2_hit, safety_triggered)
    # safety-v3 caps only an authority-confirmed, identity- and scope-matched
    # safety withdrawal. ChEMBL's broad withdrawn_flag remains a disclosure and
    # reconciliation input, but is not itself regulator confirmation.
    l1_hit = False
    l2_hit = bool(
        layer2
        and layer2.get("schema_version") == SAFETY_SCHEMA_VERSION
        and layer2.get("confirmed") is True
        and (layer2.get("authoritative_source") or {}).get(
            "verified_regulator_domain") is True
        and (layer2.get("scope") or {}).get("identity_matches") is True
        and (layer2.get("scope") or {}).get("jurisdiction")
        and (layer2.get("scope") or {}).get("formulation")
    )
    safety_triggered = l2_hit
    if l1_withdrawn and not l2_hit:
        r["safety_reconciliation"] = {
            "status": "unconfirmed_structured_signal",
            "reason": (
                "ChEMBL structured data reports withdrawn_flag=True, but no "
                "authority-confirmed safety-v3 identity-and-scope match was "
                "established. No hard cap was applied; review the disclosure."
            ),
            "layer1_source": layer1.get("source_url"),
            "layer2_citation": (layer2 or {}).get("citation"),
        }
    else:
        r["safety_reconciliation"] = None

    return _finish_safety_reconciliation(
        r, layer1, layer2, l1_hit, l2_hit, safety_triggered)


def _finish_safety_reconciliation(
    r: dict[str, Any],
    layer1: dict[str, Any],
    layer2: Optional[dict[str, Any]],
    l1_hit: bool,
    l2_hit: bool,
    safety_triggered: bool,
) -> bool:
    """Apply the cap/badge shared by frozen-v2 and production-v3 decisions."""
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


# ── Causal-anchor tier (pre-registered; rank-only, mirrors the F2 target-level
# mechanistic-convergence cap in agents/target_selection.py) ──────────────────
#
# A target reached by pathway expansion — or one whose discovery method never
# got attributed — is an EXPLORATORY lead, not a disease-causal anchor.  The
# pathway expander is documented as "handicaps, does not subordinate", so an
# exploratory target could outrank a directly disease-linked one and become the
# dossier headline with no visible signal that the causal gene was never the
# subject.  This restores the hierarchy at the CANDIDATE level (the F2 cap acts
# on targets only) as a rank demotion with disclosure — scores are untouched
# and STRONG_MATCH gating is unaffected.

#: Discovery methods that carry a direct disease-target link.
_CAUSAL_DISCOVERY_METHODS = frozenset({"genetic_association"})

#: Discovery methods carrying clinical precedent for the disease itself.
_PRECEDENT_DISCOVERY_METHODS = frozenset({
    "pharmacological_precedent",
    "pharmacological_precedent_via_parent_umbrella",
})

#: Discovery methods that only reach the target indirectly.
_EXPLORATORY_DISCOVERY_METHODS = frozenset({"pathway_neighbor"})

_TARGET_APPLICABILITIES = frozenset({
    "DIRECT_CAUSAL",
    "DIRECT_DISEASE_ASSOCIATED",
    "CROSS_TARGET_FUNCTIONALLY_SUPPORTED",
    "PATHWAY_ONLY",
    "UNKNOWN",
})


def _target_applicability(candidate: dict[str, Any]) -> str:
    """Return an explicit production applicability label.

    Upstream explicit labels win. The only compatibility projections are from
    provenance-bearing discovery methods; pathway adjacency is always
    PATHWAY_ONLY and an unattributed/class-analog row remains UNKNOWN.
    """
    explicit = str(candidate.get("target_applicability") or "").upper()
    if explicit in _TARGET_APPLICABILITIES:
        return explicit
    method = str(candidate.get("target_discovery_method") or "").strip().lower()
    if method == "genetic_association":
        return "DIRECT_DISEASE_ASSOCIATED"
    if method in _PRECEDENT_DISCOVERY_METHODS:
        # A pharmacological precedent is evidence that the target's
        # pharmacology may transfer across a disease-relevant channel/pathway.
        # It is not a direct causal-gene or disease-target association.
        return "CROSS_TARGET_FUNCTIONALLY_SUPPORTED"
    if method == "pathway_neighbor":
        return "PATHWAY_ONLY"
    return "UNKNOWN"


def _applicability_allows_prioritization(candidate: dict[str, Any]) -> bool:
    return candidate.get("target_applicability") in {
        "DIRECT_CAUSAL",
        "DIRECT_DISEASE_ASSOCIATED",
        "CROSS_TARGET_FUNCTIONALLY_SUPPORTED",
    }


def _applicability_order(candidate: dict[str, Any]) -> int:
    applicability = candidate.get("target_applicability")
    if applicability in {"DIRECT_CAUSAL", "DIRECT_DISEASE_ASSOCIATED"}:
        return 2
    if applicability == "CROSS_TARGET_FUNCTIONALLY_SUPPORTED":
        return 1
    return 0


def _candidate_source_coverage(
        source_status: Any, candidate: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assess provider coverage in the target scope applicable to a candidate.

    Direct/required target failures fail every candidate. An exploratory
    pathway-neighbor failure fails only a candidate targeting that neighbor;
    unrelated exploratory failures remain explicit warnings. Flat legacy
    source-status input has no target scope and deliberately retains the
    original fail-closed behavior.
    """
    required = {"chembl", "gtopdb", "drugcentral", "bindingdb"}
    scoped: list[tuple[dict[str, Any], dict[str, Any]]] = []
    target_failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def _norm(value: Any) -> str:
        return "".join(str(value or "").upper().split())

    candidate_symbols = {_norm(candidate.get("target_symbol"))} if candidate else set()
    candidate_accessions = {_norm(candidate.get("uniprot_id"))} if candidate else set()
    if candidate:
        for membership in candidate.get("target_memberships") or []:
            if isinstance(membership, dict):
                candidate_symbols.add(_norm(membership.get("target_symbol")))
                candidate_accessions.add(_norm(membership.get("uniprot_id")))
    candidate_symbols.discard("")
    candidate_accessions.discard("")

    def applies_to_candidate(target: dict[str, Any]) -> bool:
        if target.get("coverage_required") is not False:
            return True
        if candidate is None:
            return False
        return bool(
            _norm(target.get("target_symbol")) in candidate_symbols
            or _norm(target.get("uniprot_id")) in candidate_accessions
        )

    def visit(value: Any) -> None:
        if not isinstance(value, dict):
            return
        target = value.get("_target")
        if isinstance(target, dict):
            scoped.append((target, value))
            return
        for key, child in value.items():
            if isinstance(child, dict):
                visit(child)

    visit(source_status)
    # Preserve old single-target/flat payload semantics.
    if not scoped:
        scoped = [({"coverage_required": True}, source_status or {})]
    failures: list[dict[str, Any]] = []
    enabled: set[str] = set()
    for target, envelope in scoped:
        applies = applies_to_candidate(target)
        target_label = (target.get("target_symbol") or target.get("uniprot_id")
                        or target.get("target_index"))
        target_bad = str(target.get("status") or "").casefold() not in {
            "ok", "healthy", "success", "complete"}
        if target_bad:
            detail = {
                "source": "target_evaluation",
                "status": target.get("status", "missing"),
                "error": target.get("error"),
                "target_index": target.get("target_index"),
                "target_symbol": target.get("target_symbol"),
                "uniprot_id": target.get("uniprot_id"),
            }
            (failures if applies else warnings).append(detail)
        for name in sorted(required):
            row = envelope.get(name)
            if row and str(row.get("status") or "").casefold() == "disabled":
                continue
            enabled.add(name)
            if row is None or not _source_row_healthy(row):
                detail = {
                    "source": name,
                    "status": (row or {}).get("status", "missing"),
                    "error": (row or {}).get("error"),
                    "target": target_label,
                    "target_index": target.get("target_index"),
                    "target_symbol": target.get("target_symbol"),
                    "uniprot_id": target.get("uniprot_id"),
                }
                (failures if applies else warnings).append(detail)
    return {
        "complete": not failures,
        "enabled_sources": sorted(enabled),
        "failures": failures,
        "warnings": warnings,
    }


def _source_row_healthy(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().casefold()
    if status not in {"ok", "empty", "healthy", "available", "success", "complete"}:
        return False
    if row.get("partial") is True or row.get("materially_partial") is True:
        return False
    if row.get("complete") is False:
        return False
    completeness_status = str(
        row.get("completeness_status") or row.get("coverage_status") or ""
    ).casefold()
    if "partial" in completeness_status or "incomplete" in completeness_status:
        return False
    completeness = row.get("completeness")
    if isinstance(completeness, (int, float)) and completeness < 1:
        return False
    return True


def _availability_gate(layer1: dict[str, Any],
                       layer2: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Separate active-ingredient availability from safety withdrawal."""
    s2 = layer2 or {}
    status = str(s2.get("safety_status") or "").upper()
    scope = s2.get("scope") or {}
    authority = s2.get("authoritative_source") or {}
    jurisdiction = str(scope.get("jurisdiction") or "").casefold()
    global_scope = bool(re.search(
        r"\b(global|globally|worldwide|all (?:markets|jurisdictions|countries))\b",
        jurisdiction))
    confirmed_global = bool(
        status == "INGREDIENT_UNAVAILABLE"
        and authority.get("verified_regulator_domain") is True
        and scope.get("identity_matches") is True
        and scope.get("fetched_identity_verified") is True
        and scope.get("jurisdiction_verified") is True
        and scope.get("formulation_verified") is True
        and s2.get("quote_verified_in_fetched_source") is True
        and global_scope
    )
    if confirmed_global:
        return {
            "status": "GLOBALLY_UNAVAILABLE_ACTIVE_INGREDIENT",
            "blocks_prioritization": True,
            "reason": "An authoritative source confirms global active-ingredient unavailability.",
        }
    # Scope-verified, uncontradicted market exit in a *named* (non-global)
    # jurisdiction: the confirmed_global branch above requires "global"/
    # "worldwide" wording, which a single-jurisdiction market exit will never
    # have — this is what let panobinostat's 2022 FDA market withdrawal pass
    # through disclosure-only (REGIONAL_OR_PRODUCT_DISCLOSURE, below) even
    # though the search found no alternative-supply evidence for it. It must
    # not swallow the Cantu/glibenclamide case, where "other manufacturers
    # continue to supply generic glibenclamide" is exactly the kind of
    # contradiction this checks for, so this branch requires positive
    # scope-verification AND an explicit absence of any such contradiction —
    # silence about alternative supply is not evidence it exists.
    _NO_ALT_SUPPLY_SIGNALS = {
        "generics remain available", "generic alternatives available",
        "generic supply continues", "no formal withdrawal",
        "not withdrawn", "remains marketed",
    }
    confirmed_uncontradicted_exit = bool(
        status in {"BRAND_DISCONTINUED", "MANUFACTURER_DISCONTINUED",
                   "NOT_MARKETED"}
        and authority.get("verified_regulator_domain") is True
        and scope.get("identity_matches") is True
        and scope.get("fetched_identity_verified") is True
        and scope.get("jurisdiction_verified") is True
        and scope.get("formulation_verified") is True
        and s2.get("quote_verified_in_fetched_source") is True
        and jurisdiction
        and not _NO_ALT_SUPPLY_SIGNALS.intersection(
            str(c).casefold() for c in (s2.get("contradictions") or []))
    )
    if confirmed_uncontradicted_exit:
        return {
            "status": "CONFIRMED_MARKET_EXIT_NO_ALTERNATIVE_SUPPLY",
            "blocks_prioritization": True,
            "reason": (
                f"An authoritative, scope-verified source confirms this "
                f"product exited the market in {scope.get('jurisdiction')}, "
                f"with no evidence found of continued or alternative supply "
                f"there."
            ),
        }
    if status in {"BRAND_DISCONTINUED", "MANUFACTURER_DISCONTINUED",
                  "NOT_MARKETED", "INGREDIENT_UNAVAILABLE"}:
        return {
            "status": "REGIONAL_OR_PRODUCT_DISCLOSURE",
            "blocks_prioritization": False,
            "reason": "A product/region availability notice was found, but global active-ingredient unavailability was not confirmed.",
        }
    if layer1.get("availability_type") == -1:
        return {
            "status": "MANUFACTURER_DISCONTINUED",
            "blocks_prioritization": False,
            "reason": "Manufacturer discontinuation is disclosure-only; generic active ingredient may remain available.",
        }
    return {
        "status": "UNKNOWN",
        "blocks_prioritization": False,
        "reason": "Global active-ingredient availability was not established.",
    }


def _observed_literature_support(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    finding = candidate.get("literature_limitation") or {}
    # Related/non-applicable support is deliberately excluded: it may be
    # disclosed in the dossier but cannot satisfy exact-use readiness.
    rows = [
        row for row in finding.get("evidence", [])
        if row.get("exact_use_label") == "APPLICABLE_SUPPORT"
        and row.get("exact_applicability") is True
    ]
    return [{
        "pmid": row.get("pmid"),
        "evidence_level": row.get("evidence_level"),
        "exact_use_label": row.get("exact_use_label"),
        "exact_applicability": bool(row.get("exact_applicability")),
        "score_boost": 0,
    } for row in rows]


def _target_tier(method: Optional[str]) -> str:
    """Classify a candidate's target by how directly it is tied to the disease.

    Unknown / blank discovery methods are classified ``unattributed`` and are
    treated as exploratory: an unexplained provenance is the weakest claim in
    the pool, never the strongest.
    """
    m = (method or "").strip()
    if not m:
        return "unattributed"
    if m in _CAUSAL_DISCOVERY_METHODS:
        return "causal_anchor"
    if m in _PRECEDENT_DISCOVERY_METHODS:
        return "clinical_precedent"
    if m in _EXPLORATORY_DISCOVERY_METHODS:
        return "exploratory_expansion"
    return "unattributed"


_EXPLORATORY_TIERS = frozenset({"exploratory_expansion", "unattributed"})


def _direction_check_candidate(
    candidate: dict[str, Any], disease: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve a candidate's target-specific action, then direction-check it.

    Shared by every direction pass so they cannot drift apart in how the drug's
    action ON THE EVALUATED TARGET is labeled. Returns the direction verdict and
    the action-type provenance, and records the verdict on the candidate.
    """
    target_sym = candidate.get("target_symbol") or ""
    at_info = (
        {"source": "holdout_redacted", "action_type": None,
         "mechanism_of_action": None}
        if _candidate_is_heldout(candidate)
        else get_drug_action_type(candidate["drug_name"], target_sym)
    )
    action_t = at_info.get("action_type")
    moa = at_info.get("mechanism_of_action")

    # Prefer a qualified target-specific action from the common evidence
    # ledger.  This prevents non-ChEMBL curated interactions from being
    # mislabeled as generic IC50/Ki inhibitors.
    ledger_records = (candidate.get("_evidence_ledger") or {}).get("records", [])
    ledger_action_record = next(
        (
            rec for rec in ledger_records
            if rec.get("qualification_status") == "qualified"
            and rec.get("evidence_role") in ("efficacy", "target_link")
            and rec.get("action")
            and (
                not target_sym
                or str(rec.get("target_symbol") or "").upper() == target_sym.upper()
            )
        ),
        None,
    )
    if ledger_action_record:
        action_t = ledger_action_record.get("action")
        moa = ledger_action_record.get("context") or moa
        at_info = {
            **at_info,
            "source": f"evidence_ledger:{ledger_action_record.get('provider')}",
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
    if at_info.get("source") == "any_mechanism" and action_t:
        action_t = (
            f"INHIBITOR (inferred from IC50/Ki bioactivity assay data; "
            f"ChEMBL primary registered mechanism is '{action_t} / {moa}' "
            f"which is for a DIFFERENT protein target — do NOT use this as "
            f"the drug's action on {target_sym}; instead reason from the "
            f"fact that this drug has IC50/Ki binding activity against "
            f"{target_sym} in ChEMBL assays, which implies inhibitory interaction)"
        )
    elif at_info.get("source") == "not_found":
        action_t = (
            f"INHIBITOR (inferred: no ChEMBL mechanism record found; "
            f"drug has IC50/Ki binding activity against {target_sym})"
        )

    direction = check_mechanism_direction(
        candidate["drug_name"], target_sym, action_t, moa, disease,
        candidate_chembl_ids=_candidate_chembl_ids(candidate),
        candidate_inchikey=candidate.get("inchikey"),
    )
    candidate["mechanism_direction"] = direction
    return direction, at_info


def _unchecked_direction_leader(
    reviewed: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The promotable leader, when it has never been direction-checked.

    The bounded passes spend their budget on the candidates they cap, and each
    cap re-sorts the list. A pool whose whole head is directionally
    incompatible therefore exhausts the budget capping it and hands the lead to
    a candidate no pass ever reached. Returns that candidate so the caller can
    close the gap; None when the leader was already checked.
    """
    for candidate in reviewed:
        if not candidate.get("strong_match"):
            continue
        if candidate.get("mechanism_direction") is not None:
            return None
        return candidate
    return None


def _postcap_direction_shortlist(
    reviewed: list[dict[str, Any]],
    checked_names: set[str],
    seen_fps: list[Any],
) -> list[dict[str, Any]]:
    """Collect newly promoted strong candidates after cap-driven re-ranking.

    Tier ordering can place a weak causal anchor before strong exploratory
    candidates. Therefore a non-strong row must be skipped, not treated as an
    end-of-list sentinel.
    """
    selected: list[dict[str, Any]] = []
    selected_fps: list[Any] = []
    for candidate in reviewed:
        if len(selected) >= MAX_MECHANISM_DIRECTION_CANDIDATES:
            break
        if not candidate.get("strong_match"):
            continue
        if candidate["drug_name"] in checked_names:
            continue
        candidate_fp = _mdc_desalted_fp(candidate.get("smiles"))
        is_duplicate = False
        if candidate_fp is not None:
            for seen_fp in seen_fps + selected_fps:
                if (
                    seen_fp is not None
                    and DataStructs.TanimotoSimilarity(
                        candidate_fp, seen_fp,
                    ) >= 0.99
                ):
                    is_duplicate = True
                    break
        if not is_duplicate:
            selected.append(candidate)
            selected_fps.append(candidate_fp)
    return selected


def _apply_causal_tier_demotion(reviewed: list[dict[str, Any]]) -> None:
    """Rank-only demotion of exploratory candidates below the best anchored one.

    ``reviewed`` must already be sorted best-first; it is reordered in place.
    When the pool contains NO anchored candidate (every lead is exploratory),
    nothing is demoted — the ordering is left alone and each row keeps its
    exploratory tier for disclosure.
    """
    for r in reviewed:
        r["target_tier"] = _target_tier(r.get("target_discovery_method"))
        # Recompute disclosure metadata from the CURRENT score order. A later
        # cap can make a formerly rank-demoted row naturally fall below the
        # anchor; stale labels would then claim a demotion that no longer
        # affected its position.
        r["exploratory_rank_demoted"] = False
        r["causal_anchor"] = None

    anchor_idx = next(
        (i for i, r in enumerate(reviewed)
         if r["target_tier"] not in _EXPLORATORY_TIERS),
        None,
    )
    if anchor_idx is None or anchor_idx == 0:
        return

    anchor = reviewed[anchor_idx]
    anchor_note = {
        "drug_name": anchor.get("drug_name"),
        "target_symbol": anchor.get("target_symbol"),
        "target_discovery_method": anchor.get("target_discovery_method"),
        "target_tier": anchor["target_tier"],
        "composite_score": anchor.get("composite_score"),
    }

    demoted = [r for r in reviewed[:anchor_idx]]
    for r in demoted:
        r["exploratory_rank_demoted"] = True
        r["causal_anchor"] = anchor_note

    rest = reviewed[anchor_idx:]
    reordered = [anchor] + demoted + rest[1:]
    reviewed[:] = reordered
    print(
        f"[reviewer] causal-anchor tier: demoted {len(demoted)} exploratory "
        f"candidate(s) below {anchor.get('drug_name')} "
        f"({anchor.get('target_symbol')}, "
        f"{anchor.get('target_discovery_method')}); scores unchanged, rank only"
    )


#: How directly a tier ties its candidate to the disease. Higher wins when the
#: SAME compound was pooled against more than one target.
_TIER_STRENGTH = {
    "causal_anchor": 3,
    "clinical_precedent": 2,
    "exploratory_expansion": 1,
    "unattributed": 0,
}


def _compound_identity(candidate: dict[str, Any]) -> str:
    """Structure-first identity, so one drug's rows group across targets."""
    key = str(candidate.get("inchikey") or "").strip().upper()
    if key:
        # Connectivity layer only: salt and stereo variants are the same drug
        # for the purpose of "which target did we attribute this to".
        return f"inchikey:{key.split('-')[0]}"
    return "name:" + str(candidate.get("drug_name") or "").strip().lower()


def _demote_weaker_tier_duplicates(reviewed: list[dict[str, Any]]) -> None:
    """Represent each compound by its best-attributed target row.

    A drug is pooled once per target it was retrieved for, and those rows are
    not interchangeable: one may carry a measured disease-target association
    and a qualified assay, another only a stamped precedent constant. Ranked
    independently, the thin row can win.

    That is what produced the AKT1 dossier for an AKT2 disease. Capivasertib
    was pooled on AKT2 (genetic_association, measured association 0.787,
    pChEMBL 8.10) and on AKT1 (a pathway neighbour promoted to precedent, no
    assay row, association excluded as a stamped constant). The AKT1 row
    outranked the AKT2 row, so the dossier proposed the right drug against the
    wrong gene and the reader had no way to see that a better-attributed row
    existed.

    Rank-only, exactly like the causal-anchor demotion: scores are untouched.
    """
    best: dict[str, int] = {}
    for r in reviewed:
        ident = _compound_identity(r)
        strength = _TIER_STRENGTH.get(
            _target_tier(r.get("target_discovery_method")), 0)
        best[ident] = max(best.get(ident, -1), strength)

    strongest: list[dict[str, Any]] = []
    weaker: list[dict[str, Any]] = []
    for r in reviewed:
        ident = _compound_identity(r)
        strength = _TIER_STRENGTH.get(
            _target_tier(r.get("target_discovery_method")), 0)
        if strength < best[ident]:
            r["tier_duplicate_demoted"] = True
            weaker.append(r)
        else:
            r["tier_duplicate_demoted"] = False
            strongest.append(r)

    if not weaker:
        return
    # Score order is preserved within each group.
    reviewed[:] = strongest + weaker
    print(
        f"[reviewer] target-attribution: demoted {len(weaker)} row(s) whose "
        f"compound is also pooled against a better-attributed target; "
        f"scores unchanged, rank only"
    )


def _rank_reviewed(reviewed: list[dict[str, Any]]) -> None:
    """Score-sort, then apply the rank-only demotions.

    Ranking ALWAYS goes through this sequence.  Demoting only once at the end
    would let a candidate reach rank 1 after the bounded mechanism-direction and
    Layer-2 safety shortlists were already drawn from the old top of the list,
    so the eventual headline could skip both checks.

    Attribution is resolved before the causal-anchor pass so the anchor is
    chosen from rows that already represent their compound's best target.
    """
    _sort_reviewed(reviewed)
    if not _holdout.is_active():
        _demote_weaker_tier_duplicates(reviewed)
    _apply_causal_tier_demotion(reviewed)


def _sort_reviewed(reviewed: list[dict[str, Any]]) -> None:
    """Sort by composite, breaking cap-floor ties by pre-cap score.

    Every cap (unapproved / mechanism-direction / DILI pre-cap / safety) pins
    candidates to the same floor value.  Sorting capped ties by the composite
    computed BEFORE any cap keeps a genuinely strong-but-capped candidate
    ranked above a weak one at the same floor, without changing which
    candidates pass STRONG_MATCH.
    """
    if _holdout.is_active():
        reviewed.sort(
            key=lambda r: (
                r["composite_score"], r.get("pre_cap_score") or 0.0),
            reverse=True,
        )
        return
    reviewed.sort(
        key=lambda r: (
            _applicability_order(r),
            r["composite_score"], r.get("pre_cap_score") or 0.0,
            str(r.get("canonical_compound_identity") or r.get("drug_name") or ""),
            str(r.get("uniprot_id") or r.get("target_symbol") or ""),
        ),
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
            "formula_version": "reviewer-composite-v3-production",
            "safety_schema_version": SAFETY_SCHEMA_VERSION,
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
            "tractability_weights": TRACTABILITY_WEIGHTS,
            # True when the operator overrode any scoring weight via env —
            # dossiers must disclose that scores are not benchmark-comparable.
            "scoring_config_overridden": bool(
                TRACTABILITY_WEIGHTS_OVERRIDDEN or COMPOSITE_WEIGHTS_OVERRIDDEN),
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
