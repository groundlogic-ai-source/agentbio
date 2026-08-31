"""
Writer Agent (Stage 3).

Compiles a human-readable Markdown repurposing report for each STRONG_MATCH
candidate produced by the Stage 2 Reviewer, enriched with the Stage 3 Boltz
structure / binding / ADME results.

Each report has EXACTLY these five sections, plus a static appendix:
  1. Hypothesis summary (one paragraph)
  2. Evidence table (affinity, structure confidence, ADME, network context)
  3. Full source citations (deduplicated PMIDs, ChEMBL activity IDs, NCT numbers)
  4. Composite score breakdown (every term of the Stage 2 formula, with weights)
  5. Limitations (the full standard list)
  6. How to read this dossier (static reader's guide — format explanation only,
     no candidate-specific claims; see _readers_guide_appendix)

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
# Retained as a compatibility seam for older tests/extensions that patch this
# symbol. Flagship rendering never calls it; trial evidence is persisted by the
# Reviewer and rendered from candidate["trial_audit"].
from data_sources.clinicaltrials import check_prior_trials  # noqa: F401

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


def _valid_citation_id(value: Any) -> Optional[str]:
    """Return a meaningful provider identifier, rejecting placeholder junk."""
    text = str(value or "").strip()
    folded = text.casefold()
    if (not text
            or folded in {"0", "none", "null", "n/a", "na", "unknown"}
            or folded.endswith(":0")
            or "identifier-unavailable" in folded
            or folded.startswith("unresolved-label-for:")):
        return None
    return text


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

    # Multi-source ledger identifiers.  The three citation classes above are
    # all ChEMBL/PubMed/NCT-shaped, so a candidate that entered through GtoPdb,
    # DrugCentral, BindingDB or a regulatory label rendered "none" on every
    # citation row while the dossier claimed full traceability.  Every provider
    # record carries its own source id — surface them.
    ledger_ids: dict[str, set[str]] = {}
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        provider = str(record.get("provider") or "").strip().lower()
        pid = _valid_citation_id(record.get("publication_id")) or ""
        if pid:
            # publication_id is not always a PMID — BindingDB stores a DOI when
            # no PMID exists. Mislabelling one as the other is a false citation.
            if pid.isdigit():
                pmids.add(pid)
            elif provider:
                ledger_ids.setdefault(provider, set()).add(f"doi/ref:{pid}")
        if provider in ("", "chembl"):
            # ChEMBL ids are already rendered as their own citation class.
            continue
        found_id = bool(pid)
        for key in ("source_id", "label_id", "trial_id"):
            value = _valid_citation_id(record.get(key))
            if value:
                ledger_ids.setdefault(provider, set()).add(str(value))
                found_id = True
        if not found_id:
            # The lane contributed evidence but carried no citable identifier —
            # say so rather than dropping the lane from the citation list.
            ledger_ids.setdefault(provider, set()).add(
                "⚠ record with no citable identifier")

    # Target-level literature confirmed by the biologist (PMID provenance).
    for h in (biologist_output or {}).get("literature_hits", []):
        if h.get("pmid") is not None:
            pmids.add(str(h["pmid"]))

    # NCT numbers already retrieved by the Reviewer. The Writer is a pure
    # renderer: it must not silently refresh evidence after scoring.
    ncts: set[str] = set()
    for trial in (candidate.get("trial_audit") or {}).get("trials", []):
        if trial.get("nct_id"):
            ncts.add(str(trial["nct_id"]))

    return {
        "pmids": sorted(pmids),
        "chembl_activity_ids": sorted(chembl_acts),
        "nct_numbers": sorted(ncts),
        # provider -> sorted source identifiers (GtoPdb interactions/ligands,
        # DrugCentral struct ids, BindingDB assay anchors, openFDA label ids).
        "source_records": {
            provider: sorted(ids) for provider, ids in sorted(ledger_ids.items())
        },
    }


# Score terms that can be genuinely UNOBSERVED rather than measured.  When the
# Reviewer reports None for one of these it dropped the term from both sides of
# the composite, so the breakdown must show it as excluded, not as a zero.
# Maps score_components key -> (basis field, fallback wording).
_COVERAGE_GAP_TERMS = {
    "normalized_ot_association": (
        "ot_association_basis",
        "no measured target-disease association",
    ),
    "no_failed_trial": (
        "trial_evidence_basis",
        "trial lookup unavailable",
    ),
    "normalized_tanimoto": (
        "tanimoto_basis",
        "no resolvable structure comparison",
    ),
}


def _composite_breakdown(candidate: dict[str, Any], formula: dict[str, Any]) -> str:
    weights = formula.get("composite_weights", {})
    comp = candidate.get("score_components", {}) or {}
    # term key -> (display label, score_components key)
    # v2 composite schema uses a single efficacy_evidence term; legacy payloads
    # (pre-v2 formula blocks) carried separate pchembl/confidence weights.
    # Render whichever schema the run's own formula actually used so the
    # printed arithmetic always matches how the score was computed.
    if "efficacy_evidence" in weights:
        rows = [
            ("efficacy_evidence",
             "Efficacy evidence — calibrated confidence (not measured efficacy; "
             "evidence-ledger calibrated; legacy fallback: 0.6 × normalized "
             "pChEMBL + 0.4 × assay confidence)",
             "efficacy_evidence"),
            ("ot_association", "Normalized Open Targets association", "normalized_ot_association"),
            ("tanimoto", "Normalized Tanimoto similarity", "normalized_tanimoto"),
            ("no_failed_trial", "No prior failed trial (1/0)", "no_failed_trial"),
        ]
    else:
        rows = [
            ("pchembl", "Normalized pChEMBL affinity", "normalized_pchembl"),
            ("confidence", "Assay confidence (score / 9)", "confidence_term"),
            ("ot_association", "Normalized Open Targets association", "normalized_ot_association"),
            ("tanimoto", "Normalized Tanimoto similarity", "normalized_tanimoto"),
            ("no_failed_trial", "No prior failed trial (1/0)", "no_failed_trial"),
        ]
    lines = ["| Term | Weight | Component value | Contribution |",
             "| --- | ---: | ---: | ---: |"]
    if formula.get("scoring_config_overridden"):
        lines.insert(0, "")
        lines.insert(0, "> **Non-default scoring configuration.** This run used "
                        "operator-overridden scoring weights "
                        "(`AGENTBIO_TRACTABILITY_WEIGHTS` / "
                        "`AGENTBIO_COMPOSITE_WEIGHTS`). Its scores are **not "
                        "comparable** to the frozen benchmark v2 numbers.")
    subtotal = 0.0
    excluded: list[str] = []
    for wkey, label, ckey in rows:
        w = float(weights.get(wkey, 0.0))
        val = comp.get(ckey)
        if val is None and ckey in _COVERAGE_GAP_TERMS:
            # The observation was never made, so the Reviewer dropped this term
            # from BOTH the numerator and the denominator.  Printing a 0.0000
            # contribution against its full weight would misreport how the
            # score was actually computed and would read as adverse evidence.
            basis_key, default_basis = _COVERAGE_GAP_TERMS[ckey]
            basis = comp.get(basis_key) or default_basis
            excluded.append(f"{label} ({basis})")
            lines.append(f"| {label} | excluded | not observed — {basis} | — |")
            continue
        contrib = w * float(val) if isinstance(val, (int, float)) else 0.0
        subtotal += contrib
        lines.append(f"| {label} | {w:.2f} | {_fmt(val)} | {contrib:.4f} |")

    bonus = comp.get("qualified_directional_bonus") or 0.0
    if bonus:
        lines.append(f"| Qualified directional evidence bonus | — | — | +{bonus:.4f} |")

    penalty = 0.0
    if candidate.get("lipinski_penalty_applied"):
        penalty = float(formula.get("lipinski_penalty", 0.0))
        lines.append(f"| Lipinski penalty (>1 violation) | — | — | -{penalty:.4f} |")

    if candidate.get("unapproved_cap_applied"):
        lines.append("| Unapproved-compound cap (hard gate, max 0.400) | — | — | applied |")

    if candidate.get("mechanism_cap_applied"):
        mdir = candidate.get("mechanism_direction") or {}
        verdict = mdir.get("verdict", "DIRECTIONALLY_INCOMPATIBLE")
        lines.append(
            f"| Mechanism-direction cap ({verdict}, hard gate, max 0.400) | — | — | applied |"
        )

    if candidate.get("safety_cap_applied"):
        s2 = candidate.get("safety_layer2") or {}
        layer_parts: list[str] = []
        if (candidate.get("safety_layer1") or {}).get("confirmed"):
            layer_parts.append("ChEMBL safety signal")
        if s2.get("confirmed"):
            layer_parts.append("web-search safety signal")
        layer_str = " + ".join(layer_parts) if layer_parts else "safety signal"
        lines.append(
            f"| Safety cap ({layer_str}, hard gate, max 0.400) | — | — | applied |"
        )

    _any_cap = (candidate.get("unapproved_cap_applied")
                or candidate.get("mechanism_cap_applied")
                or candidate.get("safety_cap_applied"))
    pre_cap = candidate.get("pre_cap_score")
    if _any_cap and isinstance(pre_cap, (int, float)):
        lines.append(f"| **Pre-cap score (before hard caps)** | | | **{_fmt(pre_cap, 4)}** |")

    total = candidate.get("composite_score")
    lines.append(f"| **Composite (renormalized weighted sum + bonus − penalty, capped)** | | | **{_fmt(total, 4)}** |")
    lines.append("")

    cap_notes = []
    if candidate.get("unapproved_cap_applied"):
        cap_notes.append("Unapproved-compound cap applied (capped at 0.400).")
    if candidate.get("mechanism_cap_applied"):
        mdir = candidate.get("mechanism_direction") or {}
        verdict = mdir.get("verdict", "DIRECTIONALLY_INCOMPATIBLE")
        reason  = mdir.get("reason") or "drug mechanism is incompatible with target's causal role in disease"
        cap_notes.append(
            f"Mechanism-direction cap applied (capped at 0.400): {verdict} — {reason}"
        )
    if candidate.get("safety_cap_applied"):
        badge = candidate.get("status_badge", "")
        cap_notes.append(
            f"Safety cap applied (capped at 0.400) — {badge}" if badge else
            "Safety cap applied (capped at 0.400): known adverse indication or safety signal detected."
        )

    cap_note = (" " + " ".join(cap_notes)) if cap_notes else ""
    # Show every arithmetic step so the printed table reproduces the reported
    # score: observed-term subtotal → renormalize over covered weight →
    # + directional bonus → − Lipinski penalty → caps → composite_score.
    coverage = comp.get("evidence_weight_coverage")
    summary = f"Weighted sum of observed terms = {subtotal:.4f}"
    # The Reviewer ALWAYS divides the numerator by the covered weight — not
    # only when terms were dropped.  With non-unit-sum weight overrides the
    # covered weight differs from 1 even with full observation, so the
    # division must be displayed whenever it is not the identity.
    if (isinstance(coverage, (int, float)) and 0 < coverage
            and (excluded or abs(float(coverage) - 1.0) > 1e-9)):
        summary += (f"; renormalized over covered weight "
                    f"(÷ {coverage:.4f}) = {subtotal / coverage:.4f}")
    if bonus:
        summary += f"; directional bonus = +{bonus:.4f}"
    summary += (f"; penalty = {penalty:.4f}; "
                f"reported composite_score = {_fmt(total, 4)}.{cap_note}")
    lines.append(summary)

    if excluded:
        coverage = comp.get("evidence_weight_coverage")
        lines.append("")
        lines.append(
            "**Coverage note.** " + "; ".join(excluded) + ". "
            "These observations were never made, so they were excluded from "
            "the score rather than recorded as zero — the remaining terms are "
            "renormalized over the weight actually covered"
            + (f" ({_fmt(coverage, 2)} of 1.00)." if coverage is not None else ".")
            + " This is not a credit for missing data: a *measured* zero "
            "(including a real failed trial) still counts against the "
            "candidate. It means the pipeline could not see this evidence, "
            "so the candidate is scored on what is actually known about it."
        )
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


def _modality_cell(candidate: dict[str, Any]) -> str:
    """
    Render the ChEMBL modality DISCLOSURE (molecule type / route) for the
    evidence table. Disclosure only — never affects any score. An unresolved
    lookup is stated plainly, never silently rendered as "clear".
    """
    mtype = candidate.get("chembl_molecule_type")
    oral = candidate.get("chembl_oral")
    # Missingness must stay unresolved: an unknown route must never be
    # rendered as a concrete "non-oral" (or vice versa).
    if mtype is None or oral is None:
        return "unresolved (ChEMBL molecule lookup unavailable)"
    route = "oral" if oral else "non-oral"
    base = f"{mtype} / {route}"
    if candidate.get("nonoral_biologic_flag"):
        return f"⚠ {base} (non-oral biologic — empirical caution flag; see modality disclosure above)"
    return base


def _discovery_method_cell(candidate: dict[str, Any]) -> str:
    """How the target was surfaced — never guessed, never blank."""
    method = str(candidate.get("target_discovery_method") or "").strip()
    if not method:
        return ("⚠ unattributed — the provenance of this target was not "
                "recorded; treat the target-disease link as unverified")
    return method


def _target_tier_cell(candidate: dict[str, Any]) -> str:
    """Causal-anchor tier plus the rank-demotion disclosure, when it applies."""
    tier = str(candidate.get("target_tier") or "").strip() or "unknown"
    labels = {
        "causal_anchor": "causal anchor (direct disease-target association)",
        "clinical_precedent": "clinical precedent (approved for this disease concept)",
        "exploratory_expansion": "⚠ exploratory (reached by pathway expansion, "
                                 "not a direct disease-target link)",
        "unattributed": "⚠ unattributed (target provenance not recorded)",
    }
    cell = labels.get(tier, tier)
    if candidate.get("exploratory_rank_demoted"):
        anchor = candidate.get("causal_anchor") or {}
        cell += (
            f" — rank-demoted below the anchored candidate "
            f"{anchor.get('drug_name')} ({anchor.get('target_symbol')}, "
            f"{anchor.get('target_discovery_method')}); scores unchanged"
        )
    return cell


def _approval_basis_cell(candidate: dict[str, Any]) -> str:
    """What positively established this compound's regulatory approval."""
    basis = str(candidate.get("approval_basis") or "").strip()
    providers = candidate.get("approval_evidence_providers") or []
    if not basis:
        return "not recorded"
    if basis == "unknown":
        return ("⚠ NOT established — no qualified regulatory-approval record "
                "and no max_phase ≥ 4 was found for this compound")
    if providers:
        return f"{basis} ({', '.join(providers)})"
    return basis


def _confidence_band(value: Any) -> str:
    """Qualitative 0-1 confidence band used only for report prose."""
    if not isinstance(value, (int, float)):
        return "unavailable"
    bounded = max(0.0, min(1.0, float(value)))
    if bounded < 0.33:
        return "low"
    if bounded < 0.67:
        return "moderate"
    return "high"


def _direct_chembl_activity_note(candidate: dict[str, Any]) -> str:
    """Explain whether the dossier has a qualifying direct ChEMBL assay."""
    activity_ids = {
        str(value).strip()
        for value in candidate.get("source_activity_ids", [])
        if _valid_citation_id(value)
    }
    for group in ("counted_once", "collapsed_as_duplicate"):
        for record in (candidate.get("provenance") or {}).get(group, []):
            if record.get("source_type") == "chembl_activity":
                value = _valid_citation_id(record.get("source_id"))
                if value:
                    activity_ids.add(value)
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if (record.get("provider") == "chembl"
                and record.get("source_type") == "bioactivity_assay"
                and record.get("qualification_status") in (None, "", "qualified")):
            value = (
                _valid_citation_id(record.get("assay_id"))
                or _valid_citation_id(record.get("source_id"))
            )
            if value:
                activity_ids.add(value)
    if (candidate.get("pchembl_value") is not None
            and candidate.get("confidence_score") is not None
            and activity_ids):
        return (
            f"Direct assay-backed: {len(activity_ids)} qualifying ChEMBL "
            "Homo sapiens IC50/Ki activity record(s)"
        )
    return (
        "No qualifying direct ChEMBL Homo sapiens IC50/Ki activity record "
        "at assay confidence ≥ 8 was found; potency and assay confidence "
        "are therefore unavailable."
    )


def _efficacy_provenance_cell(candidate: dict[str, Any]) -> str:
    """Describe modalities behind the calibrated evidence value."""
    modalities: set[str] = set()
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if record.get("qualification_status") not in (None, "", "qualified"):
            continue
        if record.get("evidence_role") not in ("efficacy", "target_link", "disease_link"):
            continue
        source_type = str(record.get("source_type") or "").strip()
        if source_type:
            modalities.add(source_type.replace("_", " "))
    source = (candidate.get("score_components") or {}).get("efficacy_evidence_source")
    assay_backed = "bioactivity assay" in modalities
    if source == "legacy_pchembl_assay_confidence":
        return "assay-backed legacy pChEMBL + assay-confidence calculation"
    if modalities:
        basis = ", ".join(sorted(modalities))
        prefix = "assay-backed" if assay_backed else "not direct-assay-backed"
        return (
            f"{prefix}; calibrated from qualified {basis} evidence. "
            "This is evidence confidence, not a measured probability of efficacy."
        )
    return (
        "provenance unavailable; this calibrated evidence value is not a "
        "measured probability of efficacy"
    )


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
        ("Direct ChEMBL activity basis", _direct_chembl_activity_note(candidate)),
        ("Calibrated evidence-confidence provenance",
         _efficacy_provenance_cell(candidate)),
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
        ("PubChem XLogP (lipophilicity)",
         (f"⚠ {_fmt(candidate.get('pubchem_xlogp'), 2)} (≥ 5 — empirical caution flag; "
          f"see high-lipophilicity disclosure above)"
          if candidate.get("high_lipophilicity_flag")
          else _fmt(candidate.get("pubchem_xlogp"), 2))),
        ("ChEMBL modality (type / route)", _modality_cell(candidate)),
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
        ("Prior trials for this exact drug+disease",
         ("⚠ query failed (API unreachable) — trial count unavailable; the "
          "trial term was excluded from the score as a coverage gap "
          "(neither credited nor penalised)"
          if candidate.get("trials_query_failed")
          else _fmt(candidate.get("prior_trial_count")))),
        # NEVER default this to a discovery method: a blank or missing value
        # means the provenance was lost, and silently printing
        # "genetic_association" would assert a disease link nobody established.
        ("Target discovery method", _discovery_method_cell(candidate)),
        ("Target tier", _target_tier_cell(candidate)),
        ("Approval basis", _approval_basis_cell(candidate)),
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
        # Show the full list when short; otherwise truncate WITH an explicit
        # "+N more". Printing "9 — A, B, C, D, E" (count larger than the visible
        # list) reads as an error to a careful reviewer.
        if names and len(names) <= 10:
            name_str = ", ".join(names)
        elif names:
            name_str = ", ".join(names[:10]) + f" (+{len(names) - 10} more)"
        else:
            name_str = "see ChEMBL"
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

    # EFO resolution mismatch warning: check the candidate first (manual mode),
    # then fall back to biologist_output["target"] (both modes propagate it there).
    efo_warn = candidate.get("efo_name_mismatch_warning") or (
        (biologist_output or {}).get("target", {}).get("efo_name_mismatch_warning")
    )

    bullet_list = [
        f"- **Binding is not efficacy.** The binding-pose confidence is "
        f"**{_confidence_band(cx.get('binding_pose_confidence'))}** "
        f"({_fmt(cx.get('binding_pose_confidence'))}); the relative predicted "
        f"affinity is **{_confidence_band(cx.get('predicted_affinity'))}** "
        f"({_fmt(cx.get('predicted_affinity'))}). These predictions only suggest "
        f"the molecule may "
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
    ]
    if efo_warn:
        # Prepend so the mismatch is the first thing a reviewer reads.
        bullet_list.insert(0, f"- ⚠ {efo_warn}")
    bullets = "\n".join(bullet_list)
    druggability = _druggability_subsection(biologist_output)
    if druggability:
        return bullets + "\n\n" + druggability
    return bullets


_READERS_GUIDE_VERSION = "1.2"


def _audit_value(value: Any) -> str:
    """Render missing evidence as an explicit unknown state."""
    if value is None or value == "":
        return "UNKNOWN"
    return _fmt(value)


def _assay_audit_table(candidate: dict[str, Any]) -> str:
    """Lossless, auditable assay rows from the existing evidence ledger only."""
    rows = []
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if str(record.get("source_type") or "") != "bioactivity_assay":
            continue
        rows.append((
            _audit_value(record.get("provider")),
            _audit_value(record.get("assay_id") or record.get("source_id")),
            _audit_value(record.get("target_symbol") or record.get("target_accession")),
            _audit_value(record.get("measurement_type")),
            _audit_value(record.get("measurement_value")),
            _audit_value(record.get("measurement_unit")),
            _audit_value(record.get("target_species")),
            _audit_value(record.get("qualification_status")),
        ))
    if not rows:
        return ("No ledger bioactivity-assay rows were available. This is an "
                "explicit absence of reportable rows, not evidence of no binding.")
    lines = [
        "| Provider | Record / assay ID | Target | Measurement | Value | Unit | Species | Qualification |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in sorted(set(rows)):
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _comparator_table(candidate: dict[str, Any],
                      biologist_output: Optional[dict[str, Any]]) -> str:
    """Render only comparators supplied by the biologist or reviewed payload."""
    contract = candidate.get("dossier_evidence_contract") or {}
    comparators = contract.get("comparators") or {}
    target_drugs = comparators.get("target_approved_drugs")
    if target_drugs is None:
        target_drugs = ((biologist_output or {}).get("druggability_context") or {}).get(
            "approved_drugs", [])
    selected = comparators.get("selected_candidates", [])
    lines = ["| Comparator | Source / relationship | Approval or score |",
             "| --- | --- | --- |"]
    for row in target_drugs or []:
        lines.append(
            f"| {_audit_value(row.get('name'))} | biologist ChEMBL target-approved drug | "
            f"max phase {_audit_value(row.get('max_phase'))} |")
    for row in selected or []:
        lines.append(
            f"| {_audit_value(row.get('drug_name'))} | current reviewed-pool candidate | "
            f"composite {_audit_value(row.get('composite_score'))}; approval "
            f"{_audit_value(row.get('approval_basis'))} |")
    if len(lines) == 2:
        lines.append("| NONE RECORDED | No biologist approved-drug or reviewed-pool comparator was supplied | UNKNOWN |")
    return "\n".join(lines)


def _apply_matched_biologist_context(
    candidate: dict[str, Any],
    biologist_output: Optional[dict[str, Any]],
) -> None:
    """Refresh target-specific contract facts from the Writer's matched context.

    Multi-target review pools are scored together, while Writer receives the
    correctly matched biologist payload for each candidate. Replace only the
    target-specific fields; persisted trial/readiness facts remain untouched.
    """
    if not biologist_output:
        return
    bio_target = (biologist_output.get("target") or {}).get("target_symbol")
    if not bio_target or str(bio_target).upper() != str(
            candidate.get("target_symbol") or "").upper():
        return
    contract = candidate.get("dossier_evidence_contract")
    if not isinstance(contract, dict):
        return
    context = contract.setdefault("disease_mechanism_context", {})
    context["literature_pmids"] = [
        str(hit["pmid"]) for hit in biologist_output.get("literature_hits", [])
        if hit.get("pmid") is not None
    ]
    comparators = contract.setdefault("comparators", {})
    comparators["target_approved_drugs"] = (
        (biologist_output.get("druggability_context") or {}).get(
            "approved_drugs", [])
    )


def _readiness_and_context(candidate: dict[str, Any], struct: dict[str, Any]) -> str:
    contract = candidate.get("dossier_evidence_contract") or {}
    readiness = contract.get("scientific_readiness") or {}
    context = contract.get("disease_mechanism_context") or {}
    cx = (struct or {}).get("complex") or {}
    gates = readiness.get("blocking_gates") or []
    structure_state = ("AVAILABLE (prediction only)" if cx else
                       readiness.get("structure_prediction", "UNKNOWN"))
    lines = [
        "### Evidence-stage verdict and scientific readiness\n",
        f"- **Evidence-stage verdict:** `{_audit_value(contract.get('evidence_stage_verdict'))}`.",
        f"- **Readiness:** `{_audit_value(readiness.get('status'))}`. This is not a clinical efficacy verdict.",
        f"- **Qualified human target-assay evidence:** "
        f"`{_audit_value(readiness.get('qualified_human_target_assay_evidence', readiness.get('direct_human_assay_evidence')))}`. "
        "This establishes target pharmacology only, not rescue of the disease-causing variant.",
        f"- **Mutation-specific evidence:** `{_audit_value(readiness.get('mutation_specific_evidence'))}`.",
        f"- **Disease-model evidence:** `{_audit_value(readiness.get('disease_model_evidence'))}`.",
        f"- **Clinical efficacy evidence:** `{_audit_value(readiness.get('clinical_efficacy_evidence'))}`.",
        f"- **Structure evidence:** `{_audit_value(structure_state)}`.",
        f"- **Blocking gates:** {', '.join(gates) if gates else 'NONE RECORDED'}.",
        "\n### Disease and mechanism context\n",
        f"- Disease: {_audit_value(context.get('disease_name', candidate.get('disease_name')))}; "
        f"target: {_audit_value(context.get('target_symbol', candidate.get('target_symbol')))}.",
        f"- Target discovery: {_audit_value(context.get('target_discovery_method', candidate.get('target_discovery_method')))}; "
        f"therapeutic role: {_audit_value(context.get('therapeutic_role', candidate.get('therapeutic_role')))}; "
        f"mechanism class: {_audit_value(context.get('mechanism_class', candidate.get('mechanism_class')))}.",
        "- Process support: " + (
            ", ".join(str(x) for x in context.get("process_support", []))
            or "UNKNOWN"
        ) + ".",
    ]
    return "\n".join(lines)


def _trial_safety_applicability_audit(candidate: dict[str, Any]) -> str:
    """Report known, negative, and unknown states without triggering lookups."""
    audit = candidate.get("trial_audit") or {}
    trials = audit.get("trials") or []
    lines = ["### Novelty and prior-trial audit\n",
             f"- Trial query state: `{_audit_value(audit.get('query_status'))}`.",
             f"- Negative repurposing result: `{_audit_value(audit.get('negative_repurposing_result'))}`.",
             f"- Exact drug+disease trials returned: "
             f"{_audit_value(audit.get('trial_count'))}."]
    if trials:
        lines += ["| NCT | Status | Results posted | Stop classification |",
                  "| --- | --- | --- | --- |"]
        for trial in trials:
            lines.append(
                f"| {_audit_value(trial.get('nct_id'))} | {_audit_value(trial.get('status'))} | "
                f"{_audit_value(trial.get('has_results'))} | "
                f"{_audit_value(trial.get('why_stopped_classification'))} |")
    else:
        lines.append("- No individual trial rows were returned (this does not establish novelty).")
    l1, l2 = candidate.get("safety_layer1") or {}, candidate.get("safety_layer2") or {}
    lines += [
        "\n### Safety and applicability matrix\n",
        "| Domain | Existing evidence | State |",
        "| --- | --- | --- |",
        f"| Regulatory safety | {_audit_value(candidate.get('status_badge'))} | "
        f"{'FLAGGED' if candidate.get('safety_cap_applied') else 'NOT FLAGGED / UNKNOWN'} |",
        f"| Structured safety lane | {_audit_value(l1.get('disclosure_text'))} | "
        f"{'OBSERVED' if l1 else 'UNKNOWN'} |",
        f"| Independent safety lane | {_audit_value(l2.get('disclosure_text'))} | "
        f"{'OBSERVED' if l2 else 'NOT ASSESSED'} |",
        f"| Route / modality | {_modality_cell(candidate)} | "
        f"{'OBSERVED' if candidate.get('chembl_molecule_type') is not None else 'UNKNOWN'} |",
        "| Relevant tissue/cell/compartment exposure | Not assessed by this pipeline | UNKNOWN |",
        "| Effective tolerable human exposure, dose and PK | Not assessed by this pipeline | UNKNOWN |",
        "| Disease stage/subtype and therapeutic window | Not assessed by this pipeline | UNKNOWN |",
    ]
    return "\n".join(lines)


def _readers_guide_appendix() -> str:
    """Static "How to read this dossier" appendix (reader's guide).

    Deliberately STATIC text: it explains the dossier's format and vocabulary
    only. It must never state or imply anything about the specific candidate —
    keeping it claim-free means it cannot introduce an unverifiable statement
    into an otherwise claim-audited document.
    """
    return f"""Every section above reports numbers computed by deterministic code from
the sources cited in Section 3. This appendix explains what the terms mean; it
makes no claims about the candidate itself.

**What this document is.** A machine-generated repurposing *hypothesis*,
produced by a staged pipeline (target selection → literature/bioactivity
evidence → candidate scoring → structure prediction → this report) and then
held for a mandatory human review before the run is marked complete. The human
checkpoint gates completion of the record — the structure prediction (the
expensive step) has already run by the time a person is asked. This document
is a prioritised starting point for expert review, not a clinical conclusion.

**Therapeutic applicability is not scored.** The ranking does not assess
whether a drug reaches the relevant tissue, cell, or compartment at an
effective, tolerable human exposure. Route, dose, pharmacokinetics (PK),
disease stage/subtype, and therapeutic window require expert review.
Unknown must not be interpreted as compatible. This
limitation is disclosure-only: it introduces no tissue-specific score, cap, or
gate.

**Composite score.** A weighted sum of the evidence terms listed in Section
4's table (calibrated evidence confidence, the Open Targets target–disease association,
Tanimoto structural similarity to approved drugs for the same target, and
absence of prior failed trials), each scored 0–1, plus any qualified
directional-evidence bonus and minus any penalties — giving a single 0–1
number. **STRONG_MATCH** requires a composite at or above the
threshold shown in Section 4 *and* no hard cap in effect.

**Hard caps.** Certain findings cap the score at 0.400 no matter how strong the
rest of the evidence is: a *safety cap* (withdrawn status or black-box-class
signal), a *mechanism-direction cap* (the drug acts on the target in the
opposite direction to what the disease biology requires), and an
*unapproved-compound cap* (the hit is not an approved drug, so it is not a
repurposing candidate at all). Where a cap applies, Section 4 shows both the
uncapped score (`pre_cap_score`) and the cap that fired. A capped candidate can
still be scientifically interesting — the cap says "not an approvable
repurposing pick as-is", not "no biology here".

**Coverage renormalization.** When a data source could not be checked for this
candidate (source unreachable, identifier unresolvable, or the term is a
stamped constant for the whole pool), that term is **dropped from both the
numerator and the denominator** — it is never silently scored as zero. Section
4 shows the renormalization explicitly ("covered weight" < 1.0000 means some
terms were dropped). A renormalized score is comparable in scale but rests on
less evidence; treat heavy renormalization as "unscored", not "clean".

**Evidence table (Section 2).** Each row reports one measured or predicted
quantity and the source that produced it.

- **Calibrated evidence confidence** — a modality-aware score assembled from
  qualified assay, mechanism, label, genetic, publication, pathway, or trial
  evidence available for the candidate. It is not a measured probability of
  efficacy. The table states whether direct qualifying ChEMBL activity supports
  it.
- **pChEMBL** — −log10 of molar potency from ChEMBL assays; higher = more
  potent. Reported as a median over *Homo sapiens* IC50/Ki assays at ChEMBL
  confidence ≥ 8.
- **Tanimoto similarity** — structural fingerprint similarity (0–1) to
  approved drugs known to act on the same target. It is computed **within the
  retrieved candidate pool only**: a low value means "unlike the other drugs
  retrieved for this target", not "globally novel chemistry".
- **Structure confidence / affinity** — Boltz *predictions* for the
  protein–ligand complex. Confidence (pLDDT-like) reflects how well the model
  thinks it placed the atoms; predicted affinity suggests the molecule may
  occupy the target. Neither is a measurement, and neither establishes
  agonism, antagonism, or therapeutic benefit.
- **ADME** — lipophilicity, permeability, solubility and related values are
  model *predictions*, not experimental measurements.

**Citations (Section 3).** Provenance may include PMIDs (PubMed), ChEMBL
activity/approval/mechanism records, NCT numbers (ClinicalTrials.gov), Open
Targets, openFDA label or safety records, PubChem, AlphaFold DB, Boltz, GtoPdb,
DrugCentral, BindingDB, Reactome, and BioGRID. Section 3 lists stable record
identifiers carried in the candidate ledger; model outputs and database-derived
values are identified by their source family even when no PMID, activity ID, or
NCT number applies.

**Limitations (Section 5).** The standard caveats that apply to every dossier —
read them before acting on any number in this report.

_Reader's guide v{_READERS_GUIDE_VERSION}. A longer engineering-level
description of the pipeline (sources, formulas, and the exact role of each AI
call) is in `docs/HOW_AGENTBIO_WORKS.md` in the project repository._
"""


def build_report_markdown(candidate: dict[str, Any], struct: dict[str, Any],
                          formula: dict[str, Any],
                          biologist_output: Optional[dict[str, Any]],
                          target_meta: Optional[dict[str, Any]] = None,
                          repurposing_only: bool = False,
                          k_target_summary: Optional[dict[str, Any]] = None) -> str:
    drug = candidate.get("drug_name", "Unknown drug")
    target = candidate.get("target_symbol", "?")
    disease = candidate.get("disease_name", "?")
    strong = candidate.get("strong_match")
    threshold = formula.get("strong_match_threshold")
    _apply_matched_biologist_context(candidate, biologist_output)

    network = (biologist_output or {}).get("interacting_genes", [])[:8]
    biogrid_status = (biologist_output or {}).get("biogrid_query_status", "")
    if biogrid_status == "query_failed":
        net_str = "⚠ BioGRID query failed (API error) — network context unavailable"
    elif biogrid_status == "no_key":
        net_str = "⚠ BioGRID API key not configured — network context unavailable"
    elif network:
        net_str = ", ".join(network)
    else:
        net_str = "none found (query succeeded; no interactions in BioGRID for this gene)"

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

    # General therapeutic-applicability disclosure. Informational only: this
    # intentionally adds no tissue-specific score, penalty, cap, or gate.
    parts.append(
        "> ⚠ **Therapeutic applicability not assessed.** Ranking does not assess "
        "whether this drug reaches the relevant tissue, cell, or compartment at "
        "an effective, tolerable human exposure. Route, dose, pharmacokinetics "
        "(PK), disease stage/subtype, and therapeutic window require expert "
        "review. **Unknown must not be interpreted as "
        "compatible.**\n\n"
    )

    parts.append(header_note)

    # Repurposing-only pool disclosure — tells the reviewer the candidate pool
    # was restricted to approved drugs at collection time.
    if repurposing_only:
        parts.append(
            "> **Repurposing-only pool:** the candidate compounds for this target "
            "were restricted to approved / established drugs at collection time "
            "and re-checked against the merged pool afterwards. Approval must be "
            "positively evidenced (a qualified regulatory-approval record, or "
            "max_phase ≥ 4); a compound whose approval status cannot be resolved "
            "is excluded, not merely down-ranked.\n\n"
        )
        # Fail-loud contradiction check: the pool claim above must be true for
        # the candidate actually being recommended.
        if str(candidate.get("approval_basis") or "") == "unknown" or \
                candidate.get("is_approved_drug") is not True:
            parts.append(
                "> ⚠ **Disclosure conflict:** this dossier declares a "
                "repurposing-only pool, but the candidate above has no "
                "positively established regulatory approval. Treat the "
                "repurposing framing as unsupported for this candidate.\n\n"
            )

    # K-target evaluation summary — visible count of how many of the K targets
    # were successfully evaluated so a partial failure is not invisible.
    if k_target_summary and k_target_summary.get("k_pursued", 1) > 1:
        k_note = k_target_summary.get("note", "")
        failed = k_target_summary.get("failed_targets", [])
        if failed:
            parts.append(
                f"> ⚠ **Partial evaluation ({k_note})** "
                f"Targets that failed: {', '.join(failed)}. "
                "Compounds from those targets are absent from this run's pool. "
                "Re-running the job will retry them.\n\n"
            )
        else:
            parts.append(
                f"> ℹ **Top-K evaluation: {k_note}**\n\n"
            )

    # Pathway-neighbor disclosure — surfaced when the candidate's target was
    # discovered via Reactome pathway adjacency rather than a direct OT association.
    disc_method = candidate.get("target_discovery_method", "")
    if disc_method == "pathway_neighbor":
        parts.append(
            "> ℹ **Pathway-neighbor candidate.** "
            f"The target **{target}** was not directly linked to **{disease}** "
            "via Open Targets; it was discovered because it co-participates in "
            "the same Reactome pathway(s) as the primary causal gene. "
            f"Open Targets association score for this target: "
            f"{_fmt(candidate.get('ot_association_score'))} (0 = no direct link). "
            "The drug–target binding evidence (pChEMBL, confidence) is real; "
            "only the disease-relevance link is inferred from pathway adjacency.\n\n"
        )

    # Black-box warning advisory — surfaced when ChEMBL records black_box_warning=True
    # but the drug has NOT been withdrawn from any market.  A boxed warning means
    # serious risks require prescriber attention; it does NOT mean the drug is
    # unavailable.  More than 30% of FDA-approved drugs carry boxed warnings
    # (warfarin, clozapine, SSRIs, fluoroquinolones, thalidomide+REMS, brexanolone…).
    # This banner is disclosure only — it does NOT affect any score.
    if candidate.get("black_box_advisory"):
        l1 = (candidate.get("safety_layer1") or {})
        l2 = (candidate.get("safety_layer2") or {})
        # Advisory can come from L1 structured data, the L2 web check, or both —
        # name the source(s) honestly instead of always attributing to ChEMBL.
        l1_bb = l1.get("black_box_advisory", False)
        l2_bb = l2.get("black_box_advisory", False)
        bbw_url = l1.get("source_url") or l2.get("citation") or ""
        url_text = f" ([source]({bbw_url}))" if bbw_url else ""
        if l1_bb and l2_bb:
            bb_src = "ChEMBL structured data and an independent web search both record"
        elif l2_bb and not l1_bb:
            bb_src = "A web-search safety check records"
        else:
            bb_src = "ChEMBL records"
        parts.append(
            f"> ⚠ **Black-box (boxed) warning — disclosure only.** "
            f"{bb_src} a regulatory black-box warning for **{drug}**{url_text}. "
            f"The drug is still approved and available; this warning reflects serious "
            f"risks (sedation, haematological effects, teratogenicity, etc.) that require "
            f"monitoring in its approved indication. "
            f"**This flag does not affect the composite score.** "
            f"The reviewer must judge whether these risks are acceptable in the context "
            f"of the proposed repurposing indication.\n\n"
        )

    # DILI-screening target disclosure — surfaced when the candidate's target is
    # a well-known pharmaceutical safety-profiling target (BSEP/ABCB11, hERG/KCNH2,
    # P-gp/ABCB1, CYP enzymes, etc.).  Activity records for these proteins in ChEMBL
    # commonly originate from DILI / cardiac-safety screening assays (companies test
    # drugs against them to detect liver/heart toxicity risk BEFORE approval), NOT from
    # therapeutic-intent binding studies.  A high pChEMBL against BSEP does NOT mean
    # the drug is a good treatment for a BSEP-deficiency disease — it may mean the
    # drug is a hepatotoxicity risk.  This disclosure does NOT affect scoring.
    _DILI_SCREEN_TARGETS: frozenset[str] = frozenset({
        "ABCB11", "BSEP", "KCNH2", "HERG", "ABCB1", "MDR1",
        "ABCC2", "MRP2", "CYP3A4", "CYP2D6", "CYP2C9", "CYP2C19", "CYP1A2", "SCN5A",
    })
    if target.upper() in _DILI_SCREEN_TARGETS:
        parts.append(
            f"> ⚠ **DILI/safety-screening target (disclosure).** "
            f"**{target}** is a well-known pharmaceutical safety-profiling target. "
            f"Drug companies routinely measure IC50/Ki of candidate drugs against "
            f"{target} to detect DRUG-INDUCED LIVER INJURY (DILI) or cardiac toxicity "
            f"risk *before* regulatory submission — not because those drugs are intended "
            f"to treat diseases caused by {target} dysfunction. "
            f"The pChEMBL value in this report may therefore come from a **safety-screening "
            f"assay** (recording a toxicity liability) rather than a therapeutic-intent "
            f"binding study. Verify the source assay context in ChEMBL before treating "
            f"this binding data as evidence of a therapeutic mechanism. "
            f"This disclosure does not affect any score.\n\n"
        )

    # High-lipophilicity DISCLOSURE — surfaced when PubChem XLogP >= 5.
    # The bisociation analysis (run-629a01b9) found XLogP >= 5 is associated
    # with 0.426x odds of repurposing success (broad-framing Fisher's exact
    # p = 3×10⁻⁹, holdout-confirmed p = 0.009, survives adjustment for
    # established-product status and CNS-area membership). Direction is opposite
    # to the original hypothesis — lipophilic drugs are LESS likely to succeed.
    # An incumbency-confound caveat is unresolved (Task 14 confirmation run).
    # This banner is disclosure only — it does NOT affect any score.
    # Modality DISCLOSURE — surfaced when the candidate is a non-oral biologic.
    # Confirmed registry finding run-704c0cb4-H05: non-oral, non-small-molecule
    # agents have ~0.30x odds of repurposing success (narrow framing, discovery
    # q=4.7e-2, holdout confirmation p=2.8e-4, survives established-maturity
    # adjustment; broad framing LABEL_ARTIFACT_SUSPECT). Disclosure only.
    if candidate.get("nonoral_biologic_flag"):
        _mtype = candidate.get("chembl_molecule_type") or "biologic"
        parts.append(
            f"> ⚠ **Non-oral biologic ({_mtype}) — empirical modality caution.** "
            f"**{drug}** is a non-orally-administered {_mtype}. A registry-confirmed "
            f"analysis (hypothesis run-704c0cb4-H05, narrow genuine-outcome framing) "
            f"found that non-oral, non-small-molecule agents have **0.30× the odds of "
            f"repurposing success** (95% CI [0.121, 0.657], n=2,642; discovery FDR "
            f"q = 4.7×10⁻²; independent holdout confirmation p = 2.8×10⁻⁴; survives "
            f"adjustment for established-product maturity). Two candidate confounds "
            f"(prior repurposing base rate, oncology phase mix) were not testable. "
            f"The delivery constraints of this modality class historically suppress "
            f"cross-indication spread — treat this candidate's rank as carrying a "
            f"higher external-validation burden, not as a disqualifier. "
            f"**This flag does not affect the composite score.** "
        )
    if candidate.get("high_lipophilicity_flag"):
        _xlogp_val = candidate.get("pubchem_xlogp")
        _xlogp_str = f"{_xlogp_val:.2f}" if _xlogp_val is not None else "≥ 5"
        parts.append(
            f"> ⚠ **High lipophilicity (XLogP = {_xlogp_str}) — empirical caution flag.** "
            f"**{drug}** has a PubChem XLogP of {_xlogp_str}, above the threshold of 5. "
            f"A dataset-derived analysis (repoDB, n ≈ 8,700 drug–disease pairs) found that "
            f"drugs with XLogP ≥ 5 have **0.426× the odds of repurposing success** "
            f"(Fisher's exact p = 3×10⁻⁹; holdout p = 0.009; survives adjustment for "
            f"established-product status and CNS-area membership). The direction of effect "
            f"is opposite to the original hypothesis — high-lipophilicity drugs repurpose "
            f"*less* often, not more. "
            f"**Unresolved caveat:** an incumbency-confound (lipophilic drugs are "
            f"disproportionately represented in historically studied disease areas) has not "
            f"yet been fully ruled out; treat this as an alert rather than a disqualifier. "
            f"**This flag does not affect the composite score.** "
            f"The reviewer must judge whether lipophilicity is a material concern in the "
            f"context of the proposed repurposing indication.\n\n"
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
        f"the target **{target}**. "
        f"{_direct_chembl_activity_note(candidate)} It has an Open Targets "
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
        # Unmet-need reconciliation disclosure: OT links approved drugs at the
        # DISEASE level (or a parent-umbrella EFO). Syndromic diseases whose
        # treatable manifestations live under separate EFO nodes (e.g. MEN2A,
        # treated via its medullary-thyroid-carcinoma manifestation) can show
        # unmet_need_score ≈ 1.0 while approved mechanism-drugs exist for the
        # causal target. Surface that tension instead of leaving an apparent
        # contradiction between this score and the druggability section.
        _dc = (biologist_output or {}).get("druggability_context") or {}
        _tgt_approved = (
            _dc.get("approved_drug_count", 0)
            if _dc.get("has_approved_drug_for_target") else 0
        )
        _no_disease_therapy = (
            meta.get("has_approved_treatment") is False
            or (
                meta.get("has_approved_treatment") is not True
                and isinstance(unmet, (int, float)) and unmet >= 0.9
            )
        )
        if _tgt_approved and _no_disease_therapy:
            parts.append(
                f"\n> **Unmet-need reconciliation:** Open Targets links no approved "
                f"therapy to this disease's own EFO record, yet {_tgt_approved} "
                f"approved drug(s) with known mechanism against the selected target "
                f"exist (see Target druggability context). For syndromic diseases "
                f"this usually means an approved therapy treats a manifestation "
                f"recorded under a different EFO node (e.g. medullary thyroid "
                f"carcinoma for MEN2A). The unmet_need_score above reflects "
                f"disease-level OT linkage only and may overstate unmet need — "
                f"judge accordingly.\n"
            )

    # 2. Evidence table
    parts.append("\n## 2. Evidence table\n")
    parts.append(_evidence_table(candidate, struct) + "\n")
    parts.append("\n" + _readiness_and_context(candidate, struct) + "\n")
    parts.append("\n### Auditable assay rows\n")
    parts.append(_assay_audit_table(candidate) + "\n")
    parts.append("\n### Comparator table\n")
    parts.append(_comparator_table(candidate, biologist_output) + "\n")

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
    source_records = cites.get("source_records") or {}
    if source_records:
        for provider, ids in source_records.items():
            shown = ", ".join(ids[:25])
            more = f" (+{len(ids) - 25} more)" if len(ids) > 25 else ""
            parts.append(
                f"- **{provider} record ids ({len(ids)}):** {shown}{more}\n"
            )
    else:
        parts.append(
            "- **Other source record ids:** none recorded in the candidate "
            "evidence ledger\n"
        )
    parts.append("\n" + _trial_safety_applicability_audit(candidate) + "\n")

    # 4. Composite breakdown
    parts.append("\n## 4. Composite score breakdown\n")
    parts.append(
        f"**Reproducibility contract:** dossier evidence "
        f"`{_audit_value((candidate.get('dossier_evidence_contract') or {}).get('contract_version'))}`; "
        f"scoring formula `{_audit_value(formula.get('formula_version'))}`; "
        f"safety schema `{_audit_value(formula.get('safety_schema_version'))}`. "
        "Arithmetic below is recomputed from this candidate's persisted score components.\n\n"
    )
    parts.append(_composite_breakdown(candidate, formula) + "\n")

    # 5. Limitations
    parts.append("\n## 5. Limitations\n")
    parts.append(_limitations(candidate, struct, biologist_output) + "\n")

    # 6. Static reader's guide (format explanation only — no candidate claims)
    parts.append("\n## 6. How to read this dossier\n")
    parts.append(_readers_guide_appendix() + "\n")

    return "".join(parts)


def run_writer(reviewed: dict[str, Any], selected: list[dict[str, Any]],
               structure_results: dict[str, Any],
               biologist_output: Optional[dict[str, Any]] = None,
               target: Optional[dict[str, Any]] = None,
               bio_for_candidate: Optional[Any] = None,
               k_target_summary: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """
    Write one Markdown report per selected candidate. Returns a list of
    {drug, disease, path, strong_match} descriptors.

    bio_for_candidate: optional callable(candidate) -> biologist_output dict.
    When supplied (Top-K multi-target runs), each candidate receives its own
    biologist context in the Limitations/druggability section.  Falls back to
    the top-level biologist_output when the callable returns None.

    k_target_summary: when present, a visible "N of K targets evaluated" note
    is embedded in every report.  Partial failures are flagged with a warning
    callout; full success is noted informatively.
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
                                   repurposing_only=repurposing_only,
                                   k_target_summary=k_target_summary)
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
