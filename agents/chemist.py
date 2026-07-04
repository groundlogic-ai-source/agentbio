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
from agents.mutation_disclosure import detect_mutation_specificity
from data_sources.chembl import get_target_candidate_compounds, get_drug_indications
from data_sources.pubchem import get_compound_data, get_drug_classification
from data_sources.openfda import get_label_indications

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


def _mutation_disclosure_for(drug_name: str, molecule_chembl_id: str) -> dict[str, Any]:
    """
    Build the mutation-specificity DISCLOSURE record for one drug by scanning its
    FDA-label indications (primary) plus ChEMBL indication terms (secondary).
    Disclosure only — never affects scoring; see agents/mutation_disclosure.py.
    """
    label = get_label_indications(drug_name) if drug_name else {}
    chembl_terms = get_drug_indications(molecule_chembl_id) if molecule_chembl_id else []
    disc = detect_mutation_specificity(label.get("indications_text", ""), chembl_terms)
    disc["indication_source"] = label.get("source")
    return disc


def _enrich_compounds(
    compounds: list[dict[str, Any]],
    symbol: str,
    uniprot: str,
    disease_name: str,
    ot_association_score: float,
    target_discovery_method: str,
) -> list[dict[str, Any]]:
    """
    PubChem-enrich a list of raw ChEMBL compound dicts and attach target
    metadata fields consumed by the Reviewer and Writer.

    Sets target_discovery_method on every compound so the report makes the
    discovery path auditable ("genetic_association", "pharmacological_precedent",
    or "pathway_neighbor").
    """
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
             "target_symbol": symbol,
             "target_discovery_method": target_discovery_method}
        e["is_approved_drug"] = _is_approved(e)
        e["drug_name"] = name or c["molecule_chembl_id"]
        e["ot_association_score"] = ot_association_score
        e["disease_name"] = disease_name
        if e["is_approved_drug"] and name:
            e["mutation_specificity"] = _mutation_disclosure_for(
                name, c.get("molecule_chembl_id", ""))
        else:
            e["mutation_specificity"] = detect_mutation_specificity("")
        enriched.append(e)
    return enriched


def run_chemist(biologist_output: dict[str, Any],
                repurposing_only: bool = False) -> dict[str, Any]:
    target = biologist_output["target"]
    uniprot = target.get("uniprot_id")
    symbol = target.get("target_symbol")
    disease_name = target.get("disease_name", "")
    ot_score = target.get("ot_association_score", 0.0)
    network = biologist_output.get("interacting_genes", [])

    if not uniprot:
        print("[chemist] WARNING: target has no UniProt id; cannot query ChEMBL")
        return {"target": target, "candidates": [],
                "pooled_across_multiple_targets": False,
                "repurposing_only": repurposing_only}

    cc = get_target_candidate_compounds(uniprot, repurposing_only=repurposing_only)
    compounds = cc["compounds"]
    if repurposing_only:
        print(f"[chemist] repurposing_only mode: pool restricted to "
              f"{len(compounds)} approved compound(s) (unapproved tool "
              f"compounds dropped at collection time)")

    # Enrich primary target's compounds via the shared helper.
    primary_disc_method = target.get("target_discovery_method", "genetic_association")
    enriched = _enrich_compounds(
        compounds, symbol, uniprot, disease_name, ot_score, primary_disc_method)

    # Pathway-neighbor expansion: also query compounds for any pathway-neighbor
    # targets discovered by the Biologist (those with approved drugs).
    # Each neighbor's candidates are enriched and pooled with the primary set.
    neighbor_enriched: list[dict[str, Any]] = []
    for nbr in biologist_output.get("pathway_neighbor_targets") or []:
        nbr_uid = nbr.get("uniprot_id", "")
        nbr_sym = nbr.get("target_symbol", nbr_uid)
        if not nbr_uid:
            continue
        try:
            nbr_cc = get_target_candidate_compounds(
                nbr_uid, repurposing_only=repurposing_only)
            nbr_compounds = nbr_cc.get("compounds", [])
            if nbr_compounds:
                nbr_enriched = _enrich_compounds(
                    nbr_compounds, nbr_sym, nbr_uid, disease_name,
                    0.0, "pathway_neighbor")  # ot_score=0: no direct OT link
                neighbor_enriched.extend(nbr_enriched)
                print(f"[chemist] pathway_neighbor {nbr_sym} ({nbr_uid}): "
                      f"{len(nbr_compounds)} compound(s) added to pool")
        except Exception as e:
            print(f"[chemist] WARNING: compound lookup failed for pathway "
                  f"neighbor {nbr_sym} ({nbr_uid}): {e}")

    all_enriched = enriched + neighbor_enriched
    print(f"[chemist] pooled {len(enriched)} primary + "
          f"{len(neighbor_enriched)} pathway-neighbor compounds "
          f"= {len(all_enriched)} total")

    # Build approved-drug reference fingerprints from the FULL pooled set —
    # so Tanimoto similarity is computed against the combined approved reference.
    approved_fps: dict[str, tuple[dict[str, Any], Any]] = {}
    for e in all_enriched:
        if e["is_approved_drug"]:
            fp = _fingerprint(e["smiles"])
            if fp is not None:
                approved_fps[e["molecule_chembl_id"]] = (e, fp)

    client = _anthropic_client()
    prov_entries: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for e in all_enriched:
        e_sym = e.get("target_symbol", symbol)
        e_uid = e.get("uniprot_id", uniprot)
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
            "mutation_specificity": e.get("mutation_specificity")
            or detect_mutation_specificity(""),
            "rationale": rationale,
            "source_chembl_ids": e.get("source_chembl_ids", []),
            "source_activity_ids": e.get("source_activity_ids", []),
            "target_symbol": e_sym,
            "target_discovery_method": e.get("target_discovery_method",
                                             primary_disc_method),
            "uniprot_id": e_uid,
            "disease_name": disease_name,
            "ot_association_score": e.get("ot_association_score",
                                          ot_score),
        })

        for aid in e.get("source_activity_ids", []):
            prov_entries.append({
                "source_type": "chembl_activity",
                "source_id": aid,
                "used_by": "chemist",
                "context": f"{e['drug_name']} affinity vs {e_sym}",
            })

    provenance.log_many(prov_entries)

    # Rank: approved drugs always before unapproved (repurposing requires a prior
    # human safety profile), then by affinity, then structural novelty signal.
    results.sort(key=lambda r: (
        1 if r["is_approved_drug"] else 0,
        r["pchembl_value"] or 0.0,
        r["tanimoto_score"],
    ), reverse=True)

    n_mut = sum(1 for r in results
                if (r.get("mutation_specificity") or {}).get("is_mutation_specific"))
    if n_mut:
        print(f"[chemist] mutation-specificity disclosure: {n_mut} candidate(s) "
              f"have an approved indication that names a specific mutation")

    return {
        "target": target,
        "candidates": results,
        "pooled_across_multiple_targets": cc["pooled_across_multiple_targets"],
        "repurposing_only": repurposing_only,
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

    # CLI default is the mixed pool (repurposing_only=False); pass
    # --repurposing-only to restrict to approved drugs, matching live API jobs.
    repurposing_only = "--repurposing-only" in sys.argv
    out = run_chemist(biologist_output, repurposing_only=repurposing_only)
    print(f"[chemist] {len(out['candidates'])} candidates ranked "
          f"({out['approved_reference_set_size']} approved in reference set); "
          f"repurposing_only={out['repurposing_only']}")

    path_out = os.path.join(OUTPUT_DIR, "chemist_output.json")
    with open(path_out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"[chemist] wrote {path_out}")


if __name__ == "__main__":
    main()
