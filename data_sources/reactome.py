"""
Reactome Content Service data source.

Provides pathway-neighbor discovery for a given UniProt protein:
  - Fetches the Reactome pathways the protein participates in.
  - For each pathway (tightest first, ranked by maxDepth ascending), fetches
    the co-participating UniProt-mapped proteins.
  - Deduplicates and ranks by co-occurrence frequency.
  - Annotates each neighbor with a specificity tier so downstream agents can
    distinguish direct reaction partners from broad metabolic groupings.
  - Caps at max_neighbors to avoid very broad neighbor sets from large complexes.

Verified Reactome Content Service endpoints (July 2026):
  GET /ContentService/data/mapping/UniProt/{accession}/pathways
      -> list of Pathway objects with {stId, displayName, maxDepth, ...}
  GET /ContentService/data/participants/{stId}/referenceEntities
      -> list of reference entities with {databaseName, identifier, geneName, ...}
      Filters to UniProt proteins automatically (databaseName == "UniProt").
      Small molecules are ChEBI-mapped (excluded).

PATHWAY SPECIFICITY CALIBRATION (measured 2026-07 from real Reactome API data):

  Known-tight (direct reaction partners):
    R-HSA-165181  "Inhibition of TSC complex formation by AKT (PKB)"
                  5 UniProt participants: AKT1, AKT2, AKT3, TSC1, TSC2
                  → "direct" tier (proteins are directly phosphorylated/complexed)

  Known-valid signaling module (accepted as specific, not flagged):
    R-HSA-380972  "Energy dependent regulation of mTOR by LKB1-AMPK"
                  29 UniProt participants: AMPK subunits, mTORC1 complex, etc.
                  → "moderate" tier (TSC1→MTOR connection is mechanistically real)

  Known-at-risk metabolic grouping (flagged "broad_metabolic"):
    R-HSA-70221   "Glycogen breakdown (glycogenolysis)"
                  15 UniProt participants: GAA, phosphorylases, phosphoglucomutase, etc.
                  → "broad_metabolic" tier (enzymes share substrate, not complex;
                    GAA operates in lysosomes, SLC37A4 in ER — unrelated mechanisms)

Key finding: participant count alone does NOT separate good from bad cases
(valid TSC1→MTOR has 29 participants; at-risk glycogenolysis has 15).
The reliable discriminator is whether the pathway name indicates a METABOLIC
PROCESS (enzymes grouped by shared substrate, different compartments) vs. a
SIGNALING or REGULATORY PATHWAY (proteins in the same complex or cascade).

Specificity tiers applied per neighbor:
  "direct"         — ≤ PATHWAY_TIER_DIRECT (5) participants in any shared
                     non-metabolic pathway.  Near-certain direct reaction partner.
  "broad_metabolic"— ALL shared examined pathways have metabolic-process keywords
                     in their display name.  Reviewer must verify compartment and
                     mechanism compatibility independently.
  "moderate"       — everything else (valid signaling module; human judgment applies).
"""

import time
from typing import Any

import requests

from cache.cache import get, set as cache_set, make_key

BASE_URL = "https://reactome.org/ContentService"
_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json"})

# Examine at most this many pathways per protein, choosing the tightest
# (lowest maxDepth) first. Prevents returning hundreds of neighbors from
# broad housekeeping pathways (translation, metabolism) that would obscure
# the direct-reaction-adjacency relationships we care about.
_TOP_PATHWAYS = 5

_REQUEST_TIMEOUT = 20

# --- Specificity calibration constants ----------------------------------------
# Calibrated 2026-07 against real Reactome participant counts (see module docstring).

PATHWAY_TIER_DIRECT = 5
# A shared pathway with ≤ this many UniProt participants AND no metabolic
# keyword in its name is classified "direct" (near-certain reaction partner).
# Calibrated against R-HSA-165181 (TSC1/AKT direct pathway, 5 participants).

_BROAD_PATHWAY_KEYWORDS: frozenset[str] = frozenset({
    # Metabolic process terms — pathways that group enzymes by shared substrate
    # rather than direct molecular interaction.  Calibrated against R-HSA-70221
    # "Glycogen breakdown (glycogenolysis)" to ensure it is flagged, while
    # R-HSA-380972 "Energy dependent regulation of mTOR by LKB1-AMPK" is NOT
    # (it contains no keyword and is a signaling/regulatory pathway).
    "metabolism", "catabolism", "anabolism", "biosynthesis", "degradation",
    "breakdown", "glycogenolysis", "glycolysis", "lipolysis", "gluconeogenesis",
    "lipogenesis", "proteolysis", "beta-oxidation", "fatty acid", "amino acid",
    "nucleotide", "citric acid", "glycogen", "glycan", "cholesterol", "steroid",
    "carbohydrate", "pentose", "pyruvate", "lipid", "phospholipid",
    "sphingolipid", "ceramide", "eicosanoid", "ketone",
})


def _is_broad_metabolic(pathway_name: str) -> bool:
    """Return True when a pathway name contains metabolic-process grouping keywords."""
    name_lower = pathway_name.lower()
    return any(kw in name_lower for kw in _BROAD_PATHWAY_KEYWORDS)


def _get(url: str) -> Any:
    """GET url, return parsed JSON or None on any error."""
    try:
        r = _SESSION.get(url, timeout=_REQUEST_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[reactome] WARNING: GET {url} failed: {e}")
        return None


def get_pathway_neighbors(
    uniprot_id: str,
    max_neighbors: int = 10,
) -> list[dict[str, Any]]:
    """
    Return up to max_neighbors UniProt proteins that co-participate in the
    same Reactome pathway(s) as the query protein.

    Only the _TOP_PATHWAYS tightest (lowest maxDepth) pathways are examined
    to prefer direct reaction-adjacency relationships over broad complex
    membership.

    Each returned neighbor includes specificity metadata calibrated against
    real Reactome participant counts (see module docstring):
      - specificity_tier: "direct" | "moderate" | "broad_metabolic"
      - shared_pathway_names: display names of pathways shared with query protein
      - min_shared_participants: smallest UniProt participant count among shared pathways
      - all_shared_pathways_metabolic: True if ALL shared pathways have metabolic keywords

    Returns:
        List of dicts: {uniprot_id, gene_name, pathway_count, specificity_tier,
                        shared_pathway_names, min_shared_participants,
                        all_shared_pathways_metabolic}
        Returns [] gracefully on any API failure (never crashes the pipeline).
    """
    cache_key = make_key("reactome_pathway_neighbors_v2", uniprot_id, max_neighbors)
    cached = get(cache_key)
    if cached is not None:
        return cached

    # Step 1: fetch all pathways the protein participates in.
    # _get returns None on failure — distinguish failure from a genuine empty
    # pathway list: only the genuine empty may be cached.
    pathways = _get(f"{BASE_URL}/data/mapping/UniProt/{uniprot_id}/pathways")
    if pathways is None:
        print(f"[reactome] pathway query FAILED for {uniprot_id} (not caching)")
        return []
    if not isinstance(pathways, list) or not pathways:
        print(f"[reactome] no pathways found for {uniprot_id}")
        cache_set(cache_key, [], ttl_days=30)
        return []

    # Step 2: sort by maxDepth ascending (tight pathways first) and cap.
    # In Reactome's API, maxDepth = the depth of the deepest child BELOW this
    # pathway (0 = leaf reaction, 1 = one sub-level, etc.).  Lower maxDepth →
    # the pathway is a leaf or near-leaf → more specific → preferred.
    pathways_sorted = sorted(
        pathways,
        key=lambda p: (p.get("maxDepth", 99), p.get("stId", "")),
    )
    selected = pathways_sorted[:_TOP_PATHWAYS]
    print(f"[reactome] {uniprot_id}: {len(pathways)} pathway(s) found; "
          f"examining {len(selected)} tightest (maxDepth range "
          f"{selected[0].get('maxDepth','?')}–{selected[-1].get('maxDepth','?')})")

    # Step 3: for each pathway, fetch reference entities AND count participants.
    # pathway_meta maps stId → {name, participant_count, is_metabolic}
    pathway_meta: dict[str, dict[str, Any]] = {}
    freq: dict[str, dict[str, Any]] = {}  # uid -> {gene_name, pathway_count, shared_stids}
    fetch_failed = False  # any participant fetch failed → aggregate is partial

    for pw in selected:
        st_id = pw.get("stId")
        pw_name = pw.get("displayName", "")
        if not st_id:
            continue

        is_metabolic = _is_broad_metabolic(pw_name)
        entities = _get(f"{BASE_URL}/data/participants/{st_id}/referenceEntities")
        if entities is None:
            fetch_failed = True
            continue
        if not isinstance(entities, list):
            continue
        time.sleep(0.05)  # gentle rate limiting

        uniprot_members = [
            e for e in entities
            if e.get("databaseName") == "UniProt"
            and e.get("identifier")
            and e.get("identifier") != uniprot_id
        ]
        pathway_meta[st_id] = {
            "name": pw_name,
            "participant_count": len(uniprot_members),
            "is_metabolic": is_metabolic,
        }

        for ent in uniprot_members:
            uid = ent.get("identifier", "")
            if not uid:
                continue
            if uid not in freq:
                genes = ent.get("geneName") or []
                gene = genes[0] if genes else uid
                freq[uid] = {
                    "uniprot_id": uid,
                    "gene_name": gene,
                    "pathway_count": 0,
                    "shared_stids": [],
                }
            freq[uid]["pathway_count"] += 1
            freq[uid]["shared_stids"].append(st_id)

    if not freq:
        print(f"[reactome] no UniProt neighbors found for {uniprot_id} "
              f"in the examined pathways")
        if not fetch_failed:
            cache_set(cache_key, [], ttl_days=30)
        return []

    # Step 4: rank by co-occurrence frequency, then alphabetically for determinism.
    ranked_uids = sorted(
        freq,
        key=lambda u: (-freq[u]["pathway_count"], freq[u]["gene_name"]),
    )[:max_neighbors]

    # Step 5: annotate each neighbor with its specificity tier.
    results: list[dict[str, Any]] = []
    for uid in ranked_uids:
        data = freq[uid]
        shared_pws = [pathway_meta[s] for s in data["shared_stids"] if s in pathway_meta]
        pw_names = [pw["name"] for pw in shared_pws]
        participant_counts = [pw["participant_count"] for pw in shared_pws]
        min_participants = min(participant_counts) if participant_counts else 999
        all_metabolic = all(pw["is_metabolic"] for pw in shared_pws) if shared_pws else False

        # Classify specificity tier using the calibrated rules (see module docstring):
        #   "direct"         → any shared non-metabolic pathway has ≤ PATHWAY_TIER_DIRECT participants
        #   "broad_metabolic"→ every shared pathway is flagged as metabolic-process grouping
        #   "moderate"       → default (valid signaling module; reviewer should still check)
        any_direct_hit = any(
            not pw["is_metabolic"] and pw["participant_count"] <= PATHWAY_TIER_DIRECT
            for pw in shared_pws
        )
        if any_direct_hit:
            tier = "direct"
        elif all_metabolic:
            tier = "broad_metabolic"
        else:
            tier = "moderate"

        results.append({
            "uniprot_id": uid,
            "gene_name": data["gene_name"],
            "pathway_count": data["pathway_count"],
            "specificity_tier": tier,
            "shared_pathway_names": pw_names,
            "min_shared_participants": min_participants,
            "all_shared_pathways_metabolic": all_metabolic,
        })

    top = results[0]
    print(
        f"[reactome] {uniprot_id}: {len(results)} pathway neighbor(s) "
        f"(top: {top['gene_name']} count={top['pathway_count']} "
        f"tier={top['specificity_tier']})"
    )

    broad_count = sum(1 for r in results if r["specificity_tier"] == "broad_metabolic")
    if broad_count:
        broad_names = [
            r["gene_name"] for r in results if r["specificity_tier"] == "broad_metabolic"
        ]
        print(
            f"[reactome] WARNING: {broad_count} neighbor(s) connected ONLY via "
            f"broad metabolic pathway(s) → tier=broad_metabolic: {broad_names}. "
            f"Reviewer must verify cellular compartment and mechanism compatibility."
        )

    # A partial aggregate (some participant fetches failed) must not be
    # cached: a cached partial neighbor list poisons expansion for 30 days.
    if not fetch_failed:
        cache_set(cache_key, results, ttl_days=30)
    return results
