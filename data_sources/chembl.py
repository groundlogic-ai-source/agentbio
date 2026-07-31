"""
ChEMBL bioactivity data.
Resolves UniProt IDs to ChEMBL targets (Homo sapiens only),
then fetches IC50/Ki bioactivity records with confidence_score >= 8.
"""

import math
import statistics
import requests
from typing import Any
from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://www.ebi.ac.uk/chembl/api/data"
UNIPROT_REST = "https://rest.uniprot.org/uniprotkb"

# Scoring constant for the pharmacological-precedent target-discovery path.
# DEFINED HERE, applied at call-time, NEVER baked into the cache payload.
# Changing this value takes effect immediately on the next run without any
# cache flush, because the cache stores only raw ChEMBL mechanism-lookup
# facts (target_symbol, uniprot_id, ensembl_id) and this constant is
# applied as a decoration step after the cache is read.
PHARM_PRECEDENT_ASSOC_SCORE: float = 0.90


def _get_json(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _get_gene_symbol(uniprot_id: str) -> str:
    """
    Look up the HGNC gene symbol for a UniProt accession via UniProt REST API.
    Returns the gene symbol (e.g. 'PDE5A') or the accession itself as fallback.
    Cached with a 30-day TTL.
    """
    cache_key = make_key("uniprot_gene_symbol_v1", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    symbol = uniprot_id  # safe fallback: accession is unique, won't trigger wrong trial matches
    try:
        resp = requests.get(
            f"{UNIPROT_REST}/{uniprot_id}.json",
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            genes = data.get("genes") or []
            if genes:
                gn = genes[0].get("geneName") or {}
                name = gn.get("value") or ""
                if name:
                    symbol = name
    except Exception:
        pass

    cache_set(cache_key, symbol, ttl_days=30)
    return symbol


def _resolve_target_chembl_id(uniprot_id: str) -> list[str]:
    """
    Resolve a UniProt accession to Homo sapiens ChEMBL target IDs.
    Returns a list (may be empty).
    """
    url = f"{BASE_URL}/target.json"
    params = {
        "target_components__accession": uniprot_id,
        "organism": "Homo sapiens",
        "limit": 50,
    }
    data = _get_json(url, params)
    targets = data.get("targets", [])
    ids = []
    for t in targets:
        organism = (t.get("organism") or "")
        tax_id = t.get("tax_id")
        # Strict species match: only keep Homo sapiens (tax_id 9606).
        # Server-side organism filter is belt; this is the suspenders.
        if "Homo sapiens" in organism or tax_id == 9606:
            ids.append(t["target_chembl_id"])
    return ids


def _fetch_assay_confidence(assay_ids: list[str]) -> dict[str, int]:
    """
    Look up confidence_score for a list of assay_chembl_ids.

    NOTE: confidence_score lives on the ChEMBL *assay* resource, not on the
    activity record. The activity endpoint's assay_confidence_score filter is
    silently ignored by the API, so we must join against /assay here ourselves.
    """
    confidence: dict[str, int] = {}
    if not assay_ids:
        return confidence

    url = f"{BASE_URL}/assay.json"
    batch_size = 50
    for i in range(0, len(assay_ids), batch_size):
        batch = assay_ids[i : i + batch_size]
        params = {
            "assay_chembl_id__in": ",".join(batch),
            "only": "assay_chembl_id,confidence_score",
            "limit": 1000,
        }
        data = _get_json(url, params)
        for a in data.get("assays", []):
            aid = a.get("assay_chembl_id")
            score = a.get("confidence_score")
            if aid is not None and score is not None:
                confidence[aid] = int(score)
    return confidence


def _fetch_activities(target_chembl_id: str) -> list[dict[str, Any]]:
    """
    Fetch IC50/Ki activities (pchembl_value present) for a target, then keep
    only those whose assay confidence_score >= 8. Pulls up to 1000 records.
    """
    url = f"{BASE_URL}/activity.json"
    params = {
        "target_chembl_id": target_chembl_id,
        "standard_type__in": "IC50,Ki",
        "pchembl_value__isnull": "false",
        "only": "assay_chembl_id,pchembl_value,standard_type",
        "limit": 1000,
        "offset": 0,
    }
    data = _get_json(url, params)
    activities = data.get("activities", [])
    if not activities:
        return []

    assay_ids = sorted({a["assay_chembl_id"] for a in activities if a.get("assay_chembl_id")})
    confidence = _fetch_assay_confidence(assay_ids)

    return [
        a for a in activities
        if confidence.get(a.get("assay_chembl_id"), 0) >= 8
    ]


def get_target_bioactivity_count(uniprot_id: str) -> dict[str, Any]:
    """
    For a UniProt ID, resolve to Homo sapiens ChEMBL target(s) and return:
      - count: number of qualifying IC50/Ki records (confidence >= 8)
      - median_pchembl: median pChEMBL value across qualifying records
      - target_chembl_ids: list of ChEMBL IDs used
      - pooled_across_multiple_targets: bool flag (True if > 1 target ID matched)
      - low_confidence_excluded: always True (we filter < 8 out)

    IMPORTANT: Values are NOT pooled across different target_chembl_ids silently.
    When pooled_across_multiple_targets is True, interpret with caution.
    """
    cache_key = make_key("get_target_bioactivity_count", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "count": 0,
        "median_pchembl": None,
        "target_chembl_ids": [],
        "pooled_across_multiple_targets": False,
        "low_confidence_excluded": True,
    }

    try:
        target_ids = _resolve_target_chembl_id(uniprot_id)
        if not target_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["target_chembl_ids"] = target_ids
        if len(target_ids) > 1:
            result["pooled_across_multiple_targets"] = True

        all_pchembl: list[float] = []
        total_count = 0
        for tid in target_ids:
            activities = _fetch_activities(tid)
            total_count += len(activities)
            for a in activities:
                try:
                    val = float(a["pchembl_value"])
                    all_pchembl.append(val)
                except (TypeError, ValueError):
                    pass

        result["count"] = total_count
        if all_pchembl:
            result["median_pchembl"] = statistics.median(all_pchembl)

    except Exception as e:
        print(f"[chembl] WARNING: bioactivity query failed for '{uniprot_id}': {e}")
        # Do NOT cache failures: a transient API error would otherwise be
        # cached for 7 days as a legitimate "zero bioactivity" result.
        return result

    cache_set(cache_key, result, ttl_days=7)
    return result


def _fetch_activities_full(target_chembl_id: str) -> list[dict[str, Any]]:
    """
    Like _fetch_activities, but retains molecule identity and structure so callers
    can build a candidate-compound list (not just a count). Keeps only activities
    whose assay confidence_score >= 8 and tags each kept record with `_confidence`.
    Cached with a 7-day TTL so both the count and compound functions share the data.
    """
    cache_key = make_key("_fetch_activities_full_v1", target_chembl_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/activity.json"
    params = {
        "target_chembl_id": target_chembl_id,
        "standard_type__in": "IC50,Ki",
        "pchembl_value__isnull": "false",
        "only": "activity_id,assay_chembl_id,molecule_chembl_id,canonical_smiles,pchembl_value,standard_type",
        "limit": 1000,
        "offset": 0,
    }
    data = _get_json(url, params)
    activities = data.get("activities", [])
    if not activities:
        cache_set(cache_key, [], ttl_days=7)
        return []

    assay_ids = sorted({a["assay_chembl_id"] for a in activities if a.get("assay_chembl_id")})
    confidence = _fetch_assay_confidence(assay_ids)

    kept = []
    for a in activities:
        c = confidence.get(a.get("assay_chembl_id"), 0)
        if c >= 8:
            a["_confidence"] = c
            kept.append(a)

    cache_set(cache_key, kept, ttl_days=7)
    return kept


def _fetch_molecule_meta(molecule_ids: list[str]) -> dict[str, dict[str, Any]]:
    """
    Batch-fetch molecule metadata. Returns {molecule_chembl_id: {max_phase,
    pref_name, canonical_smiles}}. `max_phase == 4` denotes an approved drug.
    """
    meta: dict[str, dict[str, Any]] = {}
    if not molecule_ids:
        return meta
    url = f"{BASE_URL}/molecule.json"
    batch_size = 40
    for i in range(0, len(molecule_ids), batch_size):
        batch = molecule_ids[i : i + batch_size]
        params = {"molecule_chembl_id__in": ",".join(batch), "limit": 1000}
        try:
            data = _get_json(url, params)
        except Exception as e:
            print(f"[chembl] WARNING: molecule meta fetch failed: {e}")
            continue
        for m in data.get("molecules", []):
            mid = m.get("molecule_chembl_id")
            if not mid:
                continue
            struct = m.get("molecule_structures") or {}
            # molecule_hierarchy.parent_chembl_id groups salt forms / hydrates
            # under a single canonical parent so Tanimoto reference exclusion
            # can detect same-moiety variants that differ only in counterion
            # (e.g. physostigmine vs physostigmine salicylate, verapamil vs
            # verapamil hydrochloride).  Stored raw; exclusion logic is in
            # agents/chemist.py.
            hier = m.get("molecule_hierarchy") or {}
            parent_id = hier.get("parent_chembl_id")
            meta[mid] = {
                "max_phase": m.get("max_phase"),
                "pref_name": m.get("pref_name"),
                "canonical_smiles": struct.get("canonical_smiles"),
                "parent_chembl_id": parent_id,
            }
    return meta


def get_approved_drugs_for_target(uniprot_id: str) -> dict[str, Any]:
    """
    Return approved drugs (max_phase >= 4 in ChEMBL) that have a known
    mechanism of action recorded against a Homo sapiens target identified by
    UniProt accession.

    This is a direct factual lookup (no LLM); it queries ChEMBL's mechanism
    endpoint then cross-references molecule metadata for approved status.

    Returns:
      {
        "approved_drugs":       [{molecule_chembl_id, name, max_phase}],
        "approved_drug_count":  int,
        "target_chembl_ids":    [str],
      }
    """
    cache_key = make_key("get_approved_drugs_for_target", uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "approved_drugs": [],
        "approved_drug_count": 0,
        "target_chembl_ids": [],
    }

    try:
        target_ids = _resolve_target_chembl_id(uniprot_id)
        if not target_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["target_chembl_ids"] = target_ids

        mol_ids: set[str] = set()
        for tid in target_ids:
            url = f"{BASE_URL}/mechanism.json"
            params = {"target_chembl_id": tid, "limit": 1000}
            data = _get_json(url, params)
            for m in data.get("mechanisms", []):
                mid = m.get("molecule_chembl_id")
                if mid:
                    mol_ids.add(mid)

        if not mol_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        meta = _fetch_molecule_meta(list(mol_ids))
        approved = []
        for mid, info in meta.items():
            mp = info.get("max_phase")
            try:
                mp_float = float(mp) if mp is not None else 0.0
            except (TypeError, ValueError):
                mp_float = 0.0
            if mp_float >= 4:
                approved.append({
                    "molecule_chembl_id": mid,
                    "name": info.get("pref_name") or mid,
                    "max_phase": mp_float,
                })

        approved.sort(key=lambda x: (x.get("name") or ""))
        result["approved_drugs"] = approved
        result["approved_drug_count"] = len(approved)

    except Exception as e:
        print(f"[chembl] WARNING: approved-drug mechanism lookup failed for '{uniprot_id}': {e}")
        # Do NOT cache failures: a transient error would otherwise be cached
        # for 7 days as "no approved mechanism drugs exist".
        return result

    cache_set(cache_key, result, ttl_days=7)
    return result


def get_target_candidate_compounds(uniprot_id: str, max_compounds: int = 25,
                                   repurposing_only: bool = False) -> dict[str, Any]:
    """
    Return the actual candidate compounds with bioactivity against a target
    (Homo sapiens, IC50/Ki, assay confidence_score >= 8), aggregated per molecule.

    Returns:
      {
        compounds: [ {
          molecule_chembl_id, pref_name, max_phase, canonical_smiles,
          pchembl_value (median over that molecule's qualifying activities),
          confidence_score (max assay confidence among kept activities),
          n_activities,
          source_activity_ids: [int],   # ChEMBL activity ids (provenance)
          source_assay_ids: [str],
          source_chembl_ids: [str],     # molecule + assay ids for provenance
        } ],
        target_chembl_ids: [str],
        pooled_across_multiple_targets: bool,
        repurposing_only: bool,
      }

    Pool construction:
      - repurposing_only=False (default): top `max_compounds` by median pChEMBL
        (unapproved tool compounds included), PLUS every approved drug
        (max_phase >= 4) appended regardless of pChEMBL rank.
      - repurposing_only=True: ONLY approved drugs (max_phase >= 4). The
        top-N-by-pChEMBL unapproved compounds are not pulled at all. Used by
        live API jobs, which only ever want repurposing candidates (drugs with
        an existing human safety profile).

    Mirrors get_target_bioactivity_count's strict filtering — this is the
    compound-level counterpart of that count.
    """
    # v2: approved drugs (max_phase >= 4) are always included regardless of pchembl rank.
    # repurposing_only is part of the cache key so the approved-only and mixed
    # pools never collide in the cache.
    cache_key = make_key("get_target_candidate_compounds_v2", uniprot_id,
                         max_compounds, repurposing_only)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "compounds": [],
        "target_chembl_ids": [],
        "pooled_across_multiple_targets": False,
        "repurposing_only": repurposing_only,
    }

    try:
        target_ids = _resolve_target_chembl_id(uniprot_id)
        if not target_ids:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["target_chembl_ids"] = target_ids
        result["pooled_across_multiple_targets"] = len(target_ids) > 1

        by_mol: dict[str, dict[str, Any]] = {}
        for tid in target_ids:
            for a in _fetch_activities_full(tid):
                mid = a.get("molecule_chembl_id")
                if not mid:
                    continue
                d = by_mol.setdefault(mid, {
                    "molecule_chembl_id": mid,
                    "pchembls": [],
                    "confidences": [],
                    "activity_ids": [],
                    "assay_ids": set(),
                    "canonical_smiles": a.get("canonical_smiles"),
                })
                try:
                    d["pchembls"].append(float(a["pchembl_value"]))
                except (TypeError, ValueError, KeyError):
                    pass
                d["confidences"].append(a.get("_confidence", 0))
                if a.get("activity_id") is not None:
                    d["activity_ids"].append(a["activity_id"])
                if a.get("assay_chembl_id"):
                    d["assay_ids"].add(a["assay_chembl_id"])

        meta = _fetch_molecule_meta(list(by_mol.keys()))

        compounds = []
        for mid, d in by_mol.items():
            m = meta.get(mid, {})
            smiles = m.get("canonical_smiles") or d["canonical_smiles"]
            assay_ids = sorted(d["assay_ids"])
            compounds.append({
                "molecule_chembl_id": mid,
                "pref_name": m.get("pref_name"),
                "max_phase": m.get("max_phase"),
                "canonical_smiles": smiles,
                "pchembl_value": statistics.median(d["pchembls"]) if d["pchembls"] else None,
                "confidence_score": max(d["confidences"]) if d["confidences"] else None,
                "n_activities": len(d["activity_ids"]),
                "source_activity_ids": d["activity_ids"],
                "source_assay_ids": assay_ids,
                "source_chembl_ids": [mid] + assay_ids,
            })

        compounds.sort(key=lambda c: (c["pchembl_value"] or 0.0), reverse=True)

        # Always include FDA-approved drugs (max_phase >= 4) even if their pchembl
        # does not place them in the top-N. This ensures approved drugs (e.g. sildenafil
        # for PDE5A) are never excluded just because tool compounds are more potent.
        approved_ids: set[str] = set()
        approved_compounds: list[dict[str, Any]] = []
        non_approved_compounds: list[dict[str, Any]] = []
        for c in compounds:
            try:
                is_app = c.get("max_phase") is not None and float(c["max_phase"]) >= 4
            except (TypeError, ValueError):
                is_app = False
            if is_app:
                approved_ids.add(c["molecule_chembl_id"])
                approved_compounds.append(c)
            else:
                non_approved_compounds.append(c)

        if repurposing_only:
            # Repurposing-only pool: approved drugs (max_phase >= 4) ONLY. The
            # top-N unapproved tool compounds are dropped entirely, so the
            # downstream stages never rank a research-grade binder that could
            # never be a repurposing candidate.
            selected: list[dict[str, Any]] = approved_compounds
        else:
            # Mixed pool: top-N by pChEMBL from non-approved, then append any
            # approved drug not already included.
            selected = non_approved_compounds[:max_compounds]
            selected_ids = {c["molecule_chembl_id"] for c in selected}
            for c in approved_compounds:
                if c["molecule_chembl_id"] not in selected_ids:
                    selected.append(c)

        result["compounds"] = selected

    except Exception as e:
        print(f"[chembl] WARNING: candidate compound query failed for '{uniprot_id}': {e}")
        # Do NOT cache failures: a transient error would otherwise be cached
        # for 7 days as an empty candidate pool.
        return result

    cache_set(cache_key, result, ttl_days=7)
    return result


def get_drug_indications(molecule_chembl_id: str, limit: int = 200) -> list[str]:
    """
    Return the APPROVED-indication term strings for a ChEMBL molecule from
    the /drug_indication endpoint (efo_term + mesh_heading), phase-4 rows only.

    NOTE: ChEMBL normalizes these to mutation-STRIPPED disease terms (e.g.
    "non-small cell lung carcinoma", never "KRAS G12C-mutated NSCLC"), so they
    are only a secondary input to the mutation-specificity DISCLOSURE scan — the
    FDA label indications text is the primary source.

    Trial-phase indication rows (max_phase_for_ind < 4) are EXCLUDED: their MeSH
    headings can name mutations from unapproved trials (e.g. "Leukemia,
    Myelogenous, Chronic, BCR-ABL Positive" at phase 2 for bortezomib), and
    letting them reach the mutation scan produced false "approved indication
    names mutation X" disclosure flags.
    """
    if not molecule_chembl_id:
        return []
    cache_key = make_key("get_drug_indications_v2", molecule_chembl_id, limit)
    cached = get(cache_key)
    if cached is not None:
        return cached

    terms: list[str] = []
    try:
        data = _get_json(f"{BASE_URL}/drug_indication.json",
                         {"molecule_chembl_id": molecule_chembl_id, "limit": limit})
        for ind in data.get("drug_indications", []):
            try:
                phase = float(ind.get("max_phase_for_ind") or 0)
            except (TypeError, ValueError):
                phase = 0.0
            if phase < 4:
                continue
            for k in ("efo_term", "mesh_heading"):
                v = ind.get(k)
                if v and v not in terms:
                    terms.append(v)
    except Exception as e:
        print(f"[chembl] WARNING: drug_indication query failed for '{molecule_chembl_id}': {e}")
        return []  # do not cache transient failures as "no indications"

    cache_set(cache_key, terms, ttl_days=30)
    return terms


def _find_molecule_chembl_id(drug_name: str) -> str | None:
    """
    Look up a ChEMBL molecule ID for a drug by preferred name.
    Tries pref_name exact match first (guaranteed 1:1 in ChEMBL), then synonym
    match with best-not-first selection: among all synonym-matched molecules,
    prefers the one whose pref_name most closely matches the query drug name
    (exact match first, then highest string-overlap ratio) rather than blindly
    returning mols[0] which may be an unrelated compound that shares a synonym.

    Returns the exact mol ID matched — do NOT resolve to parent, because
    ChEMBL stores mechanism-of-action records on the specific form
    (often the salt, e.g. CHEMBL1737 = sildenafil citrate) rather than
    on the free-base parent (CHEMBL192).
    Returns None if not found.
    """
    # Path 1: pref_name exact match — 1:1 in ChEMBL; if found, always correct.
    data = _get_json(f"{BASE_URL}/molecule.json",
                     {"pref_name__iexact": drug_name, "limit": 5})
    mols = data.get("molecules", [])
    if mols:
        return mols[0].get("molecule_chembl_id")

    # Path 2: synonym match — may return multiple molecules that share this
    # synonym (e.g. salt forms, polymorphs). Pick best-not-first:
    #   (a) exact pref_name match (case-insensitive)
    #   (b) highest character overlap ratio between pref_name and query
    # NOTE: the synonym filter field is `molecule_synonym` — the old
    # `synonym_value` variant returns HTTP 400 for every query (fixed 2026-07-31).
    data = _get_json(f"{BASE_URL}/molecule.json",
                     {"molecule_synonyms__molecule_synonym__iexact": drug_name, "limit": 20})
    mols = data.get("molecules", [])
    if not mols:
        return None

    query_lower = drug_name.lower()

    # (a) Exact pref_name match.
    for mol in mols:
        pref = (mol.get("pref_name") or "").lower()
        if pref == query_lower:
            return mol.get("molecule_chembl_id")

    # (b) Best partial match by longest-common-subsequence ratio (simple overlap).
    def _overlap(mol: dict) -> float:
        pref = (mol.get("pref_name") or "").lower()
        if not pref:
            return 0.0
        common = sum(1 for c in query_lower if c in pref)
        return common / max(len(query_lower), len(pref))

    best = max(mols, key=_overlap)
    return best.get("molecule_chembl_id")


def _fetch_molecule_safety(molecule_chembl_id: str) -> dict[str, Any]:
    """
    Fetch withdrawal and safety flags for a single ChEMBL molecule.

    Fields checked:
      withdrawn_flag     — True if the drug has been withdrawn from any market
      black_box_warning  — True (non-zero) if a black-box / boxed warning exists
      availability_type  — -2=withdrawn from market, -1=discontinued by manufacturer,
                           0=unknown, 1=available

    Cached for 30 days; regulatory status changes rarely.
    Returns safe defaults (False/None) on any API error.
    """
    cache_key = make_key("_fetch_molecule_safety_v1", molecule_chembl_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "withdrawn_flag": False,
        "black_box_warning": False,
        "availability_type": None,
    }
    try:
        data = _get_json(f"{BASE_URL}/molecule/{molecule_chembl_id}")
        result = {
            "withdrawn_flag":    bool(data.get("withdrawn_flag")),
            "black_box_warning": bool(data.get("black_box_warning")),
            "availability_type": data.get("availability_type"),
        }
    except Exception as e:
        print(f"[chembl] WARNING: molecule safety fetch failed for "
              f"'{molecule_chembl_id}': {e}")

    cache_set(cache_key, result, ttl_days=30)
    return result


def get_molecule_safety_flags(
    drug_name: str,
    molecule_chembl_id: str | None = None,
) -> dict[str, Any]:
    """
    Layer 1 structured safety check: query ChEMBL molecule record for
    market-withdrawal and black-box-warning status.

    Lookup order: use `molecule_chembl_id` if provided (saves a name→ID round
    trip); otherwise resolves by pref_name / synonym via _find_molecule_chembl_id.
    The pref_name lookup finds the canonical parent molecule (e.g. CHEMBL436 for
    tolrestat) rather than a salt form, which is where ChEMBL stores the flags.

    Fail-open on API error (returns confirmed=False): a network outage should
    never produce a false-positive safety block.  Layer 2's independent web-search
    check provides the redundant catch in that scenario.

    Returns:
        {
          "confirmed"          : bool — True ONLY for market-withdrawn drugs
                                        (withdrawn_flag=True).  A black-box warning
                                        alone does NOT set confirmed=True; use the
                                        "black_box_advisory" field for disclosure.
          "black_box_advisory" : bool — True when black_box_warning=True but the
                                        drug is NOT market-withdrawn.  Triggers a
                                        prominent disclosure note in the report but
                                        does NOT apply a hard scoring cap (black-box
                                        warnings cover >30% of approved drugs and
                                        do not indicate a drug is unavailable).
          "layer"              : "chembl_structured"
          "chembl_id"          : str | None
          "flag_type"          : "withdrawn_flag" | "black_box_warning" | None
          "availability_type"  : int | None  # -2 withdrawn, -1 discontinued, 1 available
          "source_url"         : str | None  # ChEMBL compound page URL
          "disclosure_text"    : str
        }
    """
    cache_key = make_key("get_molecule_safety_flags_v2", drug_name,
                         molecule_chembl_id or "")
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "confirmed": False,
        "black_box_advisory": False,
        "api_error": False,   # True only when an exception prevented the check
        "layer": "chembl_structured",
        "chembl_id": molecule_chembl_id,
        "flag_type": None,
        "availability_type": None,
        "source_url": None,
        "disclosure_text": (
            "No market withdrawal or black-box warning found in ChEMBL "
            "structured data."
        ),
    }

    try:
        mid = molecule_chembl_id or _find_molecule_chembl_id(drug_name)
        if not mid:
            result["disclosure_text"] = (
                f"'{drug_name}' not found in ChEMBL molecule dictionary — "
                "Layer 1 structured check could not run."
            )
            cache_set(cache_key, result, ttl_days=30)
            return result

        result["chembl_id"] = mid
        source_url = (
            f"https://www.ebi.ac.uk/chembl/compound_report_card/{mid}/"
        )
        result["source_url"] = source_url

        flags = _fetch_molecule_safety(mid)
        result["availability_type"] = flags.get("availability_type")

        if flags.get("withdrawn_flag"):
            result["confirmed"] = True
            result["flag_type"] = "withdrawn_flag"
            result["disclosure_text"] = (
                f"ChEMBL records withdrawn_flag=True for {drug_name} ({mid}): "
                f"this compound has been withdrawn from at least one market. "
                f"Source: {source_url}"
            )
        elif flags.get("black_box_warning"):
            # Black-box warning ≠ market withdrawal.  >30% of FDA-approved drugs
            # carry boxed warnings (warfarin, SSRIs, fluoroquinolones, clozapine,
            # brexanolone, thalidomide+REMS, etc.) and remain fully available.
            # Do NOT hard-cap the score; surface a prominent advisory note only.
            result["confirmed"] = False          # no hard-cap triggered
            result["black_box_advisory"] = True  # disclosure banner in report
            result["flag_type"] = "black_box_warning"
            result["disclosure_text"] = (
                f"ChEMBL records black_box_warning=True for {drug_name} ({mid}): "
                f"this compound carries a regulatory black-box (boxed) warning. "
                f"The drug is still approved and available (not withdrawn). "
                f"Source: {source_url}"
            )

    except Exception as e:
        print(f"[chembl] WARNING: get_molecule_safety_flags failed for "
              f"'{drug_name}': {e}")
        result["api_error"] = True
        result["disclosure_text"] = (
            f"Layer 1 structured check encountered an error for '{drug_name}': "
            f"{e}. Treating as unconfirmed (fail-open). "
            f"Layer 2 web-search will run as redundancy regardless of budget."
        )

    cache_set(cache_key, result, ttl_days=30)
    return result


def get_pharmacological_targets_for_disease(
    disease_efo_id: str,
    approved_drug_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Second target-discovery path: approved-drug mechanism (pharmacological precedent).

    Finds the Homo sapiens SINGLE PROTEIN targets of drugs that Open Targets
    already confirmed are approved for this disease (approved_drug_names from
    get_disease_known_drugs).  Falls back to a ChEMBL /drug_indication query
    using the EFO ID (colon format, e.g. "EFO:0001361") when no drug names are
    provided, though the drug-names path is more reliable.

    Steps (drug-names path):
      1. For each approved drug name: look up ChEMBL molecule ID
      2. /mechanism?molecule_chembl_id=<id> to get MOA target IDs
      3. Batch-resolve target_chembl_ids -> UniProt (Homo sapiens SINGLE PROTEIN)

    Returns [{target_symbol, uniprot_id, ensembl_id='',
               association_score=0.90, target_discovery_method='pharmacological_precedent'}]

    Returns [] gracefully on any API or format error.
    """
    names_key = tuple(sorted(approved_drug_names)) if approved_drug_names else ()
    # Cache key v3: stores ONLY raw mechanism-lookup facts (target_symbol,
    # uniprot_id, ensembl_id). association_score and target_discovery_method
    # are injected fresh from the current code constants at return time.
    # Bumped from v2 which baked association_score into the cache payload —
    # changing the constant then required a manual cache flush to take effect.
    cache_key = make_key("get_pharmacological_targets_for_disease_v3", disease_efo_id, names_key)
    cached = get(cache_key)
    if cached is not None:
        # Decorate with current-code scoring constants, not cached values.
        return [
            {**t,
             "association_score": PHARM_PRECEDENT_ASSOC_SCORE,
             "target_discovery_method": "pharmacological_precedent"}
            for t in cached
        ]

    results: list[dict[str, Any]] = []
    try:
        mol_ids: set[str] = set()

        if approved_drug_names:
            # Primary path: use drug names already confirmed by Open Targets
            for name in approved_drug_names:
                mid = _find_molecule_chembl_id(name)
                if mid:
                    mol_ids.add(mid)
        else:
            # Fallback: ChEMBL drug_indication, EFO colon format, no max_phase filter
            # (max_phase_for_indication is NULL for many approved indications in ChEMBL)
            efo_colon = disease_efo_id.replace("_", ":", 1)
            data = _get_json(f"{BASE_URL}/drug_indication.json", {
                "efo_id": efo_colon,
                "limit": 200,
            })
            for ind in data.get("drug_indications", []):
                mid = ind.get("molecule_chembl_id")
                if mid:
                    mol_ids.add(mid)
            # Filter mol_ids to globally approved (max_phase >= 4) molecules
            if mol_ids:
                meta = _fetch_molecule_meta(list(mol_ids))
                mol_ids = {
                    mid for mid, info in meta.items()
                    if float(info.get("max_phase") or 0) >= 4
                }

        if not mol_ids:
            cache_set(cache_key, results, ttl_days=7)
            return results

        # Get MOA target IDs for each approved drug molecule
        target_chembl_ids: set[str] = set()
        for mol_id in sorted(mol_ids):
            mech_data = _get_json(f"{BASE_URL}/mechanism.json", {
                "molecule_chembl_id": mol_id,
                "limit": 100,
            })
            for m in mech_data.get("mechanisms", []):
                tid = m.get("target_chembl_id")
                if tid:
                    target_chembl_ids.add(tid)

        if not target_chembl_ids:
            cache_set(cache_key, results, ttl_days=7)
            return results

        # Resolve target_chembl_ids -> UniProt (Homo sapiens SINGLE PROTEIN only)
        tgt_data = _get_json(f"{BASE_URL}/target.json", {
            "target_chembl_id__in": ",".join(sorted(target_chembl_ids)),
            "target_type": "SINGLE PROTEIN",
            "organism": "Homo sapiens",
            "limit": 1000,
        })

        seen_uniprot: set[str] = set()
        for tgt in tgt_data.get("targets", []):
            if tgt.get("tax_id") != 9606 and "Homo sapiens" not in (tgt.get("organism") or ""):
                continue
            # Use target_components[].accession — the primary UniProt ChEMBL activity
            # data is indexed under (distinct from target_component_xrefs which may
            # list many isoform accessions, most of which have 0 ChEMBL bioactivity).
            uniprot_id = None
            for comp in tgt.get("target_components", []):
                acc = comp.get("accession")
                if acc:
                    uniprot_id = acc
                    break
            if uniprot_id and uniprot_id not in seen_uniprot:
                seen_uniprot.add(uniprot_id)
                # Use HGNC gene symbol (from UniProt) as target_symbol so that
                # check_prior_trials() matches correctly. The ChEMBL pref_name
                # (e.g. "cGMP-specific 3',5'-cyclic phosphodiesterase") causes
                # false trial-failure matches; gene symbols (e.g. "PDE5A") do not.
                gene_sym = _get_gene_symbol(uniprot_id)
                # Store only raw source facts in the cache payload.
                # association_score and target_discovery_method are NOT stored
                # here — they are applied after the cache is read so that
                # changing PHARM_PRECEDENT_ASSOC_SCORE (or the method label)
                # takes effect immediately without requiring a cache flush.
                results.append({
                    "target_symbol": gene_sym,
                    "ensembl_id": "",
                    "uniprot_id": uniprot_id,
                })

    except Exception as e:
        print(f"[chembl] WARNING: pharmacological target lookup failed "
              f"for '{disease_efo_id}': {e}")

    # Cache the raw facts only (no scoring constants).
    cache_set(cache_key, results, ttl_days=7)
    # Evidentiary principle (stated in advance, not tuned per case):
    # A target reached via an FDA max_phase >= 4 approved drug's confirmed
    # mechanism of action for this exact disease receives
    # PHARM_PRECEDENT_ASSOC_SCORE = 0.90. This reflects direct regulatory and
    # clinical confirmation — the highest level of evidence that a target is
    # relevant to a given disease — and is treated as at least as strong as
    # the maximum plausible genetic-association score Open Targets could return.
    # Do not adjust based on individual validation case outcomes; any revision
    # must be a separate, disclosed methodology decision.
    return [
        {**t,
         "association_score": PHARM_PRECEDENT_ASSOC_SCORE,
         "target_discovery_method": "pharmacological_precedent"}
        for t in results
    ]


def get_drug_action_type(
    drug_name: str,
    target_symbol: str | None = None,
) -> dict[str, Any]:
    """
    Look up the ChEMBL mechanism action_type for a drug, optionally preferring
    a mechanism record that matches a given target gene symbol in its moa text.

    Resolution order:
      1. Resolve drug_name → molecule_chembl_id via pref_name / synonym.
      2. Fetch mechanism records for that mol_id directly.
      3. If empty, try parent_molecule_chembl_id fallback — ChEMBL stores some
         mechanism records on salt forms (e.g. sildenafil citrate CHEMBL1737)
         rather than the free-base parent (CHEMBL192), so querying by
         parent_molecule_chembl_id catches them.
      4. Among records found, prefer one whose mechanism_of_action field
         contains target_symbol (if given); else use the first record.

    Returns:
      {
        "action_type"         : str | None,   # e.g. "INHIBITOR", "AGONIST"
        "mechanism_of_action" : str | None,   # e.g. "Phosphodiesterase 5A inhibitor"
        "molecule_chembl_id"  : str | None,
        "target_chembl_id"    : str | None,
        "source": "exact_target_match" | "any_mechanism" | "not_found",
      }
    Cached 30 days.
    """
    cache_key = make_key("get_drug_action_type_v1", drug_name, target_symbol or "")
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "action_type": None,
        "mechanism_of_action": None,
        "molecule_chembl_id": None,
        "target_chembl_id": None,
        "source": "not_found",
    }

    try:
        mol_id = _find_molecule_chembl_id(drug_name)
        if not mol_id:
            cache_set(cache_key, result, ttl_days=30)
            return result

        result["molecule_chembl_id"] = mol_id

        # Step 2a: mechanism records by molecule_chembl_id
        data = _get_json(f"{BASE_URL}/mechanism.json",
                         {"molecule_chembl_id": mol_id, "limit": 100})
        mechs = data.get("mechanisms", [])

        # Step 2b: fallback — query by parent_molecule_chembl_id.
        # This catches cases where ChEMBL only records mechanism on a
        # salt/specific form while our mol_id lookup returned the free-base
        # parent (e.g. sildenafil CHEMBL192 → citrate CHEMBL1737 has the MoA).
        if not mechs:
            data2 = _get_json(f"{BASE_URL}/mechanism.json",
                              {"parent_molecule_chembl_id": mol_id, "limit": 100})
            mechs = data2.get("mechanisms", [])

        if not mechs:
            cache_set(cache_key, result, ttl_days=30)
            return result

        # Prefer mechanism record whose moa string mentions target_symbol
        best: dict[str, Any] | None = None
        if target_symbol:
            sym_up = target_symbol.upper()
            for m in mechs:
                moa = (m.get("mechanism_of_action") or "").upper()
                if sym_up in moa:
                    best = m
                    result["source"] = "exact_target_match"
                    break

        if best is None:
            best = mechs[0]
            result["source"] = "any_mechanism"

        result["action_type"] = best.get("action_type")
        result["mechanism_of_action"] = best.get("mechanism_of_action")
        result["target_chembl_id"] = best.get("target_chembl_id")

    except Exception as e:
        print(f"[chembl] WARNING: get_drug_action_type failed for '{drug_name}': {e}")

    cache_set(cache_key, result, ttl_days=30)
    return result


def get_drug_mechanism_targets_for_audit(
    drug_name: str,
    chembl_id: str | None = None,
) -> list[str]:
    """Return the mechanism-of-action strings this drug has in ChEMBL.

    Used by the audit endpoint to explain why a drug is absent from an
    AgentBio candidate pool (i.e. to show which proteins it actually targets,
    versus which protein AgentBio selected for the queried disease).

    Returns strings like "Phosphodiesterase 5A inhibitor", "CRBN modulator".
    Returns empty list on lookup failure or not-found.
    """
    mid = chembl_id or _find_molecule_chembl_id(drug_name)
    if not mid:
        return []
    try:
        data = _get_json(f"{BASE_URL}/mechanism.json",
                         {"molecule_chembl_id": mid, "limit": 50})
        mechs = data.get("mechanisms", [])
        if not mechs:
            # Fallback: parent molecule
            data2 = _get_json(f"{BASE_URL}/mechanism.json",
                               {"parent_molecule_chembl_id": mid, "limit": 50})
            mechs = data2.get("mechanisms", [])
        seen: set[str] = set()
        result: list[str] = []
        for m in mechs:
            moa = (m.get("mechanism_of_action") or "").strip()
            if moa and moa not in seen:
                seen.add(moa)
                result.append(moa)
        return result
    except Exception:
        return []


def get_molecule_data(drug_name: str) -> dict[str, Any]:
    """
    Drug-level ChEMBL lookup: molecule_type, max_phase (global), oral flag.

    Returns:
      {
        chembl_id: str | None,
        molecule_type: str | None,   # "Small molecule", "Antibody", "Enzyme", etc.
        max_phase: int | None,        # 0-4 (highest phase globally in ChEMBL)
        oral: bool | None,
        resolved: bool,
      }

    Cached 30 days (7 on miss). Returns resolved=False on any API error or not-found.
    molecule_type distinguishes small-molecule vs biologic; max_phase captures how
    far a compound has progressed across all indications in ChEMBL.
    """
    cache_key = make_key("get_molecule_data_v2", drug_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any] = {
        "chembl_id": None,
        "molecule_type": None,
        "max_phase": None,
        "oral": None,
        "resolved": False,
    }

    try:
        chembl_id = _find_molecule_chembl_id(drug_name)
        if not chembl_id:
            cache_set(cache_key, result, ttl_days=7)
            return result

        result["chembl_id"] = chembl_id
        mol = _get_json(f"{BASE_URL}/molecule/{chembl_id}")
        if mol:
            result["molecule_type"] = mol.get("molecule_type")
            mp = mol.get("max_phase")
            result["max_phase"] = int(float(mp)) if mp is not None else None
            result["oral"] = bool(mol.get("oral")) if mol.get("oral") is not None else None
            result["resolved"] = True

    except Exception as e:
        print(f"[chembl] WARNING: get_molecule_data failed for '{drug_name}': {e}")

    cache_set(cache_key, result, ttl_days=30)
    return result
