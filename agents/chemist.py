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
from typing import Any, Iterable, Optional

from rdkit import Chem
from rdkit.Chem import AllChem, DataStructs
from rdkit.Chem.SaltRemover import SaltRemover
import anthropic

from agents.target_selection import OUTPUT_DIR
from agents import provenance
from agents.mutation_disclosure import detect_mutation_specificity
from agents.biologist import get_pathway_neighbor_targets
from data_sources.chembl import get_target_candidate_compounds, get_drug_indications
from data_sources.pubchem import get_compound_data, get_drug_classification
from data_sources.openfda import get_label_indications, get_label_mechanism
from data_sources.multisource_candidates import collect_target_candidates
from data_sources import holdout as _holdout
from data_sources.evidence_ledger import (
    EvidenceRecord, EvidenceRole, QualificationStatus, SourceType,
)

MODEL = "claude-sonnet-4-6"
FP_RADIUS = 2
FP_BITS = 2048

# Singleton salt remover — strips counterions/solvents so two salt forms of the
# same active moiety produce identical desalted fingerprints.
_SALT_REMOVER = SaltRemover()

# Lazy pathway-neighbor expansion threshold.
# Pathway-neighbor expansion is only triggered when the primary target's own
# approved-drug pool (max_phase >= 4) is SMALLER than this number.  A healthy
# pool (>= threshold) means the repurposing landscape is already well-covered
# by approved compounds and the ~45 extra ChEMBL + Reactome API calls would
# not materially change the candidate set.  Set to 0 to disable expansion
# entirely; set to a large number to always expand.
PATHWAY_NEIGHBOR_MIN_APPROVED = int(
    os.environ.get("PATHWAY_NEIGHBOR_MIN_APPROVED", "3")
)


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


def _desalted_fingerprint(smiles: Optional[str]):
    """
    Morgan fingerprint of the desalted (stripped) form of a molecule.
    Used to detect salt/hydrate variants of the same active moiety:
    if two compounds have desalted Tanimoto >= 0.99, they are the same
    drug in different salt/hydrate forms and one should be excluded as a
    Tanimoto reference for the other (to avoid trivial 0.98+ self-similarity).
    """
    if not smiles:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        stripped = _SALT_REMOVER.StripMol(mol, dontRemoveEverything=True)
        if stripped is None or stripped.GetNumAtoms() == 0:
            stripped = mol  # nothing to strip; use original
    except Exception:
        stripped = mol
    return AllChem.GetMorganFingerprintAsBitVect(stripped, FP_RADIUS, nBits=FP_BITS)


def _is_approved(compound: dict[str, Any]) -> bool:
    if compound.get("pubchem_known_drug"):
        return True
    mp = compound.get("max_phase")
    try:
        return mp is not None and float(mp) >= 4
    except (TypeError, ValueError):
        return False


def _is_max_phase_approved(max_phase: Any) -> bool:
    """Return True when a raw max_phase value from ChEMBL represents an
    approved drug (max_phase >= 4).  Tolerates numeric strings."""
    try:
        return float(max_phase) >= 4
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


def _label_mechanism_record(candidate: dict[str, Any]) -> Optional[EvidenceRecord]:
    """Create one qualified, quoted FDA-label mechanism record for a candidate.

    Drug-name label queries are intentionally disabled while a benchmark holdout
    is active: target-first candidate discovery remains valid under holdout, but
    fetching the held-out drug's own label would inject direct identity evidence.
    """
    drug_name = str(candidate.get("drug_name") or "").strip()
    if not drug_name or _holdout.is_active() or not candidate.get("is_approved_drug"):
        return None
    label = get_label_mechanism(drug_name)
    text = str(label.get("mechanism_text") or "").strip()
    if not text or label.get("error"):
        return None
    return EvidenceRecord(
        provider="openfda",
        source_type=SourceType.DRUG_LABEL,
        evidence_role=EvidenceRole.EFFICACY,
        source_id=f"openfda-label-mechanism:{label.get('label_id') or drug_name}",
        label_id=str(label.get("label_id") or ""),
        molecule_id=str(candidate.get("molecule_chembl_id") or ""),
        molecule_name=drug_name,
        inchikey=str(candidate.get("inchikey") or ""),
        smiles=str(candidate.get("smiles") or ""),
        target_symbol=str(candidate.get("target_symbol") or ""),
        target_accession=str(candidate.get("uniprot_id") or ""),
        target_species="Homo sapiens",
        action="label_mechanism",
        measurement_type="label_mechanism_class",
        phenotype=str(candidate.get("mechanism_class") or "label_declared_mechanism"),
        context=text,
        qualification_status=QualificationStatus.QUALIFIED,
    )


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
             "target_discovery_method": target_discovery_method,
             # Stamp the correct per-compound UniProt so structure_validation_node
             # folds the right protein.  For primary-target compounds this is the
             # primary target's UniProt; for pathway_neighbor compounds this is the
             # NEIGHBOR's UniProt (nbr_uid), not the primary target's.
             # Without this stamp, line 345 below falls back to the outer scope's
             # `uniprot` (the primary target) for EVERY compound — causing Boltz
             # to fold the wrong protein for all pathway_neighbor candidates.
             "uniprot_id": uniprot}
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
                repurposing_only: bool = False,
                enabled_sources: Optional[Iterable[str]] = None,
                ) -> dict[str, Any]:
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

    # Lazy pathway-neighbor expansion.
    # Count approved drugs (max_phase >= 4) in the PRIMARY target's pool.
    # Expansion is skipped when the primary pool is already healthy — i.e.,
    # it already has PATHWAY_NEIGHBOR_MIN_APPROVED or more approved compounds.
    # This avoids ~45 extra Reactome + ChEMBL API calls for well-drugged targets.
    n_primary_approved = sum(
        1 for c in compounds
        if c.get("max_phase") is not None
        and _is_max_phase_approved(c.get("max_phase"))
    )
    neighbor_enriched: list[dict[str, Any]] = []
    if n_primary_approved >= PATHWAY_NEIGHBOR_MIN_APPROVED:
        print(
            f"[chemist] pathway-neighbor expansion SKIPPED for {symbol}: "
            f"primary pool has {n_primary_approved} approved compound(s) "
            f"(threshold={PATHWAY_NEIGHBOR_MIN_APPROVED})"
        )
    else:
        print(
            f"[chemist] pathway-neighbor expansion TRIGGERED for {symbol}: "
            f"primary pool has only {n_primary_approved} approved compound(s) "
            f"(threshold={PATHWAY_NEIGHBOR_MIN_APPROVED})"
        )
        neighbors = get_pathway_neighbor_targets(uniprot, disease_name)
        for nbr in neighbors:
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

                    # Stamp pathway specificity note on broad_metabolic neighbors so
                    # the Reviewer and Writer can surface this clearly.  A "broad_metabolic"
                    # tier (calibrated in data_sources/reactome.py) means the two proteins
                    # were grouped by shared substrate in a metabolic process pathway, NOT
                    # because they directly interact.  Reviewer must check cellular
                    # compartment and mechanism before trusting the repurposing hypothesis.
                    #
                    # KNOWN ARCHETYPE (recorded 2026-07):
                    #   GSD1c (glucose-6-phosphate transport, SLC37A4 in ER membrane) →
                    #   GAA discovered as pathway_neighbor via "Glycogen breakdown
                    #   (glycogenolysis)" [R-HSA-70221, 15 participants] →
                    #   MIGLITOL (intestinal alpha-glucosidase inhibitor) appears as
                    #   GAA-targeting candidate.
                    #   REJECTION REASONING: GAA operates in lysosomes and is the Pompe
                    #   disease target (GSD type II), unrelated to GSD1c's ER transport
                    #   defect.  MIGLITOL acts on MGA/MGAM (brush-border), not lysosomal
                    #   GAA.  The shared glycogen pathway is a metabolic process grouping,
                    #   NOT evidence of direct molecular interaction.
                    #   The mechanism_direction check (Step 1 GPT-5 web search) must catch
                    #   this; pathway_specificity_note surfaces it for human reviewers.
                    if nbr.get("specificity_tier") == "broad_metabolic":
                        pw_names = nbr.get("shared_pathway_names", [])
                        note = (
                            f"PATHWAY SPECIFICITY WARNING: {nbr_sym} is connected to the "
                            f"primary target ONLY via broad metabolic pathway(s): "
                            f"{', '.join(pw_names) if pw_names else 'unknown'}. "
                            f"These enzymes share a substrate but may operate in different "
                            f"cellular compartments and have unrelated mechanisms. "
                            f"Independent verification of compartment and mechanism "
                            f"compatibility is required before trusting this hypothesis."
                        )
                        for c in nbr_enriched:
                            c["pathway_specificity_note"] = note
                        print(
                            f"[chemist] WARNING: pathway_neighbor {nbr_sym} "
                            f"is broad_metabolic ({pw_names}); "
                            f"note stamped on {len(nbr_enriched)} compound(s)"
                        )

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
    # Each entry stores (compound_dict, raw_fp, desalted_fp) so we can exclude
    # salt/hydrate variants of the same active moiety from the reference set.
    approved_fps: dict[str, tuple[dict[str, Any], Any, Any]] = {}
    for e in all_enriched:
        if e["is_approved_drug"]:
            fp = _fingerprint(e["smiles"])
            if fp is not None:
                dfp = _desalted_fingerprint(e["smiles"])
                approved_fps[e["molecule_chembl_id"]] = (e, fp, dfp)

    # Pre-build parent_chembl_id lookup: maps molecule_chembl_id → parent_chembl_id
    # (or self if no parent is recorded).  Used for ChEMBL-hierarchy-based salt
    # exclusion, which catches organic acid salts (salicylate, tartrate, citrate)
    # that RDKit SaltRemover doesn't strip from the SMILES.
    # Example: PHYSOSTIGMINE (CHEMBL94) and PHYSOSTIGMINE SALICYLATE (CHEMBL…)
    # share the same parent_chembl_id so one is excluded as a reference for the other.
    _parent: dict[str, str | None] = {
        e["molecule_chembl_id"]: e.get("parent_chembl_id")
        for e in all_enriched
    }

    client = _anthropic_client()
    prov_entries: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

    for e in all_enriched:
        e_sym = e.get("target_symbol", symbol)
        e_uid = e.get("uniprot_id", uniprot)
        cand_fp = _fingerprint(e["smiles"])
        # Desalted fingerprint for salt-form exclusion check.
        cand_dfp = _desalted_fingerprint(e["smiles"])
        best_drug = None
        best_score = 0.0
        cand_id     = e["molecule_chembl_id"]
        cand_parent = _parent.get(cand_id)
        if cand_fp is not None:
            for mid, (ref, ref_fp, ref_dfp) in approved_fps.items():
                if mid == cand_id:
                    continue

                # ── Salt / hydrate exclusion (two complementary checks) ──────
                # 1. ChEMBL parent-hierarchy check (covers organic acid salts:
                #    salicylate, tartrate, citrate, maleate, etc.).
                #    If both molecules share the same ChEMBL parent_chembl_id,
                #    they are registered salt/hydrate forms of the same active
                #    moiety and one should not serve as a Tanimoto reference for
                #    the other.
                #    Example: PHYSOSTIGMINE (CHEMBL94) vs PHYSOSTIGMINE
                #    SALICYLATE share the same parent → excluded.
                ref_parent = _parent.get(mid)
                if (cand_parent and ref_parent
                        and cand_parent == ref_parent):
                    continue  # same active moiety (ChEMBL hierarchy)

                # 2. RDKit desalted-fingerprint check (catches simple inorganic
                #    salts HCl, NaBr, etc. even when ChEMBL parent is missing).
                #    Example: VERAPAMIL HYDROCHLORIDE vs VERAPAMIL free base
                #    → desalted Tanimoto 1.0 → excluded.
                if cand_dfp is not None and ref_dfp is not None:
                    desalted_sim = DataStructs.TanimotoSimilarity(cand_dfp, ref_dfp)
                    if desalted_sim >= 0.99:
                        continue  # same active moiety (desalted fingerprint)
                # ── End salt exclusion ─────────────────────────────────────────

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
            # Populated for pathway_neighbor candidates discovered via broad metabolic
            # pathways (specificity_tier == "broad_metabolic").  Surfaces in the report
            # so reviewers can judge compartment/mechanism compatibility independently.
            "pathway_specificity_note": e.get("pathway_specificity_note"),
        })

        for aid in e.get("source_activity_ids", []):
            prov_entries.append({
                "source_type": "chembl_activity",
                "source_id": aid,
                "used_by": "chemist",
                "context": f"{e['drug_name']} affinity vs {e_sym}",
            })

    provenance.log_many(prov_entries)

    # V2 target-first source union. Existing ChEMBL candidates enter as fully
    # enriched rows so their Tanimoto, mutation, rationale, and pathway fields
    # survive; GtoPdb and DrugCentral add approved target pharmacology without
    # any held-out-drug-name lookup. Identity and evidence deduplication are
    # delegated to the common active-moiety ledger.
    multisource = collect_target_candidates(
        uniprot_id=uniprot,
        gene=symbol or "",
        disease_name=disease_name,
        ot_score=ot_score,
        target_discovery_method=primary_disc_method,
        repurposing_only=repurposing_only,
        chembl_enriched=results,
        mechanism_class=target.get("mechanism_class") or "",
        therapeutic_role=target.get("therapeutic_role", "disease_modifying"),
        process_support=target.get("process_support", []),
        enabled_sources=enabled_sources,
    )
    results = multisource["candidates"]
    # Regulatory labels are a separate evidence lane for non-binding modalities
    # (antimetabolites, cofactors, pathway inhibitors).  Add them only after
    # target-first union, then re-merge once so the label never creates a
    # standalone candidate or provider-count bonus.
    label_records = [
        record for record in (_label_mechanism_record(candidate) for candidate in results)
        if record is not None
    ]
    if label_records:
        from data_sources.evidence_ledger import merge_candidates
        results = merge_candidates([*results, *label_records])
        multisource["source_status"]["openfda_label"] = {
            "status": "ok", "error": None, "release": None,
        }
    elif not _holdout.is_active():
        multisource["source_status"]["openfda_label"] = {
            "status": "empty", "error": None, "release": None,
        }
    for candidate in results:
        candidate.setdefault("atc_codes", [])
        candidate.setdefault("most_similar_approved_drug", None)
        candidate.setdefault("tanimoto_score", 0.0)
        candidate.setdefault("mutation_specificity", detect_mutation_specificity(""))
        candidate.setdefault(
            "rationale",
            "Target-first curated pharmacology evidence; see the evidence ledger "
            "for provider, lineage, action, and qualification details.",
        )
        candidate.setdefault("pathway_specificity_note", None)
        candidate["mechanism_class"] = target.get("mechanism_class")
        candidate["therapeutic_role"] = target.get(
            "therapeutic_role", "disease_modifying")
        candidate["process_support"] = target.get("process_support", [])
        candidate["process_source_status"] = target.get("process_source_status")

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
        "source_status": multisource["source_status"],
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
