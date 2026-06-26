"""
Biologist Agent (Stage 2).

Takes the top target chosen in Stage 1 and gathers biological context:
  - BioGRID physical/genetic interactors (labelled as NOT a mechanism)
  - PubMed literature whose target<->disease relationship is LLM-confirmed

Every BioGRID interaction id and PMID used is recorded in the shared
provenance_log.json (output/) for the Chemist and Reviewer to build on.

Run:  python -m agents.biologist
Input:  output/top_candidates.json  (Stage 1 output)
Output: output/biologist_output.json
"""

import json
import os
import sys
from typing import Any

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from data_sources.biogrid import get_interactions
from data_sources.pubmed import search_literature


def run_biologist(target: dict[str, Any]) -> dict[str, Any]:
    symbol = target["target_symbol"]
    disease = target.get("disease_name", "")

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

    return {
        "target": {
            "target_symbol": symbol,
            "uniprot_id": target.get("uniprot_id"),
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
