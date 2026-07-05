"""
Biologist Agent (Stage 2).

Takes the top target chosen in Stage 1 and gathers biological context:
  - BioGRID physical/genetic interactors (labelled as NOT a mechanism)
  - PubMed literature whose target<->disease relationship is LLM-confirmed
  - Druggability context: ChEMBL approved-drug count for this target +
    optional literature-grounded difficulty summary (informational only;
    does NOT affect any score)

Every BioGRID interaction id and PMID used is recorded in the shared
provenance_log.json (output/) for the Chemist and Reviewer to build on.

Run:  python -m agents.biologist
Input:  output/top_candidates.json  (Stage 1 output)
Output: output/biologist_output.json
"""

import json
import os
import sys
import time
from typing import Any, Optional

import anthropic

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from data_sources.biogrid import get_interactions
from data_sources.pubmed import search_literature, fetch_raw_abstracts
from data_sources.chembl import get_approved_drugs_for_target
from data_sources.reactome import get_pathway_neighbors
from cache.cache import get, set as cache_set, make_key

SCREENING_MODEL = "claude-sonnet-4-6"
HAIKU_MODEL = "claude-haiku-4-5-20251001"


def _anthropic_client() -> Optional[anthropic.Anthropic]:
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        return None
    return anthropic.Anthropic(base_url=base_url, api_key=api_key)


def get_druggability_literature(
    target_symbol: str,
    uniprot_id: Optional[str],
    client: Optional[anthropic.Anthropic],
) -> dict[str, Any]:
    """
    Produces an informational druggability context block for the biologist output.
    NEVER affects tractability_score, unmet_need_score, composite_score, or
    STRONG_MATCH — it is read-only context for human reviewers.

    Steps:
      1. ChEMBL mechanism lookup (no LLM) → approved_drug_count.
      2. 3 targeted PubMed queries for difficulty/undruggability history.
      3. YES/NO LLM screening of each abstract (sonnet, reusing the existing
         relevance-gate pattern from pubmed.py).
      4. If 2+ abstracts pass: ONE haiku call writes a 2-3 sentence summary
         citing the specific PMIDs. Otherwise: 'insufficient literature signal'.

    Returns:
      {
        has_approved_drug_for_target: bool,
        approved_drug_count: int,
        approved_drugs: [{molecule_chembl_id, name, max_phase}],
        difficulty_summary: str | None,
        supporting_pmids: [str],
        druggability_flag: str,   # 'literature signal found' | 'insufficient literature signal'
      }
    """
    cache_key = make_key("get_druggability_literature", target_symbol, uniprot_id)
    cached = get(cache_key)
    if cached is not None:
        return cached

    # Part A — ChEMBL approved drugs with known mechanism (no LLM).
    approved_info: dict[str, Any] = {
        "approved_drugs": [],
        "approved_drug_count": 0,
    }
    if uniprot_id:
        try:
            approved_info = get_approved_drugs_for_target(uniprot_id)
        except Exception as e:
            print(f"[biologist] WARNING: approved-drug lookup failed: {e}")

    result: dict[str, Any] = {
        "has_approved_drug_for_target": approved_info["approved_drug_count"] > 0,
        "approved_drug_count": approved_info["approved_drug_count"],
        "approved_drugs": approved_info.get("approved_drugs", []),
        "difficulty_summary": None,
        "supporting_pmids": [],
        "druggability_flag": "insufficient literature signal",
    }

    if client is None:
        cache_set(cache_key, result, ttl_days=1)
        return result

    # Part B — PubMed searches for historical difficulty / undruggability.
    queries = [
        f'"{target_symbol}" undruggable',
        f'"{target_symbol}" drug resistance mechanism',
        f'"{target_symbol}" historically difficult target',
    ]
    all_abstracts: dict[str, str] = {}
    for q in queries:
        try:
            batch = fetch_raw_abstracts(q, retmax=5)
            all_abstracts.update(batch)  # PMID de-duplication via dict keys
        except Exception as e:
            print(f"[biologist] WARNING: PubMed fetch failed for '{q}': {e}")

    if not all_abstracts:
        cache_set(cache_key, result, ttl_days=7)
        return result

    # YES/NO LLM screening — same constrained pattern as pubmed._llm_relationship.
    qualifying: list[tuple[str, str]] = []  # (pmid, abstract)
    for pmid, abstract in all_abstracts.items():
        prompt = (
            f"Does this abstract specifically discuss historical difficulty, repeated "
            f"failure, or 'undruggable' characterization in targeting the protein "
            f"{target_symbol} with small-molecule or biologic drugs? "
            f"Answer only YES or NO, then one sentence of reason.\n\n"
            f"Abstract:\n{abstract[:4000]}"
        )
        try:
            msg = client.messages.create(
                model=SCREENING_MODEL,
                max_tokens=120,
                # temperature=0: YES/NO classifier that gates which abstracts enter
                # supporting_pmids. Pinned for reproducibility; non-zero temperature
                # could produce different outcomes on borderline abstracts across runs.
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            block = msg.content[0]
            text = (block.text if block.type == "text" else str(block)).strip()
            if text.upper().lstrip().startswith("YES"):
                qualifying.append((pmid, abstract))
        except Exception as e:
            print(f"[biologist] WARNING: druggability screen failed for PMID {pmid}: {e}")
        time.sleep(0.12)  # stay under E-utilities / LLM rate limits

    result["supporting_pmids"] = [pmid for pmid, _ in qualifying]

    if len(qualifying) < 2:
        # Fewer than 2 qualifying abstracts — record what we have, no LLM summary.
        cache_set(cache_key, result, ttl_days=7)
        return result

    # Part B continued — ONE haiku summarisation call.
    count = approved_info["approved_drug_count"]
    names = [d["name"] for d in approved_info.get("approved_drugs", [])][:5]
    approved_fact = (
        f"ChEMBL records {count} approved drug(s) with a known mechanism against "
        f"this target: {', '.join(names)}."
        if count > 0
        else "ChEMBL records no approved drugs with a known mechanism against this target."
    )

    abstracts_block = "\n\n".join(
        f"PMID {pmid}:\n{abstract[:800]}"
        for pmid, abstract in qualifying[:5]
    )

    summary_prompt = (
        f"You are a neutral scientific summariser. Using ONLY the abstracts provided "
        f"below and the single ChEMBL fact, write 2-3 sentences summarising the "
        f"historical difficulty in drugging the target {target_symbol}. Cite relevant "
        f"PMIDs inline in parentheses (e.g. PMID 12345678). Do NOT introduce any "
        f"claim, mechanism, drug name, number, or example that is not present in the "
        f"provided text.\n\n"
        f"ChEMBL fact: {approved_fact}\n\n"
        f"Abstracts:\n{abstracts_block}"
    )

    try:
        msg = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": summary_prompt}],
        )
        block = msg.content[0]
        result["difficulty_summary"] = (
            block.text if block.type == "text" else str(block)
        ).strip()
        result["druggability_flag"] = "literature signal found"
    except Exception as e:
        print(f"[biologist] WARNING: druggability summarisation failed: {e}")

    cache_set(cache_key, result, ttl_days=7)
    return result


def get_pathway_neighbor_targets(
    uniprot_id: str,
    disease_name: str,
) -> list[dict[str, Any]]:
    """
    Return all Reactome pathway-neighbor proteins for the Chemist to query.

    The former approved-drug pre-filter (get_approved_drugs_for_target via the
    sparse ChEMBL mechanism table) has been intentionally removed.  The
    Chemist's bioactivity-based pool (IC50/Ki + max_phase from ChEMBL activity
    records) is the correct and more complete signal.  All neighbors from
    get_pathway_neighbors are forwarded unconditionally; if a neighbor has no
    qualifying compounds the Chemist simply adds 0 candidates from it.

    Tagged with target_discovery_method = "pathway_neighbor" so every compound
    that originates from a neighbor is auditable in the report.

    Returns:
        List of {target_symbol, uniprot_id, target_discovery_method,
                 pathway_count, disease_name}
        Returns [] gracefully on any API failure.
    """
    if not uniprot_id:
        return []
    cache_key = make_key("biologist_pathway_neighbor_targets_v2",
                         uniprot_id, disease_name)
    cached = get(cache_key)
    if cached is not None:
        return cached

    neighbors = get_pathway_neighbors(uniprot_id)
    results = [
        {
            "target_symbol": nbr.get("gene_name", nbr.get("uniprot_id", "")),
            "uniprot_id": nbr.get("uniprot_id", ""),
            "target_discovery_method": "pathway_neighbor",
            "pathway_count": nbr.get("pathway_count", 1),
            "disease_name": disease_name,
        }
        for nbr in neighbors
        if nbr.get("uniprot_id")
    ]

    print(f"[biologist] pathway_neighbors: {len(results)} neighbor(s) "
          f"forwarded to Chemist (no approved-drug pre-filter)")
    cache_set(cache_key, results, ttl_days=7)
    return results


def run_biologist(target: dict[str, Any]) -> dict[str, Any]:
    symbol = target["target_symbol"]
    disease = target.get("disease_name", "")
    uniprot_id = target.get("uniprot_id")

    interactions = get_interactions(symbol)
    literature = search_literature(None, symbol, disease)

    # Unique interactor genes, preserving order.
    interacting_genes: list[str] = []
    seen: set[str] = set()
    prov_entries: list[dict[str, Any]] = []
    for it in interactions:
        g = it.get("interactor_symbol")
        if g and g not in seen:
            seen.add(g)
            interacting_genes.append(g)
        prov_entries.append({
            "source_type": "biogrid_interaction",
            "source_id": it.get("biogrid_interaction_id"),
            "used_by": "biologist",
            "context": f"{symbol} interactor {it.get('interactor_symbol')}",
        })

    literature_hits = []
    for h in literature.get("literature_hits", []):
        literature_hits.append({
            "pmid": h["pmid"],
            "summary": h["summary"],
            "relationship_asserted": True,
        })
        prov_entries.append({
            "source_type": "pmid",
            "source_id": h["pmid"],
            "used_by": "biologist",
            "context": f"{symbol}/{disease} literature",
        })

    provenance.log_many(prov_entries)

    # Druggability context — informational only; never touches any score.
    client = _anthropic_client()
    druggability_context = get_druggability_literature(symbol, uniprot_id, client)

    # Pathway-neighbor expansion: find proteins co-participating in the same
    # Reactome pathway(s) as the primary causal gene, then check whether any
    # of them have approved drugs — those become additional candidate targets
    # tagged target_discovery_method="pathway_neighbor".
    pathway_neighbor_targets = get_pathway_neighbor_targets(uniprot_id, disease)

    return {
        "target": {
            "target_symbol": symbol,
            "uniprot_id": uniprot_id,
            "ensembl_id": target.get("ensembl_id"),
            "disease_name": disease,
            "orpha_code": target.get("orpha_code"),
            "ot_association_score": target.get("ot_association_score", 0.0),
        },
        "interacting_genes": interacting_genes,
        "interaction_records": interactions,
        "literature_hits": literature_hits,
        "interaction_note": (
            "BioGRID edges are physical/genetic interactions, NOT activating/"
            "inhibiting mechanisms."
        ),
        "druggability_context": druggability_context,
        "druggability_note": (
            "druggability_context is informational reader context only. It does not "
            "affect tractability_score, unmet_need_score, composite_score, "
            "STRONG_MATCH, or any pipeline filter."
        ),
        "pathway_neighbor_targets": pathway_neighbor_targets,
        "pathway_neighbor_note": (
            "Targets tagged target_discovery_method='pathway_neighbor' were "
            "discovered via Reactome pathway co-participation of the primary "
            "causal gene. Their compounds are pooled into the Chemist's "
            "candidate set with ot_association_score=0 (no direct Open Targets "
            "link to the disease) and are auditable in the report."
        ),
    }


def _load_top_target() -> dict[str, Any]:
    path = os.path.join(OUTPUT_DIR, "top_candidates.json")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found — run Stage 1 (python -m agents.target_selection) first.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not rows:
        print("ERROR: top_candidates.json is empty.")
        sys.exit(1)
    r = rows[0]
    return {
        "target_symbol": r["target_symbol"],
        "uniprot_id": r.get("uniprot_id"),
        "ensembl_id": r.get("ensembl_id"),
        "disease_name": r["disease_name"],
        "orpha_code": r.get("orpha_code"),
        "ot_association_score": r.get("ot_association_score", 0.0),
    }


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Fresh provenance log for a new Stage 2 run starts at the biologist.
    provenance.reset()

    target = _load_top_target()
    print(f"[biologist] target: {target['target_symbol']} ({target.get('uniprot_id')}) "
          f"for {target['disease_name']}")

    out = run_biologist(target)
    print(f"[biologist] {len(out['interacting_genes'])} unique interactors, "
          f"{len(out['literature_hits'])} confirmed literature hits")

    path = os.path.join(OUTPUT_DIR, "biologist_output.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[biologist] wrote {path}")


if __name__ == "__main__":
    main()
