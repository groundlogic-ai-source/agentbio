"""
Writer Agent (Stage 3).

Compiles a human-readable Markdown repurposing report for each STRONG_MATCH
candidate produced by the Stage 2 Reviewer, enriched with the Stage 3 Boltz
structure / binding / ADME results.

Each report has EXACTLY these five sections:
  1. Hypothesis summary (one paragraph)
  2. Evidence table (affinity, structure confidence, ADME, network context)
  3. Full source citations (deduplicated PMIDs, ChEMBL activity IDs, NCT numbers)
  4. Composite score breakdown (every term of the Stage 2 formula, with weights)
  5. Limitations (the full standard list)

Reports are written to output/reports/{disease}_{drug}.md.

This agent invents NO new facts: it only restates numbers already produced by
Stages 1-3. The composite breakdown is recomputed from the candidate's own
score_components and the formula weights carried in the reviewed payload, so the
arithmetic is auditable against reviewed_candidates.json.
"""

import os
import re
from typing import Any, Optional

from agents.target_selection import OUTPUT_DIR
from data_sources.clinicaltrials import check_prior_trials

REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip())
    return s.strip("_") or "unknown"


def _fmt(v: Any, nd: int = 3) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}" if isinstance(v, float) else str(v)
    return str(v)


def _citations(candidate: dict[str, Any],
               biologist_output: Optional[dict[str, Any]]) -> dict[str, list[str]]:
    """Gather every PMID, ChEMBL activity id, and NCT number used, deduplicated."""
    pmids: set[str] = set()
    chembl_acts: set[str] = set()

    prov = candidate.get("provenance", {}) or {}
    for group in ("counted_once", "collapsed_as_duplicate"):
        for p in prov.get(group, []):
            st, sid = p.get("source_type"), p.get("source_id")
            if sid is None:
                continue
            if st == "pmid":
                pmids.add(str(sid))
            elif st == "chembl_activity":
                chembl_acts.add(str(sid))

    # Target-level literature confirmed by the biologist (PMID provenance).
    for h in (biologist_output or {}).get("literature_hits", []):
        if h.get("pmid") is not None:
            pmids.add(str(h["pmid"]))

    # NCT numbers used for the prior-trial check (cache-first; no new spend).
    ncts: set[str] = set()
    drug = candidate.get("drug_name")
    disease = candidate.get("disease_name")
    if drug and disease:
        try:
            pt = check_prior_trials(drug, disease)
            for t in pt.get("trials", []):
                if t.get("nct_id"):
                    ncts.add(t["nct_id"])
        except Exception:
            pass

    return {
        "pmids": sorted(pmids),
        "chembl_activity_ids": sorted(chembl_acts),
        "nct_numbers": sorted(ncts),
    }


def _composite_breakdown(candidate: dict[str, Any], formula: dict[str, Any]) -> str:
    weights = formula.get("composite_weights", {})
    comp = candidate.get("score_components", {}) or {}
    # term key -> (display label, score_components key)
    rows = [
        ("pchembl", "Normalized pChEMBL affinity", "normalized_pchembl"),
        ("confidence", "Assay confidence (score / 9)", "confidence_term"),
        ("ot_association", "Normalized Open Targets association", "normalized_ot_association"),
        ("tanimoto", "Normalized Tanimoto similarity", "normalized_tanimoto"),
        ("no_failed_trial", "No prior failed trial (1/0)", "no_failed_trial"),
    ]
    lines = ["| Term | Weight | Component value | Contribution |",
             "| --- | ---: | ---: | ---: |"]
    subtotal = 0.0
    for wkey, label, ckey in rows:
        w = float(weights.get(wkey, 0.0))
        val = comp.get(ckey)
        contrib = w * float(val) if isinstance(val, (int, float)) else 0.0
        subtotal += contrib
        lines.append(f"| {label} | {w:.2f} | {_fmt(val)} | {contrib:.4f} |")

    penalty = 0.0
    if candidate.get("lipinski_penalty_applied"):
        penalty = float(formula.get("lipinski_penalty", 0.0))
        lines.append(f"| Lipinski penalty (>1 violation) | — | — | -{penalty:.4f} |")

    if candidate.get("unapproved_cap_applied"):
        lines.append("| Unapproved-compound cap (hard gate, max 0.400) | — | — | applied |")

    total = candidate.get("composite_score")
    lines.append(f"| **Composite (weighted sum − penalty − cap)** | | | **{_fmt(total, 4)}** |")
    lines.append("")
    cap_note = " Unapproved-compound cap applied (capped at 0.400)." if candidate.get("unapproved_cap_applied") else ""
    lines.append(f"Weighted sum before penalty = {subtotal:.4f}; "
                 f"penalty = {penalty:.4f}; "
                 f"reported composite_score = {_fmt(total, 4)}.{cap_note}")
    return "\n".join(lines)


def _cif_link(cx: dict[str, Any]) -> str:
    """
    Return a markdown link to the Boltz CIF file.
    Prefers the locally-cached file (permanent, served by /api/structures/).
    Falls back to the raw S3 pre-signed URL with a warning that it expires.
    """
    fname = cx.get("local_cif_filename")
    if fname:
        return f"[Download CIF](/api/structures/{fname})"
    s3 = cx.get("pdb_or_cif_url")
    if s3:
        return f"[Download CIF (⚠ link may be expired)]({s3})"
    return "n/a"


def _mutation_specificity_cell(candidate: dict[str, Any]) -> str:
    """
    Render the mutation-specificity DISCLOSURE flag for the evidence table.
    Disclosure only — this does not assert the repurposing target carries the
    mutation and never affects any score.
    """
    ms = candidate.get("mutation_specificity") or {}
    if not ms.get("is_mutation_specific"):
        return "No specific mutation named in approved indication"
    terms = ", ".join(ms.get("matched_terms", [])) or "see label"
    return f"⚠ YES — indication names: {terms}"


def _evidence_table(candidate: dict[str, Any], struct: dict[str, Any]) -> str:
    cx = (struct or {}).get("complex") or {}
    adme = (struct or {}).get("adme") or {}
    afdb = (struct or {}).get("afdb") or {}
    desc = candidate.get("descriptors", {}) or {}
    ae = candidate.get("adverse_events", []) or []
    ae_str = ", ".join(
        f"{e.get('term')} ({e.get('count')})" for e in ae[:5]
    ) if ae else "none reported"

    rows = [
        ("ChEMBL median pChEMBL affinity", _fmt(candidate.get("pchembl_value"), 2)),
        ("Assay confidence score (0-9)", _fmt(candidate.get("confidence_score"))),
        ("Open Targets association score", _fmt(candidate.get("ot_association_score"))),
        ("Tanimoto to nearest approved drug",
         f"{_fmt(candidate.get('tanimoto_score'), 3)} "
         f"({candidate.get('most_similar_approved_drug') or 'none in set'})"),
        ("Approved / known drug", (
            "⚠ EXPERIMENTAL COMPOUND — NOT YET APPROVED"
            if candidate.get("is_approved_drug") is False
            else _fmt(candidate.get("is_approved_drug"))
        )),
        ("Mutation-specific approved indication (disclosure)",
         _mutation_specificity_cell(candidate)),
        ("Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB)",
         f"{_fmt(desc.get('molecular_weight'),1)}, {_fmt(desc.get('logp'),2)}, "
         f"{_fmt(desc.get('h_bond_donors'))}, {_fmt(desc.get('h_bond_acceptors'))}, "
         f"{_fmt(desc.get('tpsa'),1)}, {_fmt(desc.get('rotatable_bonds'))}"),
        ("Lipinski violations / Veber pass",
         f"{_fmt(desc.get('lipinski_violations'))} / {_fmt(desc.get('veber_pass'))}"),
        ("AFDB apo structure mean pLDDT (free protein, no ligand)",
         _fmt(afdb.get("mean_plddt"), 1)),
        ("Boltz structure confidence (0-1)", _fmt(cx.get("structure_confidence"))),
        ("Boltz binding-pose confidence (0-1)", _fmt(cx.get("binding_pose_confidence"))),
        ("Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd)",
         _fmt(cx.get("predicted_affinity"))),
        ("Boltz predicted structure (CIF)", _cif_link(cx)),
        ("Boltz ADME — lipophilicity (logD)", _fmt(adme.get("lipophilicity"))),
        ("Boltz ADME — permeability", _fmt(adme.get("permeability"))),
        ("Boltz ADME — solubility", _fmt(adme.get("solubility"))),
        ("openFDA adverse-event signal (FAERS)", ae_str),
        ("Prior trials for this exact drug+disease", _fmt(candidate.get("prior_trial_count"))),
        ("Target discovery method",
         candidate.get("target_discovery_method", "genetic_association")),
    ]
    lines = ["| Evidence | Value |", "| --- | --- |"]
    for k, v in rows:
        lines.append(f"| {k} | {v} |")
    return "\n".join(lines)


def _druggability_subsection(biologist_output: Optional[dict[str, Any]]) -> str:
    """
    Render the 'Target druggability context' subsection from the druggability_context
    field produced by the Biologist agent.  Informational only — no scoring impact.
    """
    dc = (biologist_output or {}).get("druggability_context") or {}
    if not dc:
        return ""

    lines = ["### Target druggability context\n"]

    count = dc.get("approved_drug_count", 0)
    has_approved = dc.get("has_approved_drug_for_target", False)
    if has_approved:
        names = [d.get("name") for d in dc.get("approved_drugs", []) if d.get("name")]
        name_str = ", ".join(names[:5]) if names else "see ChEMBL"
        lines.append(
            f"- **Approved drugs with known mechanism against this target (ChEMBL):** "
            f"{count} — {name_str}"
        )
    else:
        lines.append(
            "- **No approved drug currently exists with a known mechanism against this "
            "target** (ChEMBL mechanism endpoint, Homo sapiens only)."
        )

    flag = dc.get("druggability_flag", "")
    summary = dc.get("difficulty_summary")
    pmids = dc.get("supporting_pmids", [])

    if summary:
        lines.append(f"- **Historical difficulty signal:** {summary}")
        if pmids:
            lines.append(
                f"  - Supporting PMIDs: {', '.join(str(p) for p in pmids)}"
            )
    else:
        if flag == "insufficient literature signal":
            lines.append(
                "- **Historical difficulty literature:** insufficient signal found "
                "(fewer than 2 qualifying abstracts in targeted PubMed searches for "
                f"undruggability / resistance / difficulty)."
            )
        else:
            lines.append("- **Historical difficulty literature:** not available.")

    lines.append(
        "\n_Druggability context is informational only. It does not affect "
        "tractability\\_score, unmet\\_need\\_score, composite\\_score, or STRONG\\_MATCH._"
    )
    return "\n".join(lines)


def _limitations(candidate: dict[str, Any], struct: dict[str, Any],
                 biologist_output: Optional[dict[str, Any]] = None) -> str:
    cx = (struct or {}).get("complex") or {}
    afdb = (struct or {}).get("afdb") or {}
    sconf = cx.get("structure_confidence")
    plddt_complex = ((cx.get("raw_metrics") or {}).get("structure_metrics") or {}).get("complex_plddt")
    apo_plddt = afdb.get("mean_plddt")
    bullets = "\n".join([
        f"- **Binding is not efficacy.** A high binding-pose confidence "
        f"({_fmt(cx.get('binding_pose_confidence'))}) or predicted affinity "
        f"({_fmt(cx.get('predicted_affinity'))}) only suggests the molecule may "
        f"occupy the target; it does NOT establish agonism vs. antagonism, "
        f"functional modulation, or therapeutic benefit.",
        "- **ADME values are model predictions, not measurements.** The Boltz "
        "lipophilicity/permeability/solubility numbers are computed estimates and "
        "must be confirmed experimentally before any decision.",
        f"- **Structure confidence is bounded.** This hypothesis relies on a Boltz "
        f"structure_confidence of {_fmt(sconf)} (complex pLDDT "
        f"{_fmt(plddt_complex)}) and an AFDB apo mean pLDDT of {_fmt(apo_plddt, 1)}; "
        f"the AFDB model contains NO ligand, so the protein-ligand pose is entirely "
        f"a Boltz prediction.",
        "- **Assay-type and species caveats.** The affinity is a median pChEMBL over "
        "Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the "
        "bounded approved-drug reference set for Tanimoto still apply.",
        "- **Absence of evidence is not evidence of absence.** A zero prior-trial "
        "count or no adverse-event signal may reflect that the pair has simply never "
        "been studied, not that it is safe or untried.",
        "- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised "
        "starting point that requires wet-lab and, ultimately, clinical validation.",
    ])
    druggability = _druggability_subsection(biologist_output)
    if druggability:
        return bullets + "\n\n" + druggability
    return bullets


def build_report_markdown(candidate: dict[str, Any], struct: dict[str, Any],
                          formula: dict[str, Any],
                          biologist_output: Optional[dict[str, Any]],
                          target_meta: Optional[dict[str, Any]] = None,
                          repurposing_only: bool = False) -> str:
    drug = candidate.get("drug_name", "Unknown drug")
    target = candidate.get("target_symbol", "?")
    disease = candidate.get("disease_name", "?")
    strong = candidate.get("strong_match")
    threshold = formula.get("strong_match_threshold")

    network = (biologist_output or {}).get("interacting_genes", [])[:8]
    net_str = ", ".join(network) if network else "none mapped"

    cites = _citations(candidate, biologist_output)

    header_note = ""
    if not strong:
        header_note = (
            f"> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold "
            f"(composite {_fmt(candidate.get('composite_score'), 4)} < "
            f"{_fmt(threshold, 2)}). It is included as the highest-ranked hypothesis "
            f"for review; treat it accordingly.\n\n"
        )

    parts = []
    parts.append(f"# Repurposing hypothesis: {drug} → {disease}\n")

    # Unapproved-compound banner — must be the very first thing a reviewer sees.
    if candidate.get("is_approved_drug") is False:
        parts.append(
            "> ⚠ **EXPERIMENTAL COMPOUND — NOT YET APPROVED.**  \n"
            "> This compound does not have regulatory approval and has no established "
            "human safety profile from prior clinical use. It is a research-grade "
            "binding hit, **not a repurposing candidate**. Drug repurposing requires "
            "an approved or known drug as the starting point. Its composite score is "
            f"hard-capped at 0.400 (threshold for STRONG_MATCH is "
            f"{_fmt(threshold, 2)}) and it cannot reach STRONG_MATCH regardless of "
            "its other scores.\n\n"
        )

    parts.append(header_note)

    # Repurposing-only pool disclosure — tells the reviewer the candidate pool
    # was restricted to approved drugs at collection time.
    if repurposing_only:
        parts.append(
            "> **Repurposing-only pool:** the candidate compounds for this target "
            "were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) "
            "at collection time. Unapproved research-grade tool compounds were "
            "excluded from the pool, not merely down-ranked.\n\n"
        )

    # Mutation-specificity DISCLOSURE caveat — surfaced whenever the drug's
    # approved indication names a specific mutation, so the reviewer knows the
    # precedent may not transfer to the (possibly unmutated) repurposing disease.
    ms = candidate.get("mutation_specificity") or {}
    if ms.get("is_mutation_specific"):
        terms = ", ".join(ms.get("matched_terms", [])) or "a specific mutation"
        parts.append(
            "> ⚠ **Mutation-specific approval (disclosure).** "
            f"{drug}'s approved / known indication explicitly names {terms}. "
            "This is a DISCLOSURE flag only: it does NOT assert that the "
            f"repurposing target **{target}** in **{disease}** carries that "
            "mutation, and it does not change any score. The reviewer must judge "
            "whether the mutation-scoped precedent transfers to this indication.\n\n"
        )

    # 1. Hypothesis summary
    parts.append("## 1. Hypothesis summary\n")
    parts.append(
        f"{drug} is proposed as a repurposing candidate against **{disease}** via "
        f"the target **{target}**. It shows a ChEMBL median pChEMBL affinity of "
        f"{_fmt(candidate.get('pchembl_value'), 2)} at assay confidence "
        f"{_fmt(candidate.get('confidence_score'))}/9, an Open Targets "
        f"target-disease association of {_fmt(candidate.get('ot_association_score'))}, "
        f"and a Tanimoto similarity of {_fmt(candidate.get('tanimoto_score'), 3)} to "
        f"{candidate.get('most_similar_approved_drug') or 'no approved analog in the set'}. "
        f"Target network context (BioGRID, physical/genetic — not mechanism): {net_str}. "
        f"The resulting composite score is "
        f"{_fmt(candidate.get('composite_score'), 4)}.\n"
    )
    if candidate.get("rationale"):
        parts.append(f"\n_Chemist rationale:_ {candidate['rationale']}\n")

    # Stage 1 prioritization scores — the SAME two-dimensional scores the ranking
    # sweep computes, shown here whether the target was auto-ranked or hand-picked.
    meta = target_meta or {}
    tract = meta.get("tractability_score")
    unmet = meta.get("unmet_need_score")
    if tract is not None or unmet is not None:
        parts.append("\n### Stage 1 prioritization scores\n")
        parts.append(
            f"- **tractability_score:** {_fmt(tract, 4)} "
            f"(ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)\n"
            f"- **unmet_need_score:** {_fmt(unmet, 4)} "
            f"(treatment availability + prevalence)\n\n"
            f"These are computed by the same formulas used to rank the full "
            f"rare-disease / NTD universe; a manually chosen target is scored "
            f"identically, never faked or skipped.\n"
        )

    # 2. Evidence table
    parts.append("\n## 2. Evidence table\n")
    parts.append(_evidence_table(candidate, struct) + "\n")

    # 3. Citations
    parts.append("\n## 3. Full source citations\n")
    parts.append(
        f"- **PMIDs ({len(cites['pmids'])}):** "
        + (", ".join(cites["pmids"]) if cites["pmids"] else "none") + "\n"
    )
    parts.append(
        f"- **ChEMBL activity IDs ({len(cites['chembl_activity_ids'])}):** "
        + (", ".join(cites["chembl_activity_ids"]) if cites["chembl_activity_ids"] else "none")
        + "\n"
    )
    parts.append(
        f"- **NCT numbers ({len(cites['nct_numbers'])}):** "
        + (", ".join(cites["nct_numbers"]) if cites["nct_numbers"] else "none") + "\n"
    )

    # 4. Composite breakdown
    parts.append("\n## 4. Composite score breakdown\n")
    parts.append(_composite_breakdown(candidate, formula) + "\n")

    # 5. Limitations
    parts.append("\n## 5. Limitations\n")
    parts.append(_limitations(candidate, struct, biologist_output) + "\n")

    return "".join(parts)


def run_writer(reviewed: dict[str, Any], selected: list[dict[str, Any]],
               structure_results: dict[str, Any],
               biologist_output: Optional[dict[str, Any]] = None,
               target: Optional[dict[str, Any]] = None,
               bio_for_candidate: Optional[Any] = None) -> list[dict[str, Any]]:
    """
    Write one Markdown report per selected candidate. Returns a list of
    {drug, disease, path, strong_match} descriptors.

    bio_for_candidate: optional callable(candidate) -> biologist_output dict.
    When supplied (Top-K multi-target runs), each candidate receives its own
    biologist context in the Limitations/druggability section.  Falls back to
    the top-level biologist_output when the callable returns None.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    formula = reviewed.get("formula", {}) or {}
    repurposing_only = bool(reviewed.get("repurposing_only", False))
    written: list[dict[str, Any]] = []

    for cand in selected:
        drug = cand.get("drug_name", "unknown")
        disease = cand.get("disease_name", "unknown")
        struct = (structure_results or {}).get(drug, {})
        # Per-candidate biologist output (matched by target_symbol when available).
        cand_bio = biologist_output
        if bio_for_candidate is not None:
            resolved = bio_for_candidate(cand)
            if resolved is not None:
                cand_bio = resolved
        md = build_report_markdown(cand, struct, formula, cand_bio, target,
                                   repurposing_only=repurposing_only)
        fname = f"{_slug(disease)}_{_slug(drug)}.md"
        path = os.path.join(REPORTS_DIR, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        written.append({
            "drug": drug,
            "disease": disease,
            "path": path,
            "strong_match": bool(cand.get("strong_match")),
        })

    return written
