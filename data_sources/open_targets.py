"""
Open Targets Platform GraphQL API.
Resolves disease names/IDs to EFO IDs, fetches target-disease associations,
and queries known clinical drugs per disease.
"""

import requests
from typing import Any, Optional
from cache.cache import get, set as cache_set, make_key

GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"


def _graphql(query: str, variables: dict) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def search_disease_efo(disease_name: str) -> Optional[str]:
    """
    Search Open Targets for a disease by name and return its EFO ID.

    Among the top search results, prefers the most disease-specific match
    (fewest descendants in the OT ontology) over a broad umbrella term.
    Blindly taking the top-ranked hit often lands on a broad category (e.g.
    "inherited dystonia" for the query "Dystonia 14") because OT's search
    ranking is by text relevance, not ontology depth.  A leaf-level EFO gives
    more accurate has_approved_treatment and association-score data.

    Logs an auditable override line when the chosen EFO differs from rank-1.
    Cache key v3 — bumped from v1 to bust stale broad-EFO entries.

    Returns None if not found.
    """
    cache_key = make_key("search_disease_efo_v3", disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query SearchDisease($q: String!) {
      search(queryString: $q, entityNames: ["disease"], page: {index: 0, size: 8}) {
        hits {
          id
          entity
          name
          score
        }
      }
    }
    """
    try:
        data = _graphql(query, {"q": disease_name})
        hits = data.get("data", {}).get("search", {}).get("hits", [])
        disease_hits = [h for h in hits if h.get("entity") == "disease"]
        if not disease_hits:
            cache_set(cache_key, None, ttl_days=7)
            return None

        # Prefer the most specific (fewest descendants) hit among the top results
        # so that a leaf-level EFO is preferred over a broad umbrella that happens
        # to score higher on text relevance.  get_disease_descendant_count is cached
        # with a 30-day TTL so the overhead is a one-time cost per unique EFO node.
        #
        # Score-ratio guard: only consider a more-specific candidate if its OT
        # relevance score is at least 70% of the top hit's score.  This prevents
        # a far-less-relevant leaf node (unrelated disease that shares a word) from
        # overriding an accurate broader match.
        top_score = disease_hits[0].get("score") or 0.0
        score_floor = top_score * 0.70

        best_idx = 0
        best_desc = get_disease_descendant_count(disease_hits[0]["id"])
        if best_desc is None:
            best_desc = float("inf")  # unknown breadth — keep as candidate but prefer anything known

        if best_desc > 0:  # short-circuit: a leaf (0 descendants) can't be beaten
            for i, candidate in enumerate(disease_hits[1:], 1):
                c_score = candidate.get("score") or 0.0
                if c_score < score_floor:
                    break  # hits are ordered by score — everything after is also below floor
                desc = get_disease_descendant_count(candidate["id"])
                if desc is None:
                    continue  # skip candidates whose breadth can't be verified
                if desc < best_desc:
                    best_idx = i
                    best_desc = desc
                    if best_desc == 0:
                        break  # leaf node — can't do better

        efo_id = disease_hits[best_idx]["id"]
        if best_idx != 0:
            raw_top = disease_hits[0]
            raw_desc = get_disease_descendant_count(raw_top["id"])
            print(
                f"[open_targets] EFO specificity override for '{disease_name}': "
                f"rank-1={raw_top['id']} ('{raw_top['name']}', {raw_desc} desc) "
                f"-> chose {efo_id} ('{disease_hits[best_idx]['name']}', {best_desc} desc)"
            )

        cache_set(cache_key, efo_id, ttl_days=7)
        return efo_id
    except Exception as e:
        print(f"[open_targets] WARNING: EFO search failed for '{disease_name}': {e}")
        return None


def get_target_disease_score(disease_efo_id: str) -> list[dict[str, Any]]:
    """
    Fetch top target-disease associations for a given EFO ID.
    Returns [{target_symbol, ensembl_id, uniprot_id, association_score}] sorted descending.
    """
    cache_key = make_key("get_target_disease_score", disease_efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query DiseaseTargets($efoId: String!, $size: Int!) {
      disease(efoId: $efoId) {
        associatedTargets(page: {index: 0, size: $size}) {
          rows {
            target {
              id
              approvedSymbol
              proteinIds {
                id
                source
              }
            }
            score
          }
        }
      }
    }
    """
    results = []
    try:
        data = _graphql(query, {"efoId": disease_efo_id, "size": 10})
        disease_data = data.get("data", {}).get("disease") or {}
        rows = disease_data.get("associatedTargets", {}).get("rows", [])
        for row in rows:
            target = row.get("target", {})
            ensembl_id = target.get("id", "")
            symbol = target.get("approvedSymbol", "")
            uniprot_id = None
            for pid in target.get("proteinIds", []):
                if pid.get("source") in ("uniprot_swissprot", "uniprot_trembl", "uniprot"):
                    uniprot_id = pid.get("id")
                    break
            results.append({
                "target_symbol": symbol,
                "ensembl_id": ensembl_id,
                "uniprot_id": uniprot_id,
                "association_score": row.get("score", 0.0),
            })
        results.sort(key=lambda x: x["association_score"], reverse=True)
    except Exception as e:
        print(f"[open_targets] WARNING: association query failed for '{disease_efo_id}': {e}")

    cache_set(cache_key, results, ttl_days=7)
    return results


def get_disease_known_drugs(disease_efo_id: str) -> dict[str, Any]:
    """
    Query Open Targets for known clinical-stage drugs for a disease.

    Returns:
        {
          "has_approved_treatment": True | False | None,
          "approved_drug_names":    list[str],
        }

    has_approved_treatment is:
      True  — at least one drug with isApproved=True or maximumClinicalTrialPhase>=4
      False — drugs are in trials for this disease, but none are approved
      None  — the API call itself failed (unknown; treated as missing data)

    Cache key includes "v1" so stale None-entries from before this function
    existed are not confused with "API returned no drugs".
    """
    cache_key = make_key("get_disease_known_drugs_v2", disease_efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    # OT Platform v4: disease.drugAndClinicalCandidates { rows { drug{id name} maxClinicalStage } }
    # maxClinicalStage values: "APPROVAL", "PHASE4", "PHASE3", "PHASE2", "PHASE1", "PRECLINICAL"
    query = """
    query DiseaseDrugs($efoId: String!) {
      disease(efoId: $efoId) {
        drugAndClinicalCandidates {
          rows {
            drug { id name }
            maxClinicalStage
          }
        }
      }
    }
    """
    result: dict[str, Any] = {"has_approved_treatment": None, "approved_drug_names": []}
    try:
        data = _graphql(query, {"efoId": disease_efo_id})
        disease_data = data.get("data", {}).get("disease") or {}
        candidates = disease_data.get("drugAndClinicalCandidates")

        if candidates is None:
            # disease node exists but no clinical candidate entry → no clinical drugs
            result["has_approved_treatment"] = False
        else:
            rows = candidates.get("rows") or []
            approved_names: list[str] = []
            seen: set[str] = set()
            for row in rows:
                stage = (row.get("maxClinicalStage") or "").upper()
                drug = row.get("drug") or {}
                name = drug.get("name") or drug.get("id") or ""
                if stage == "APPROVAL" and name and name not in seen:
                    approved_names.append(name)
                    seen.add(name)
            result["has_approved_treatment"] = bool(approved_names)
            result["approved_drug_names"] = approved_names
    except Exception as e:
        print(f"[open_targets] WARNING: drugAndClinicalCandidates query failed for '{disease_efo_id}': {e}")
        # has_approved_treatment stays None — caller will treat as unknown

    cache_set(cache_key, result, ttl_days=7)
    return result


def get_disease_parents(efo_id: str) -> list[dict[str, Any]]:
    """
    Return the immediate parent EFO terms for a disease in the Open Targets
    disease ontology.

    Used to walk up from a specific in-universe subtype to its parent umbrella
    when the subtype's own EFO has no linked approved-drug indications in OT.
    Canonical example: sildenafil/Revatio is linked to the umbrella
    "pulmonary arterial hypertension" EFO, not to "idiopathic pulmonary
    arterial hypertension" — so the parent walk is needed to surface PDE5A
    via the pharmacological-precedent path.

    Returns [{id, name}] for each parent, or [] if none or on API error.
    Cached with the same 7-day TTL as other OT lookups.
    """
    cache_key = make_key("get_disease_parents_v1", efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query DiseaseParents($efoId: String!) {
      disease(efoId: $efoId) {
        parents {
          id
          name
        }
      }
    }
    """
    results: list[dict[str, Any]] = []
    try:
        data = _graphql(query, {"efoId": efo_id})
        disease_data = data.get("data", {}).get("disease") or {}
        parents = disease_data.get("parents") or []
        for p in parents:
            pid = p.get("id")
            pname = p.get("name") or ""
            if pid:
                results.append({"id": pid, "name": pname})
    except Exception as e:
        print(f"[open_targets] WARNING: disease parents query failed for '{efo_id}': {e}")

    cache_set(cache_key, results, ttl_days=7)
    return results


def get_disease_descendant_count(efo_id: str) -> Optional[int]:
    """
    Return the total number of descendant diseases for a given EFO ID in the
    Open Targets disease ontology.

    Used as a breadth filter in the parent-umbrella drug supplement:
    parents that aggregate hundreds or thousands of distinct disorders are too
    non-specific to yield useful pharmacological-precedent signals.  N=100 is
    the calibrated threshold — calibrated from real descendant counts:
      largest known-good parent  (acute myeloid leukemia)      = 87
      smallest known-bad parent  (inborn error of immunity)    = 228

    Returns None on API error; the caller treats None as "too broad" (fail-closed:
    suppress the supplement rather than allow an unverified parent through).
    Cached for 30 days; the disease ontology changes only a few times a year.
    """
    cache_key = make_key("get_disease_descendant_count_v1", efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    query = """
    query DiseaseDescendants($efoId: String!) {
      disease(efoId: $efoId) {
        descendants
      }
    }
    """
    result: Optional[int] = None
    try:
        data = _graphql(query, {"efoId": efo_id})
        disease_data = data.get("data", {}).get("disease") or {}
        descendants = disease_data.get("descendants") or []
        result = len(descendants)
    except Exception as e:
        print(f"[open_targets] WARNING: descendant count query failed for '{efo_id}': {e}")

    cache_set(cache_key, result, ttl_days=30)
    return result


def get_ot_canonical_disease_name(efo_id: str) -> Optional[str]:
    """
    Return Open Targets' own canonical name for an EFO/MONDO ID, or None.

    Used as a post-resolution sanity check: if the OT canonical name for the
    resolved EFO shares little token overlap with the originally queried disease
    name, the resolution may have landed on the wrong ontology node.

    Cached with a 30-day TTL (ontology names change very rarely).
    Sentinel "" is stored when OT has no name for the ID, to avoid repeat calls.
    """
    cache_key = make_key("get_ot_canonical_disease_name_v1", efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached if cached else None  # "" sentinel → None

    query = """
    query DiseaseCanonicalName($efoId: String!) {
        disease(efoId: $efoId) {
            name
        }
    }
    """
    name: Optional[str] = None
    try:
        data = _graphql(query, {"efoId": efo_id})
        name = (data.get("data", {}).get("disease") or {}).get("name")
    except Exception as e:
        print(f"[open_targets] WARNING: canonical name lookup failed for '{efo_id}': {e}")

    cache_set(cache_key, name if name is not None else "", ttl_days=30)
    return name


def get_disease_orphanet_code(efo_id: str) -> Optional[str]:
    """
    Return the Orphanet code (e.g. "365") for an EFO disease ID, or None.

    Uses OT's dbXRefs field which includes entries like "Orphanet:365".
    Result is cached with a sentinel "" for "no Orphanet mapping" to avoid
    repeated network calls when the disease has no Orphanet cross-reference.
    """
    cache_key = make_key("get_disease_orphanet_code_v1", efo_id)
    cached = get(cache_key)
    if cached is not None:
        return cached if cached else None  # "" sentinel → None

    query = """
    query DiseaseCrossRef($efoId: String!) {
        disease(efoId: $efoId) {
            dbXRefs
        }
    }
    """
    orpha_code: Optional[str] = None
    try:
        data = _graphql(query, {"efoId": efo_id})
        xrefs = (data.get("data", {}).get("disease") or {}).get("dbXRefs") or []
        for xref in xrefs:
            if isinstance(xref, str) and xref.startswith("Orphanet:"):
                orpha_code = xref.split(":", 1)[1]
                break
    except Exception as e:
        print(f"[open_targets] WARNING: dbXRefs lookup failed for '{efo_id}': {e}")

    cache_set(cache_key, orpha_code if orpha_code is not None else "", ttl_days=7)
    return orpha_code
