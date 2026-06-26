"""
Chemist Agent (Stage 2).

Takes the Biologist's output and builds a ranked list of candidate compounds:
  - ChEMBL candidate compounds for the target (confidence >= 8, species-matched)
  - PubChem InChIKey cross-reference to confirm approved/known-drug status
  - RDKit Tanimoto similarity (Morgan fingerprints, radius 2) of each candidate to
    every OTHER approved drug in the working set — this is the bisociation step and
    is a real computed number, not an LLM guess
  - ONE constrained LLM call per candidate that is GIVEN the affinity, the Tanimoto
    score + nearest drug, and the BioGRID network context, and asked only to write
    a 2-sentence rationale referencing those numbers (no new factual claims)

NOTE on the approved-drug reference set: a complete download of PubChem's entire
approved-drug subset is infeasible here, so the Tanimoto reference set is the
approved/known drugs found among this target's own candidate pool. The similarity
numbers themselves are fully computed by RDKit; only the comparison scope is bounded.

Run:  python -m agents.chemist
Input:  output/biologist_output.json
Output: output/chemist_output.json
"""

import json
import os
import sys
from typing import Any, Optional

from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
import anthropic

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from data_sources.chembl import get_target_candidate_compounds
from data_sources.pubchem import get_compound_data, get_drug_classification

MODEL = "claude-sonnet-4-6"
FP_RADIUS = 2
FP_BITS = 2048


def _anthropic_client() -> Optional[anthropic.Anthropic]:
    base_url = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_BASE_URL")
    api_key = os.environ.get("AI_INTEGRATIONS_ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        return None
    return anthropic.Anthropic(base_url=base_url, api_key=api_key)


def _fingerprint(smiles: Optional[str]):
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return AllChem.GetMorganFingerprintAsBitVect(mol, FP_RADIUS, nBits=FP_BITS)


def _is_approved(compound: dict[str, Any]) -> bool:
    if compound.get("pubchem_known_drug"):
        return True
    mp = compound.get("max_phase")
    try:
        return mp is not None and float(mp) >= 4
    except (TypeError, ValueError):
        return False


def _fallback_rationale(c: dict[str, Any], sim_drug: Optional[str], tanimoto: float,
                        network: list[str]) -> str:
    net = ", ".join(network[:6]) if network else "no mapped interactors"
    sim = (f"It is most structurally similar to the approved drug {sim_drug} "
           f"(Tanimoto {tanimoto:.2f})." if sim_drug else
           "No approved structural analog was found in the candidate set.")
    return (
        f"{c['drug_name']} shows a median pChEMBL affinity of "
        f"{c.get('pchembl_value')} against {c.get('target_symbol')} at assay "
        f"confidence {c.get('confidence_score')}. {sim} Target network context: {net}."
    )


def _llm_rationale(client: Optional[anthropic.Anthropic], c: dict[str, Any],
                   sim_drug: Optional[str], tanimoto: float,
                   network: list[str]) -> str:
    if client is None:
        return _fallback_rationale(c, sim_drug, tanimoto, network)

    facts = [
        f"Compound: {c['drug_name']}",
        f"ChEMBL median pChEMBL affinity vs {c.get('target_symbol')}: {c.get('pchembl_value')}",
        f"Assay confidence score (0-9): {c.get('confidence_score')}",
        f"Approved/known drug: {c.get('is_approved_drug')}",
        (f"Tanimoto similarity (Morgan r2) to nearest approved drug "
         f"{sim_drug}: {tanimoto:.3f}" if sim_drug
         else "Nearest approved structural analog: none in candidate set"),
        f"Target BioGRID interactors (physical/genetic, not mechanism): "
        f"{', '.join(network[:8]) if network else 'none'}",
    ]
    prompt = (
        "Write EXACTLY two plain sentences that restate the facts listed below for "
        "a drug-repurposing candidate. You are a neutral data summariser, not a "
        "salesperson.\n\n"
        "STRICT RULES:\n"
        "- Use ONLY the facts below. Do not add any number, mechanism, target, or "
        "drug not listed.\n"
        "- Do NOT characterise the target, the compound, the disease, its biology, "
        "its market, its intellectual property, its novelty, or its development "
        "potential. Specifically, do NOT say things like 'well-validated', "
        "'promising', 'novel IP', 'differentiated', 'compelling', or 'established'.\n"
        "- Do NOT speculate about efficacy, safety, or clinical outcome.\n"
        "- Just state what the numbers are and what they measure.\n\n"
        + "\n".join(f"- {f}" for f in facts)
    )
    try:
        msg = client.messages.create(
            model=MODEL,
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        block = msg.content[0]
        return (block.text if block.type == "text" else str(block)).strip()
    except Exception as e:
        print(f"[chemist] WARNING: LLM rationale failed ({e}); using templated rationale")
        return _fallback_rationale(c, sim_drug, tanimoto, network)


def run_chemist(biologist_output: dict[str, Any]) -> dict[str, Any]:
    target = biologist_output["target"]
    uniprot = target.get("uniprot_id")
    symbol = target.get("target_symbol")
    network = biologist_output.get("interacting_genes", [])

    if not uniprot:
        print("[chemist] WARNING: target has no UniProt id; cannot query ChEMBL")
        return {"target": target, "candidates": [], "pooled_across_multiple_targets": False}

    cc = get_target_candidate_compounds(uniprot)
    compounds = cc["compounds"]

    # Enrich each compound: resolve InChIKey + SMILES + approved-drug status via PubChem.
    enriched: list[dict[str, Any]] = []
    for c in compounds:
        smiles = c.get("canonical_smiles")
        name = c.get("pref_name")
        inchikey = None
        pubchem_known = False
        atc: list[str] = []
        if name:
            pc = get_compound_data(name)
            inchikey = pc.get("inchikey")
            if not smiles:
                smiles = pc.get("canonical_smiles")
            if inchikey:
                cls = get_drug_classification(inchikey)
                pubchem_known = cls.get("is_known_drug", False)
                atc = cls.get("atc_codes", [])
        e = {**c, "smiles": smiles, "inchikey": inchikey,
             "pubchem_known_drug": pubchem_known, "atc_codes": atc,
             "target_symbol": symbol}
        e["is_approved_drug"] = _is_approved(e)
        e["drug_name"] = name or c["molecule_chembl_id"]
        enriched.append(e)

    # Build approved-drug reference fingerprints (the bisociation reference set).
    approved_fps: dict[str, tuple[dict[str, Any], Any]] = {}
    for e in enriched:
        if e["is_approved_drug"]:
            fp = _fingerprint(e["smiles"])
            if fp is not None:
                approved_fps[e["molecule_chembl_id"]] = (e, fp)

    client = _anthropic_client()
    prov_entries: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for e in enriched:
        cand_fp = _fingerprint(e["smiles"])
        best_drug = None
        best_score = 0.0
        if cand_fp is not None:
            for mid, (ref, ref_fp) in approved_fps.items():
                if mid == e["molecule_chembl_id"]:
                    continue
                s = DataStructs.TanimotoSimilarity(cand_fp, ref_fp)
                if s > best_score:
                    best_score = s
                    best_drug = ref.get("drug_name")

        rationale = _llm_rationale(client, e, best_drug, best_score, network)

        results.append({
            "drug_name": e["drug_name"],
            "molecule_chembl_id": e["molecule_chembl_id"],
            "smiles": e["smiles"],
            "inchikey": e["inchikey"],
            "pchembl_value": e.get("pchembl_value"),
            "confidence_score": e.get("confidence_score"),
            "max_phase": e.get("max_phase"),
            "is_approved_drug": e["is_approved_drug"],
            "atc_codes": e["atc_codes"],
            "most_similar_approved_drug": best_drug,
            "tanimoto_score": round(best_score, 4),
            "rationale": rationale,
            "source_chembl_ids": e.get("source_chembl_ids", []),
            "source_activity_ids": e.get("source_activity_ids", []),
            "target_symbol": symbol,
            "uniprot_id": uniprot,
            "disease_name": target.get("disease_name"),
            "ot_association_score": target.get("ot_association_score", 0.0),
        })

        for aid in e.get("source_activity_ids", []):
            prov_entries.append({
                "source_type": "chembl_activity",
                "source_id": aid,
                "used_by": "chemist",
                "context": f"{e['drug_name']} affinity vs {symbol}",
            })

    provenance.log_many(prov_entries)

    # Rank by affinity, then structural novelty signal.
    results.sort(key=lambda r: ((r["pchembl_value"] or 0.0), r["tanimoto_score"]), reverse=True)

    return {
        "target": target,
        "candidates": results,
        "pooled_across_multiple_targets": cc["pooled_across_multiple_targets"],
        "approved_reference_set_size": len(approved_fps),
        "reference_set_note": (
            "Tanimoto computed against approved drugs found in this target's "
            "candidate pool (bounded scope)."
        ),
    }


def main() -> None:
    path_in = os.path.join(OUTPUT_DIR, "biologist_output.json")
    if not os.path.exists(path_in):
        print(f"ERROR: {path_in} not found — run python -m agents.biologist first.")
        sys.exit(1)
    with open(path_in, "r", encoding="utf-8") as f:
        biologist_output = json.load(f)

    out = run_chemist(biologist_output)
    print(f"[chemist] {len(out['candidates'])} candidates ranked "
          f"({out['approved_reference_set_size']} approved in reference set)")

    path_out = os.path.join(OUTPUT_DIR, "chemist_output.json")
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[chemist] wrote {path_out}")


if __name__ == "__main__":
    main()
