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
from data_sources.orphadata import (
    get_rare_disease_list, get_who_ntd_list, get_disease_xrefs,
    get_disease_prevalence, get_disorder_metadata, GROUP_OF_DISORDERS,
)
from data_sources.open_targets import (
    search_disease_efo, get_target_disease_score, get_disease_known_drugs,
    get_disease_orphanet_code, get_disease_parents, get_disease_descendant_count,
    get_ot_canonical_disease_name,
)
from data_sources.chembl import (
    get_target_bioactivity_count,
    get_pharmacological_targets_for_disease,
    redact_holdout_names,
    PHARM_PRECEDENT_UMBRELLA_ASSOC_SCORE,
)
from data_sources import holdout as _holdout
from data_sources.europepmc_mechanisms import discover_disease_process_targets
from data_sources.afdb import get_structure_confidence
from data_sources.clinicaltrials import check_prior_trials

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
TOP_N = 30
TOP_TARGETS_PER_DISEASE = 5
# Provisional v2 engineering policy: literature-supported process targets carry
# moderate disease specificity. This is fixed independently of the five
# acceptance fixtures and must be calibrated on a broader drug-grouped corpus
# before any benchmark freeze.
PROCESS_EVIDENCE_ASSOC_SCORE = 0.50

TRACTABILITY_WEIGHTS = {
    "chembl_log_count": 0.40,
    "afdb_plddt": 0.35,
    "trial_penalty": 0.25,
}

CHEMBL_COUNT_CAP = 500

# Breadth filter for the parent-umbrella drug supplement.
# Parents with more than this many descendants in the OT disease ontology are
# skipped: their approved-drug links are too disease-non-specific to be useful
# pharmacological-precedent signals.
# Calibrated from real descendant counts (2025-07):
#   largest known-good parent  (acute myeloid leukemia)   = 87 descendants
#   smallest known-bad parent  (inborn error of immunity) = 228 descendants
# N=100 sits in the gap with 2.6× margin on both sides.
PARENT_MAX_DESCENDANTS = 100


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
    unmet_need_score — graceful degradation formula:

      treatment_component:
        True  (approved treatment exists) → 0.0  (already served, low unmet need)
        False (no approved treatment)     → 1.0  (high unmet need)
        None  (data unavailable)          → 0.5  (flagged for manual review)

      prevalence_component (secondary, 0–1, log-scaled per million):
        Only added when real prevalence data is available. Its absence does NOT
        collapse the score to a constant — the treatment signal is used alone.

      Weighting:
        - Both signals present:  score = 0.7 * treatment + 0.3 * prevalence
        - Treatment only:        score = treatment_component  (prevalence absent)
        - Neither (both None):   score = 0.5                 (flag for review)

    Returns a float in [0, 1]. Never collapses to a single constant across all
    diseases once has_approved_treatment is True/False (not None).
    """
    if has_approved_treatment is True:
        treatment_component = 0.0
    elif has_approved_treatment is False:
        treatment_component = 1.0
    else:
        treatment_component = 0.5  # unknown — flag for manual review

    if prevalence and prevalence > 0:
        prevalence_component = min(1.0, math.log1p(prevalence) / math.log1p(1_000_000))
        score = 0.7 * treatment_component + 0.3 * prevalence_component
    else:
        # No prevalence data — rely entirely on the treatment signal
        score = treatment_component

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


def _disorder_group_maps() -> tuple[dict[str, str], set[str]]:
    """
    Return ({orpha_code -> DisorderGroup}, {normalized names of "Group of
    disorders" entries}) parsed from the Orphanet cross-referencing XML product.

    Used to drop umbrella "Group of disorders" terms (e.g. "RASopathy") from the
    candidate universe and to give a specific error when a manual query names an
    umbrella term.

    FAIL-CLOSED: an empty result means the Orphanet metadata API was unreachable.
    Callers that require filtering MUST check for an empty by_code and refuse to
    proceed rather than silently skipping the umbrella filter.
    """
    by_code: dict[str, str] = {}
    group_names: set[str] = set()
    for rec in get_disorder_metadata():
        group = rec.get("disorder_group")
        code = rec.get("orpha_code")
        if code and group:
            by_code[str(code)] = group
        if group == GROUP_OF_DISORDERS:
            group_names.add(_norm(rec.get("name")))
    return by_code, group_names


def _build_candidate_universe(exclude_groups: bool = True) -> list[dict[str, Any]]:
    """
    Returns a unified list of disease dicts from Orphanet + WHO NTDs.
    Each dict has: {name, orpha_code, icd10, omim, mesh, source}

    When exclude_groups is True (default), Orphanet "Group of disorders" umbrella
    entries are dropped: they aggregate many distinct diseases and are not a
    single scorable (disease, target) unit. WHO NTDs are never groups.
    """
    _log("Fetching Orphanet rare disease list …")
    orphanet = get_rare_disease_list()
    _log(f"  Orphanet: {len(orphanet)} diseases")

    ntds = get_who_ntd_list()
    _log(f"  WHO NTDs: {len(ntds)} diseases")

    group_by_code: dict[str, str] = {}
    if exclude_groups:
        group_by_code, _ = _disorder_group_maps()
        if not group_by_code:
            raise RuntimeError(
                "[orphadata] DisorderGroup metadata unavailable (API returned empty "
                "result). Refusing to build the candidate universe without umbrella "
                "'Group of disorders' filtering — an empty filter map would allow "
                "all umbrella terms into the sweep. Retry when the Orphanet XML "
                "product endpoint recovers."
            )

    candidates: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    excluded_groups = 0
    excluded_admin = 0

    for d in orphanet:
        name = d.get("name", "").strip()
        code = d.get("orpha_code")
        # FAIL-CLOSED: drop Orphanet administrative-prefix entries that are not
        # genuine rare diseases.  "OBSOLETE:" marks retired/merged nomenclature;
        # "NON RARE IN EUROPE:" marks diseases whose European prevalence does not
        # qualify as rare.  Neither should ever reach the scoring pipeline.
        if _EFO_PREFIX_RE.match(name):
            excluded_admin += 1
            continue
        if exclude_groups and code is not None and \
                group_by_code.get(str(code)) == GROUP_OF_DISORDERS:
            excluded_groups += 1
            continue
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

    if excluded_admin:
        _log(f"  Excluded {excluded_admin} Orphanet administrative-prefix entries "
             f"(OBSOLETE / NON RARE IN EUROPE) from the candidate universe")
    if exclude_groups and excluded_groups:
        _log(f"  Excluded {excluded_groups} Orphanet 'Group of disorders' "
             f"umbrella entries from the candidate universe")

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
    group_by_code, group_names = _disorder_group_maps()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in _load_existing_top_candidates():
        name = row.get("disease_name")
        key = _norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        # Drop administrative-prefix entries even if they somehow snuck into a
        # stale top_candidates.json produced before this filter was added.
        if _EFO_PREFIX_RE.match(name or ""):
            continue
        # A stale top_candidates.json (produced before umbrella filtering) may
        # carry "Group of disorders" rows. Drop them here too, otherwise they
        # re-enter the matchable universe and bypass the exclusion + error path.
        code = row.get("orpha_code")
        if (code is not None and group_by_code.get(str(code)) == GROUP_OF_DISORDERS) \
                or key in group_names:
            continue
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


import re as _re

_EFO_PREFIX_RE = _re.compile(
    r'^(NON\s+RARE\s+IN\s+EUROPE|OBSOLETE)\s*:\s*',
    flags=_re.IGNORECASE,
)

# Heuristic for disease names that carry a legacy numbered or DYT-style
# classification that Open Targets may not recognise under that exact string,
# raising the risk of an EFO mismatch or a drug-indication data gap.
#
# Uses whitespace delimiters (\s\d+(?:\s|$)) so chemical compound numbers
# like the "6" in "glucose-6-phosphate" or the "3" in "3-phosphoglycerate"
# are NOT matched (those are always surrounded by hyphens, not spaces).
# Matches: "Dystonia 14", "Torsion dystonia 7", "BRIC type 2",
#          "DYT14", "DYT-14", "DYT 7".
_LEGACY_NAME_RE = _re.compile(
    r'\s\d+(?:\s|$)|DYT[-\s]?\d+',
    flags=_re.IGNORECASE,
)


# Stop-words stripped before computing token overlap between a queried disease
# name and OT's canonical name for the resolved EFO node.  Generic disease
# words would artificially inflate overlap scores between unrelated diseases.
_EFO_NAME_STOP = frozenset({
    "", "the", "of", "due", "to", "and", "a", "an", "with",
    "disease", "type", "syndrome", "deficiency", "in", "by", "disorder",
    "autosomal", "dominant", "recessive", "familial", "congenital",
})

# Below this overlap the EFO resolution is a hard mismatch: the system
# found no meaningful connection between the queried name and OT's canonical
# name for the resolved node.  In manual mode → raise; in sweep → skip.
_EFO_HARD_STOP_THRESHOLD = 0.0   # exclusive: overlap must be > this to proceed

# Below this overlap (but above the hard-stop) the EFO resolution is a
# partial mismatch: proceed but stamp a Limitations warning on the report.
_EFO_WARN_THRESHOLD = 0.5        # exclusive: overlap must be >= this to suppress warning


def _efo_name_overlap(queried: str, efo_id: str) -> Optional[float]:
    """
    Compute the token overlap between the queried disease name and OT's own
    canonical name for the resolved EFO node.

    Returns a float in [0, 1] — the fraction of meaningful query tokens that
    appear in the OT canonical name — or None if the OT name is unavailable
    (API failure or unknown EFO).

    Cached indirectly: get_ot_canonical_disease_name has a 30-day cache so
    repeated calls for the same EFO within a pipeline run are free.
    """
    ot_name = get_ot_canonical_disease_name(efo_id)
    if not ot_name:
        return None
    q_tok = set(_re.split(r"\W+", queried.lower())) - _EFO_NAME_STOP
    n_tok = set(_re.split(r"\W+", ot_name.lower())) - _EFO_NAME_STOP
    if not q_tok:
        return None
    return len(q_tok & n_tok) / len(q_tok)


def _efo_name_mismatch_warning(queried: str, efo_id: str) -> Optional[str]:
    """
    Post-resolution sanity check: returns a Limitations-section warning string
    when the overlap is in the partial-mismatch band (0 < overlap < 0.5).

    Returns None when:
      - overlap >= 0.5      (names sufficiently aligned — no warning needed)
      - overlap == 0.0      (hard mismatch — caller must raise / skip, not warn)
      - OT name unavailable (can't evaluate)

    The split between hard-stop (0%) and warn (0–50%) is intentional:
      0%   overlap → resolved node shares no meaningful tokens with the query;
                     the EFO is almost certainly the wrong disease entirely.
                     Proceeding silently would produce a report about a
                     different disease.  Callers raise (manual) or skip (sweep).
      1–49% overlap → partial naming mismatch; may be a synonym, abbreviation,
                     or subtype renaming.  Proceeding is defensible if the
                     user is warned.
    """
    overlap = _efo_name_overlap(queried, efo_id)
    if overlap is None or overlap >= _EFO_WARN_THRESHOLD or overlap <= _EFO_HARD_STOP_THRESHOLD:
        return None
    ot_name = get_ot_canonical_disease_name(efo_id)  # cached — free second call
    msg = (
        f"**EFO RESOLUTION MISMATCH — verify disease mapping independently.** "
        f"The queried disease '{queried}' was resolved to EFO/MONDO ID `{efo_id}`, "
        f"but Open Targets' own canonical name for that node is "
        f"*'{ot_name}'* (token overlap {overlap:.0%} with the queried name). "
        f"These names may describe different diseases. "
        f"The association scores, approved-drug status, and target rankings in this "
        f"report reflect '{ot_name}', NOT necessarily '{queried}'. "
        f"Cross-check the Orphanet ORPHA code and the OT disease page before "
        f"treating this result as disease-specific evidence."
    )
    print(
        f"[target_selection] WARNING: EFO partial mismatch for '{queried}' → "
        f"{efo_id} ('{ot_name}', overlap={overlap:.0%}). Warning attached to report."
    )
    return msg


def _resolve_efo_id(
    disease_name: str,
    orig_query: str = "",
    orpha_code: Optional[str] = None,
) -> Optional[str]:
    """
    Attempt to resolve an EFO/MONDO ID for a disease via multiple strategies:

      1. Direct OT text search on the Orphanet official name.
      2. Strip known administrative prefixes ("NON RARE IN EUROPE: ",
         "OBSOLETE: ") that confuse OT's name-based search and retry.
      3. If a different original user query was supplied, try that string.

    Returns the first EFO ID found, or None if all attempts fail.
    Caching is handled inside search_disease_efo; no duplicate cache writes.
    """
    efo_id = search_disease_efo(disease_name)
    if efo_id:
        return efo_id

    stripped = _EFO_PREFIX_RE.sub("", disease_name).strip()
    if stripped and stripped.lower() != disease_name.lower():
        efo_id = search_disease_efo(stripped)
        if efo_id:
            return efo_id

    if orig_query and orig_query.strip().lower() not in (
        disease_name.lower(), stripped.lower()
    ):
        efo_id = search_disease_efo(orig_query.strip())
        if efo_id:
            return efo_id

    return None


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

    # 3. unique substring on name — exclude OBSOLETE entries (high false-positive risk)
    substring_hits = [
        d for name_key, d in by_name.items()
        if q in name_key and not name_key.startswith("obsolete")
    ]
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
        # EFO synonym fallback: common names like "Pompe disease" differ from
        # Orphanet's official name ("Glycogen storage disease due to acid maltase
        # deficiency").  Search OT directly → resolve EFO → get Orphanet xref →
        # find matching disease in our universe by ORPHA code.
        efo_direct = search_disease_efo(query)
        if efo_direct:
            orpha_code = get_disease_orphanet_code(efo_direct)
            if orpha_code:
                disease = next(
                    (d for d in candidates
                     if str(d.get("orpha_code")) == str(orpha_code)),
                    None,
                )

    if disease is None:
        # Before the generic not-found error, check whether the query names an
        # Orphanet "Group of disorders" umbrella term (deliberately excluded from
        # the universe). If so, give a specific, actionable message.
        _, group_names = _disorder_group_maps()
        if _norm(query) in group_names:
            raise DiseaseNotInUniverse(
                f"'{query}' is an Orphanet 'Group of disorders' umbrella term, not a "
                f"single scorable disease — it aggregates several distinct disorders. "
                f"Silver Bullet scores one (disease, target) pair at a time, so please "
                f"pick a specific constituent disease within this group (e.g. a named "
                f"subtype) rather than the umbrella category."
            )
        raise DiseaseNotInUniverse(
            f"'{query}' was not found in the rare-disease / neglected-tropical-disease "
            f"universe this system covers (Orphanet rare diseases + WHO NTDs). Silver "
            f"Bullet is scoped to rare and neglected diseases. Check the spelling, try "
            f"the disease's Orphanet name, or leave the field blank to auto-explore the "
            f"ranked candidate list."
        )

    # Post-match umbrella guard: a match may have come from a stale
    # top_candidates.json or an EFO xref that resolves to an Orphanet
    # "Group of disorders". Reject it here regardless of match source so an
    # umbrella term can never reach scoring.
    _group_by_code, _group_names = _disorder_group_maps()
    _matched_code = disease.get("orpha_code")
    if (_matched_code is not None
            and _group_by_code.get(str(_matched_code)) == GROUP_OF_DISORDERS) \
            or _norm(disease.get("name")) in _group_names:
        raise DiseaseNotInUniverse(
            f"'{query}' resolves to the Orphanet 'Group of disorders' umbrella term "
            f"'{disease.get('name')}', not a single scorable disease — it aggregates "
            f"several distinct disorders. Silver Bullet scores one (disease, target) "
            f"pair at a time, so please pick a specific constituent disease within "
            f"this group (e.g. a named subtype) rather than the umbrella category."
        )

    disease_name = disease["name"]
    _log(f"Manual selection: matched '{query}' → '{disease_name}'")

    efo_id = _resolve_efo_id(disease_name, orig_query=query)
    if not efo_id:
        raise RuntimeError(
            f"'{disease_name}' is in the rare/NTD universe but could not be matched "
            f"to an Open Targets EFO ID (tried official name, prefix-stripped name, "
            f"and original query '{query}'). "
            f"Try an alternate common name for this disease — for example, use "
            f"'polycystic ovary syndrome' rather than the Orphanet administrative "
            f"name that may include prefixes like 'NON RARE IN EUROPE:'."
        )

    # Hard-stop check: if OT's own canonical name for the resolved EFO shares
    # zero tokens with the Orphanet official name (disease_name), the EFO lookup
    # landed on the wrong disease entirely.  A Limitations bullet is not enough —
    # proceeding would produce a report about a different disease.
    #
    # WHY disease_name, not query: the Orphanet official name is what was actually
    # sent to OT's search API.  User queries are aliases (e.g. "Pompe disease"),
    # and their common names often differ from OT's canonical name even when the
    # disease is correct — zero token overlap between an alias and the OT name is
    # expected and is NOT a mismatch.  Comparing the Orphanet official name
    # against the OT canonical name is the right fidelity check.
    #
    # Hard-stop threshold: overlap == 0.0 (strictly no meaningful tokens in common
    # after stop-word removal).  Partial mismatches (0 < overlap < 0.5) still
    # proceed but receive a prominent Limitations warning.
    _efo_overlap = _efo_name_overlap(disease_name, efo_id)
    if _efo_overlap is not None and _efo_overlap <= _EFO_HARD_STOP_THRESHOLD:
        _ot_canonical = get_ot_canonical_disease_name(efo_id) or efo_id
        raise RuntimeError(
            f"EFO RESOLUTION MISMATCH — cannot proceed.\n"
            f"\n"
            f"  User query:          '{query}'\n"
            f"  Orphanet name:       '{disease_name}'\n"
            f"  Resolved EFO ID:     {efo_id}\n"
            f"  OT canonical name:   '{_ot_canonical}'\n"
            f"  Token overlap:       0%  (Orphanet name shares no meaningful tokens\n"
            f"                        with OT's canonical name for this EFO node)\n"
            f"\n"
            f"Open Targets mapped '{disease_name}' to a node ({efo_id}) whose own "
            f"canonical name is '{_ot_canonical}'. These appear to be different "
            f"diseases — proceeding would generate a report about '{_ot_canonical}', "
            f"not '{query}'.\n"
            f"\n"
            f"To fix: try rephrasing with the disease's most common synonym, its "
            f"Orphanet ORPHA-code-based name (found at orphadata.com), or its OMIM "
            f"preferred title. If the disease genuinely has no OT representation, "
            f"it cannot be processed by this pipeline."
        )

    # FIX 1 — real approved-treatment status from OT knownDrugs
    drug_info = get_disease_known_drugs(efo_id)
    has_approved: Optional[bool] = drug_info.get("has_approved_treatment")
    approved_drug_names: list = drug_info.get("approved_drug_names", [])

    # BENCHMARK HOLDOUT: redact the held-out drug from the approved-names list
    # before it feeds precedent target discovery OR unmet-need scoring. If the
    # held-out drug was the only approved treatment, the blind world has no
    # approved therapy — flip has_approved accordingly.
    if _holdout.is_active() and approved_drug_names:
        approved_drug_names = redact_holdout_names(approved_drug_names)
        if not approved_drug_names and has_approved is True:
            has_approved = False
            _log(
                f"  Benchmark holdout: has_approved_treatment → False "
                f"(only approved drug(s) held out for EFO {efo_id})"
            )

    # PARENT-UMBRELLA SUPPLEMENT for pharmacological-precedent drug-indication lookup.
    #
    # Approved-drug indications in Open Targets are sometimes linked only to an
    # umbrella disease EFO (e.g. the broad "pulmonary arterial hypertension" EFO
    # carries sildenafil/Revatio), not to the specific in-universe subtype EFO
    # (e.g. "idiopathic pulmonary arterial hypertension" only has SELEXIPAG and
    # EPOPROSTENOL SODIUM).  Walk up to immediate parent EFOs unconditionally and
    # collect any ADDITIONAL approved drugs not already linked to the specific EFO.
    # Those extra drugs are run through the same ChEMBL mechanism lookup, and any
    # new targets found are tagged "pharmacological_precedent_via_parent_umbrella"
    # so they are auditable and distinguishable from a direct subtype-level match.
    #
    # The umbrella-scoring guard (above) is untouched — umbrella terms still cannot
    # be selected as the disease for genetic-association ranking.  This supplement is
    # for drug-indication lookup only.
    umbrella_approved_drug_names: list = []
    umbrella_efo_id: Optional[str] = None
    _specific_drug_set = {n.upper() for n in approved_drug_names}
    for _parent in get_disease_parents(efo_id):
        _parent_efo = _parent.get("id", "")
        if not _parent_efo:
            continue
        # Breadth filter: skip parents that aggregate too many descendant diseases
        # to yield disease-specific pharmacological signals.  Fail-closed on API
        # error (None) — skip the supplement rather than allow an unverified
        # parent to contribute false-positive targets.
        _desc_count = get_disease_descendant_count(_parent_efo)
        if _desc_count is None or _desc_count > PARENT_MAX_DESCENDANTS:
            _log(
                f"  Parent-umbrella supplement: skipping EFO {_parent_efo} "
                f"('{_parent.get('name', '')}') — "
                + (
                    f"descendant count unavailable (API error), skipping to be safe"
                    if _desc_count is None
                    else f"too broad ({_desc_count} descendants > {PARENT_MAX_DESCENDANTS} threshold)"
                )
            )
            continue
        _parent_drugs = get_disease_known_drugs(_parent_efo)
        _parent_names = _parent_drugs.get("approved_drug_names", [])
        # Benchmark holdout applies to parent-umbrella names too — the held-out
        # drug must not re-enter via an umbrella EFO's approval list.
        if _holdout.is_active() and _parent_names:
            _parent_names = redact_holdout_names(_parent_names)
        # Restrict to drugs not already covered by the specific subtype's EFO list.
        _additional = [n for n in _parent_names if n.upper() not in _specific_drug_set]
        if _additional:
            umbrella_approved_drug_names = _additional
            umbrella_efo_id = _parent_efo
            _log(
                f"  Parent-umbrella drug supplement: EFO {efo_id} has "
                f"{len(approved_drug_names)} approved drug(s); parent EFO "
                f"{_parent_efo} ('{_parent.get('name', '')}') adds "
                f"[{', '.join(_additional[:5])}"
                f"{'…' if len(_additional) > 5 else ''}] — "
                f"targets tagged pharmacological_precedent_via_parent_umbrella"
            )
            break  # take the first parent that has additional approved-drug links

    # Update has_approved_treatment when the parent-umbrella walk found approved drugs.
    # Previously this data only fed pharmacological target discovery (Path B-ext);
    # now it also corrects unmet_need_score for diseases where OT links the approved
    # drug to a parent-category EFO rather than the specific subtype EFO.
    if umbrella_approved_drug_names and has_approved is not True:
        has_approved = True
        approved_drug_names = approved_drug_names + umbrella_approved_drug_names
        _log(
            f"  has_approved_treatment updated → True via parent-umbrella "
            f"EFO {umbrella_efo_id} (drugs: {umbrella_approved_drug_names[:5]})"
        )

    # ot_treatment_unconfirmed: True when has_approved is still False after both
    # the specific-EFO and parent-umbrella checks, AND the disease name carries a
    # legacy numbered or DYT-style designation — a common signal that OT may be
    # using a different/newer name and the drug link is simply absent from its data.
    ot_treatment_unconfirmed = (
        has_approved is False
        and not umbrella_approved_drug_names
        and bool(_LEGACY_NAME_RE.search(disease_name))
    )
    if ot_treatment_unconfirmed:
        _log(
            f"  ot_treatment_unconfirmed=True for '{disease_name}': "
            f"no approved-drug links in OT at specific or parent-umbrella level, "
            f"and name suggests a legacy numbered/DYT classification — "
            f"verify treatment status independently."
        )

    # FIX 3 — best-effort prevalence from Orphadata epidemiology
    orpha_code = disease.get("orpha_code")
    prevalence: Optional[float] = None
    if orpha_code:
        prevalence = get_disease_prevalence(orpha_code)

    targets = get_target_disease_score(efo_id)

    # Path A — genetic associations from Open Targets (gate: association_score >= 0.1)
    genetic_targets = [
        {**t, "target_discovery_method": "genetic_association"}
        for t in targets if t.get("association_score", 0.0) >= 0.1
    ][:TOP_TARGETS_PER_DISEASE]

    # Path B — pharmacological precedent: approved-drug MOA targets from ChEMBL.
    # Uses approved_drug_names already fetched from OT (avoids EFO/MONDO format issues).
    # Only adds targets not already covered by the OT genetic pool (dedup by UniProt ID).
    pharm_targets = get_pharmacological_targets_for_disease(
        efo_id, approved_drug_names=approved_drug_names or None
    )
    seen_uniprots = {t.get("uniprot_id") for t in genetic_targets if t.get("uniprot_id")}
    new_pharm = [
        t for t in pharm_targets
        if t.get("uniprot_id") and t.get("uniprot_id") not in seen_uniprots
    ]

    # Path B-ext — pharmacological precedent via parent umbrella EFO.
    # Only runs when the specific subtype had no approved-drug links and a parent
    # umbrella EFO did (umbrella_approved_drug_names populated above).
    if umbrella_approved_drug_names:
        _umbrella_pharm_raw = get_pharmacological_targets_for_disease(
            umbrella_efo_id, approved_drug_names=umbrella_approved_drug_names
        )
        _seen_after_direct = seen_uniprots | {t.get("uniprot_id") for t in new_pharm
                                              if t.get("uniprot_id")}
        _umbrella_pharm = [
            _tag_umbrella_precedent(t)
            for t in _umbrella_pharm_raw
            if t.get("uniprot_id") and t.get("uniprot_id") not in _seen_after_direct
        ]
        if _umbrella_pharm:
            _log(
                f"  Parent-umbrella precedent targets added: "
                f"{[t['target_symbol'] for t in _umbrella_pharm]}"
            )
        new_pharm = new_pharm + _umbrella_pharm

    top_targets = genetic_targets + new_pharm

    # Path C — disease-process / mechanism-class targets from Europe PMC.
    # This lane is disease-only, holdout-aware, and never queries a drug name.
    # It widens the mechanism object beyond single causal proteins (e.g.
    # channel families, mitotic spindle, nucleotide metabolism).
    process_env = discover_disease_process_targets(disease_name)
    process_targets: list[dict[str, Any]] = []
    if process_env.get("status") == "ok":
        existing_by_uniprot = {
            t.get("uniprot_id"): t
            for t in top_targets if t.get("uniprot_id")
        }
        for p in process_env.get("targets", []):
            uid = p.get("uniprot_id")
            if not uid:
                continue
            if uid in existing_by_uniprot:
                # Independent literature support augments an existing genetic
                # or precedent target without replacing its discovery method
                # or duplicating the protein in the pursuit pool.
                existing = existing_by_uniprot[uid]
                existing["mechanism_class"] = p.get("mechanism_class")
                existing["therapeutic_role"] = p.get("therapeutic_role")
                existing["process_support"] = p.get("supporting_records", [])
                existing["process_query"] = p.get("query")
                existing["process_source_status"] = process_env.get("status")
                existing["process_ontology_version"] = p.get(
                    "ontology_version")
                existing["process_target_priority"] = p.get(
                    "target_priority", 0)
                existing["process_class_priority"] = p.get("class_priority")
                continue
            process_targets.append({
                "target_symbol": p.get("symbol"),
                "uniprot_id": uid,
                "association_score": PROCESS_EVIDENCE_ASSOC_SCORE,
                "target_discovery_method": "literature_mechanism_class",
                "mechanism_class": p.get("mechanism_class"),
                "therapeutic_role": p.get("therapeutic_role"),
                "process_support": p.get("supporting_records", []),
                "process_query": p.get("query"),
                "process_source_status": process_env.get("status"),
                "process_ontology_version": p.get("ontology_version"),
                "process_target_priority": p.get("target_priority", 0),
                "process_class_priority": p.get("class_priority"),
            })
            existing_by_uniprot[uid] = process_targets[-1]
        if process_targets:
            _log(
                "  Literature mechanism-class targets added: "
                f"{[t['target_symbol'] for t in process_targets]}"
            )
    elif process_env.get("status") not in ("empty", None):
        _log(
            "  WARN Europe PMC mechanism lane "
            f"{process_env.get('status')}: {process_env.get('error')}"
        )
    top_targets = top_targets + process_targets

    if not top_targets:
        raise RuntimeError(
            f"Open Targets returned no genetically-associated targets for '{disease_name}' "
            f"(EFO {efo_id}) and no approved-drug MOA targets were found in ChEMBL; "
            f"there is nothing to score."
        )
    if new_pharm:
        _log(
            f"  Pharmacological-precedent targets added: "
            f"{[t['target_symbol'] for t in new_pharm]}"
        )

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
            approved_drug_names=approved_drug_names,
            ot_treatment_unconfirmed=ot_treatment_unconfirmed,
        ))

    rows.sort(key=lambda x: (x["tractability_score"] + x["unmet_need_score"]), reverse=True)
    # F2 mechanistic-convergence cap (rank demotion only, scores unchanged).
    rows = _apply_mechanistic_convergence_cap(rows, log=_log)

    # Post-resolution sanity check: compare OT's canonical name for the chosen EFO
    # against the Orphanet official name (disease_name).  Attaches a Limitations
    # warning to every row when the names are in the partial-mismatch band
    # (0 < overlap < 0.5).  Hard-stop (overlap == 0) already raised above.
    _mismatch_warn = _efo_name_mismatch_warning(disease_name, efo_id)
    if _mismatch_warn:
        for row in rows:
            row["efo_name_mismatch_warning"] = _mismatch_warn

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


def _enrich_approved_via_parents(
    efo_id: str,
    has_approved: Optional[bool],
    approved_drug_names: list,
) -> tuple:
    """
    Walk up to immediate parent EFOs and look for approved drugs not already
    linked to the specific subtype EFO.  If any are found, flip has_approved
    to True and append the parent drugs to approved_drug_names.

    Uses the same PARENT_MAX_DESCENDANTS breadth filter as the manual-mode
    parent-umbrella supplement so the two paths stay consistent.

    Called by both the sweep (run()) and manual-mode (select_for_disease) paths
    to ensure has_approved_treatment — and therefore unmet_need_score — reflects
    parent-level drug-indication data, not just the specific-subtype EFO.

    Returns:
        (updated_has_approved, updated_approved_drug_names,
         umbrella_efo_id | None, umbrella_drug_names)
    """
    if has_approved is True:
        return has_approved, approved_drug_names, None, []

    specific_drug_set = {n.upper() for n in approved_drug_names}
    umbrella_efo_id: Optional[str] = None
    umbrella_drug_names: list = []

    for parent in get_disease_parents(efo_id):
        parent_efo = parent.get("id", "")
        if not parent_efo:
            continue
        desc_count = get_disease_descendant_count(parent_efo)
        if desc_count is None or desc_count > PARENT_MAX_DESCENDANTS:
            continue
        parent_drugs = get_disease_known_drugs(parent_efo)
        additional = [
            n for n in parent_drugs.get("approved_drug_names", [])
            if n.upper() not in specific_drug_set
        ]
        if additional:
            umbrella_efo_id = parent_efo
            umbrella_drug_names = additional
            break

    if umbrella_drug_names:
        has_approved = True
        approved_drug_names = approved_drug_names + umbrella_drug_names

    return has_approved, approved_drug_names, umbrella_efo_id, umbrella_drug_names


def _score_pair(
    disease_name: str,
    target: dict[str, Any],
    association_score: float,
    has_approved_treatment: Optional[bool],
    prevalence: Optional[float],
    orpha_code: Optional[str] = None,
    disease_source: str = "orphanet",
    approved_drug_names: Optional[list] = None,
    ot_treatment_unconfirmed: bool = False,
) -> dict[str, Any]:
    """
    Compute all raw numbers and both scores for one (disease, target) pair.

    FIX 4 — tractability_score is multiplied by the OT association_score (0–1)
    so targets with weak disease-specificity (e.g. broad oncology targets that
    appear in a rare-disease association list with score 0.2) are discounted
    relative to targets with strong, disease-specific evidence.

    The raw ChEMBL/AFDB tractability is preserved in the output so the
    multiplication is auditable: tractability_score = raw_tractability × assoc.
    """

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

    raw_tractability = compute_tractability_score(
        chembl_count=chembl_data.get("count", 0),
        median_pchembl=chembl_data.get("median_pchembl"),
        plddt=afdb_data.get("mean_pLDDT"),
        has_prior_failure=trial_data.get("has_negative_repurposing_result", False),
    )
    # Weight tractability by disease-specificity (FIX 4).
    # assoc is already in [0,1]; gate upstream ensures >= 0.1.
    tractability = round(raw_tractability * association_score, 4)

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
        "approved_drug_names": approved_drug_names or [],
        "prevalence_per_million": prevalence,
        "treatment_status_needs_review": has_approved_treatment is None,
        "ot_treatment_unconfirmed": ot_treatment_unconfirmed,
        "raw_tractability_score": raw_tractability,
        "tractability_score": tractability,
        "unmet_need_score": unmet_need,
        "target_discovery_method": target.get("target_discovery_method", "genetic_association"),
        "mechanism_class": target.get("mechanism_class"),
        "therapeutic_role": target.get("therapeutic_role", "disease_modifying"),
        "process_support": target.get("process_support", []),
        "process_query": target.get("process_query"),
        "process_source_status": target.get("process_source_status"),
        "process_ontology_version": target.get("process_ontology_version"),
        "process_target_priority": target.get("process_target_priority"),
        "process_class_priority": target.get("process_class_priority"),
    }


def select_source_diverse_targets(
    rows: list[dict[str, Any]],
    cap: int,
    *,
    process_class_slots: int = 2,
) -> list[dict[str, Any]]:
    """Preserve ranked targets while reserving canonical process-class coverage.

    Candidate scores and the input row order are never changed. Up to two slots
    are reserved for priority-0 canonical targets from distinct, cited process
    classes; all remaining slots are filled by the original target ranking.
    """
    if cap <= 0 or not rows:
        return []
    ranked = list(rows)
    canonical = sorted(
        (
            row for row in ranked
            if row.get("mechanism_class")
            and row.get("process_support")
            and int(row.get("process_target_priority") or 0) == 0
        ),
        key=lambda row: (
            int(row.get("process_class_priority")
                if row.get("process_class_priority") is not None else 10_000),
            ranked.index(row),
        ),
    )
    reserved: list[dict[str, Any]] = []
    seen_classes: set[str] = set()
    for row in canonical:
        cls = str(row.get("mechanism_class"))
        if cls in seen_classes:
            continue
        reserved.append(row)
        seen_classes.add(cls)
        if len(reserved) >= min(process_class_slots, cap):
            break

    selected_ids = {id(row) for row in reserved}
    for row in ranked:
        if len(selected_ids) >= cap:
            break
        selected_ids.add(id(row))
    return [row for row in ranked if id(row) in selected_ids][:cap]


# ── F2 — precedent calibration (pre-registered constants; see
# validation/f2_precedent_calibration_justification.md — do not tune per case) ──

#: A precedent-only target may not outrank the best genetic target whose OT
#: association meets this threshold (Open Targets' moderate-association boundary).
GENETIC_CONVERGENCE_THRESHOLD: float = 0.50

#: Discovery methods carrying pharmacological-precedent evidence only.
_PRECEDENT_ONLY_METHODS = {
    "pharmacological_precedent",
    "pharmacological_precedent_via_parent_umbrella",
}


def _tag_umbrella_precedent(t: dict[str, Any]) -> dict[str, Any]:
    """Re-tag a parent-umbrella precedent target with the F2 demoted score.

    The umbrella import is indication-adjacent (approval for a parent disease
    concept), a strictly weaker claim than a direct disease-specific approval
    link — so it scores PHARM_PRECEDENT_UMBRELLA_ASSOC_SCORE (0.70), not the
    direct-precedent 0.90 stamped by the ChEMBL lookup.
    """
    return {
        **t,
        "target_discovery_method": "pharmacological_precedent_via_parent_umbrella",
        "association_score": PHARM_PRECEDENT_UMBRELLA_ASSOC_SCORE,
    }


def _apply_mechanistic_convergence_cap(
    rows: list[dict[str, Any]],
    log=None,
) -> list[dict[str, Any]]:
    """F2 mechanistic-convergence cap: when a genetically supported target
    (OT association >= GENETIC_CONVERGENCE_THRESHOLD) exists in the considered
    set, precedent-only targets may not outrank the best such genetic target.

    Rank demotion, NOT exclusion: capped rows keep their scores unchanged, are
    flagged ``precedent_capped=True`` for dossier disclosure, keep their
    relative order, and are inserted immediately after the best qualifying
    genetic row. When no qualifying genetic target exists (non-monogenic or
    genetically unmapped indications), precedent still decides — rows are
    returned untouched. ``rows`` must be pre-sorted best-first.
    """
    best_genetic_idx = next(
        (i for i, r in enumerate(rows)
         if r.get("target_discovery_method") == "genetic_association"
         and r.get("ot_association_score", 0.0) >= GENETIC_CONVERGENCE_THRESHOLD),
        None,
    )
    if best_genetic_idx is None:
        return rows
    best_row = rows[best_genetic_idx]
    best_key = best_row["tractability_score"] + best_row["unmet_need_score"]

    out: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for r in rows:
        if (r.get("target_discovery_method") in _PRECEDENT_ONLY_METHODS
                and r["tractability_score"] + r["unmet_need_score"] >= best_key):
            r["precedent_capped"] = True
            deferred.append(r)
        else:
            out.append(r)
    if not deferred:
        return rows

    insert_at = out.index(best_row) + 1
    capped = out[:insert_at] + deferred + out[insert_at:]
    if log:
        log(
            f"  F2 mechanistic-convergence cap: precedent-only target(s) "
            f"{[r['target_symbol'] for r in deferred]} demoted below best "
            f"genetic target {best_row['target_symbol']} "
            f"(assoc {best_row['ot_association_score']} >= "
            f"{GENETIC_CONVERGENCE_THRESHOLD}); scores unchanged, rank only"
        )
    return capped


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
            # temperature=0: narrative-only, does not affect scores or rankings,
            # but pinned for overall run reproducibility.
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        block = message.content[0]
        return block.text if block.type == "text" else str(block)
    except Exception as e:
        return f"[LLM narration failed: {e}]"


def _print_sentinel_disease_comparison(scored_pairs: list[dict[str, Any]]) -> None:
    """
    Print unmet_need_score for sentinel diseases so Fix 2 can be validated.
    Before the fix all had a constant 0.35 (has_approved_treatment=None, no
    prevalence).  After the fix True→0.0 and False→1.0 so diseases with approved
    treatments score lower and diseases without them score higher.
    """
    SENTINELS = {"pompe", "gaucher", "fabry", "cystic fibrosis", "wilson"}
    found: list[dict[str, Any]] = []
    for row in scored_pairs:
        name_lower = row.get("disease_name", "").lower()
        if any(s in name_lower for s in SENTINELS):
            found.append(row)

    if not found:
        _log("  (none of the sentinel diseases appeared in scored pairs)")
        return

    seen: set[str] = set()
    for row in found:
        key = (row.get("disease_name", ""), row.get("target_symbol", ""))
        if key in seen:
            continue
        seen.add(key)
        _log(
            f"  {row['disease_name']} / {row['target_symbol']:12s} "
            f"has_approved={str(row.get('has_approved_treatment')):5s}  "
            f"prevalence_per_M={str(row.get('prevalence_per_million')):6s}  "
            f"unmet_need={row['unmet_need_score']:.4f}  "
            f"raw_tractability={row.get('raw_tractability_score', '?')}  "
            f"tractability={row['tractability_score']:.4f}  "
            f"assoc={row['ot_association_score']:.4f}"
        )


def run() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    candidates = _build_candidate_universe()

    n_total = len(candidates)
    n_resolved_efo = 0
    n_passed_gate = 0
    n_has_approved_flipped = 0
    n_treatment_unconfirmed = 0

    _log(f"Total candidate diseases in universe: {n_total}")
    _log("Resolving EFO IDs and fetching Open Targets associations …")

    scored_pairs: list[dict[str, Any]] = []
    total = len(candidates)

    for idx, disease in enumerate(candidates, 1):
        disease_name = disease["name"]
        _log(f"  [{idx}/{total}] {disease_name}")

        efo_id = _resolve_efo_id(disease_name)
        if not efo_id:
            _log(f"    → no EFO ID found (tried official name + prefix-stripped), skipping")
            continue

        n_resolved_efo += 1

        # Post-resolution EFO name sanity check (sweep mode).
        # Compute overlap once; skip the entire disease on a 0% hard mismatch
        # (wrong OT node — scoring would reflect the wrong biology).
        # Attach a Limitations warning for partial mismatches (0 < overlap < 0.5).
        _sweep_overlap = _efo_name_overlap(disease_name, efo_id)
        if _sweep_overlap is not None and _sweep_overlap <= _EFO_HARD_STOP_THRESHOLD:
            _ot_canonical = get_ot_canonical_disease_name(efo_id) or efo_id
            _log(
                f"    → EFO HARD MISMATCH: '{disease_name}' resolved to {efo_id} "
                f"(OT canonical: '{_ot_canonical}', 0% token overlap). "
                f"Skipping — scoring this EFO would reflect the wrong disease."
            )
            continue
        _sweep_mismatch_warn = _efo_name_mismatch_warning(disease_name, efo_id)

        # FIX 1 — real approved-treatment status from OT knownDrugs
        drug_info = get_disease_known_drugs(efo_id)
        has_approved: Optional[bool] = drug_info.get("has_approved_treatment")
        approved_drug_names: list = drug_info.get("approved_drug_names", [])

        # Supplement has_approved via parent-umbrella walk.  Fixes diseases where OT
        # links an approved drug to a parent-category EFO rather than the specific
        # subtype EFO, which would otherwise leave has_approved=False and push
        # unmet_need_score to 1.0 (falsely implying no treatment exists).
        has_approved, approved_drug_names, _umbrella_efo, _umbrella_drugs = \
            _enrich_approved_via_parents(efo_id, has_approved, approved_drug_names)
        if _umbrella_drugs:
            n_has_approved_flipped += 1
            _log(
                f"    → has_approved_treatment updated → True via parent EFO "
                f"{_umbrella_efo} (drugs: {_umbrella_drugs[:5]})"
            )

        # Flag diseases where treatment status is still unresolved after all fallbacks
        # and the name suggests a legacy numbered / DYT-style classification.
        ot_treatment_unconfirmed = (
            has_approved is False
            and not _umbrella_drugs
            and bool(_LEGACY_NAME_RE.search(disease_name))
        )
        if ot_treatment_unconfirmed:
            n_treatment_unconfirmed += 1
            _log(
                f"    → ot_treatment_unconfirmed: '{disease_name}' — name suggests "
                f"legacy numbered classification; verify treatment status independently."
            )

        # FIX 3 — best-effort prevalence from Orphadata epidemiology
        orpha_code = disease.get("orpha_code")
        prevalence: Optional[float] = None
        if orpha_code:
            prevalence = get_disease_prevalence(orpha_code)

        targets = get_target_disease_score(efo_id)

        # FIX 4 — gate: drop targets with association_score < 0.1
        top_targets = [
            t for t in targets if t.get("association_score", 0.0) >= 0.1
        ][:TOP_TARGETS_PER_DISEASE]

        if not top_targets:
            _log(f"    → no targets passed association_score >= 0.1 gate for EFO {efo_id}")
            continue

        n_passed_gate += 1
        _log(
            f"    → EFO {efo_id}, {len(top_targets)} targets (gate passed), "
            f"has_approved={has_approved}, prevalence_per_M={prevalence}"
        )

        for target in top_targets:
            pair = _score_pair(
                disease_name=disease_name,
                target=target,
                association_score=target.get("association_score", 0.0),
                has_approved_treatment=has_approved,
                prevalence=prevalence,
                orpha_code=disease.get("orpha_code"),
                disease_source=disease.get("source", "orphanet"),
                approved_drug_names=approved_drug_names,
                ot_treatment_unconfirmed=ot_treatment_unconfirmed,
            )
            if _sweep_mismatch_warn:
                pair["efo_name_mismatch_warning"] = _sweep_mismatch_warn
            scored_pairs.append(pair)

    if not scored_pairs:
        _log("ERROR: no scored pairs — check API connectivity and try again.")
        sys.exit(1)

    scored_pairs.sort(key=lambda x: (x["tractability_score"] + x["unmet_need_score"]), reverse=True)
    # F2 mechanistic-convergence cap. The sweep scores genetic targets only, so
    # this is currently a no-op here — applied in both paths per the F2 document
    # so any future precedent rows in the sweep are capped consistently.
    scored_pairs = _apply_mechanistic_convergence_cap(scored_pairs, log=_log)
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

    _log("\n=== TOP 15 CANDIDATES ===")
    for i, row in enumerate(top30[:15], 1):
        _log(
            f"  #{i:2d}: {row['disease_name'][:40]:<40s} / {row['target_symbol']:<10s} "
            f"tract={row['tractability_score']:.4f} "
            f"(raw={row.get('raw_tractability_score', '?')}, "
            f"assoc={row['ot_association_score']:.3f})  "
            f"unmet={row['unmet_need_score']:.4f}  "
            f"sum={row['tractability_score'] + row['unmet_need_score']:.4f}"
        )

    _log("\n=== SENTINEL DISEASE COMPARISON (FIX 2 validation) ===")
    _log("  Before fix: all had unmet_need_score=0.35 (constant, has_approved_treatment=None)")
    _log("  After fix:")
    _print_sentinel_disease_comparison(scored_pairs)

    _log("\n=== SWEEP DIAGNOSTIC ===")
    _log(f"  Total diseases in universe:                {n_total}")
    _log(f"  Resolved to a valid EFO ID:                {n_resolved_efo}")
    _log(f"  Had ≥1 target with assoc score >= 0.1:     {n_passed_gate}")
    _log(f"  Total (disease, target) pairs scored:      {len(scored_pairs)}")
    _log(f"  Written to output (top-{TOP_N}):               {len(top30)}")
    _log(f"  EFO specificity overrides (rank-1 swapped): see '[open_targets] EFO specificity override' lines above")
    _log(f"  has_approved flipped via parent-umbrella:  {n_has_approved_flipped}")
    _log(f"  ot_treatment_unconfirmed flagged:          {n_treatment_unconfirmed}")

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
