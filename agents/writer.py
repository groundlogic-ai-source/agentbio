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

import copy
import os
import re
from typing import Any, Optional

from agents.target_selection import OUTPUT_DIR
# Retained as a compatibility seam for older tests/extensions that patch this
# symbol. Flagship rendering never calls it; trial evidence is persisted by the
# Reviewer and rendered from candidate["trial_audit"].
from data_sources.clinicaltrials import check_prior_trials  # noqa: F401
from data_sources.evidence_ledger import qualified_target_chembl_activity_ids

REPORTS_DIR = os.path.join(OUTPUT_DIR, "reports")
DOSSIER_CONTRACT_VERSION = "flagship-dossier-evidence-v2"


def _normalized_compound_alias(value: Any) -> str:
    """Normalize presentation aliases without conflating unrelated drugs."""
    name = str(value or "").strip().casefold()
    return {
        "glyburide": "glibenclamide",
        "glibenclamide": "glibenclamide",
    }.get(name, name)


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


def validate_dossier_inputs(
    candidate: dict[str, Any],
    biologist_output: Optional[dict[str, Any]],
    target_meta: Optional[dict[str, Any]],
    *,
    repurposing_only: bool,
) -> None:
    """Fail closed when persisted report inputs disagree.

    This is intentionally pure and belongs before structure prediction in the
    graph. A report must never spend on Boltz—or reach human review—when its
    authoritative evidence layers cannot be reconciled deterministically.
    """
    errors: list[str] = []
    drug = str(candidate.get("drug_name") or "unknown candidate")
    candidate_target = str(candidate.get("target_symbol") or "").upper()
    candidate_accession = str(candidate.get("uniprot_id") or "").upper()

    contract = candidate.get("dossier_evidence_contract")
    production_contract = False
    if not isinstance(contract, dict):
        errors.append("missing dossier_evidence_contract")
        contract = {}
    elif contract.get("contract_version") not in {
        DOSSIER_CONTRACT_VERSION,
        "flagship-dossier-evidence-v1",  # frozen benchmark compatibility
    }:
        errors.append(
            "unsupported dossier contract "
            f"{contract.get('contract_version')!r}"
        )
    else:
        production_contract = (
            contract.get("contract_version") == DOSSIER_CONTRACT_VERSION)

    context = contract.get("disease_mechanism_context") or {}
    contract_target = str(context.get("target_symbol") or "").upper()
    contract_disease = str(context.get("disease_name") or "").casefold()
    candidate_disease = str(candidate.get("disease_name") or "").casefold()
    if contract_target and contract_target != candidate_target:
        errors.append(
            f"contract target {contract_target} != candidate target {candidate_target}"
        )
    if contract_disease and contract_disease != candidate_disease:
        errors.append("contract disease != candidate disease")

    bio_target = ((biologist_output or {}).get("target") or {})
    bio_symbol = str(bio_target.get("target_symbol") or "").upper()
    bio_accession = str(bio_target.get("uniprot_id") or "").upper()
    if biologist_output is not None and not (
        (candidate_accession and bio_accession == candidate_accession)
        or (candidate_target and bio_symbol == candidate_target)
    ):
        errors.append(
            f"Biologist target {bio_symbol or '?'}/{bio_accession or '?'} "
            f"does not match candidate {candidate_target or '?'}/"
            f"{candidate_accession or '?'}"
        )

    meta_symbol = str((target_meta or {}).get("target_symbol") or "").upper()
    meta_accession = str((target_meta or {}).get("uniprot_id") or "").upper()
    if target_meta is not None and not (
        (candidate_accession and meta_accession == candidate_accession)
        or (candidate_target and meta_symbol == candidate_target)
    ):
        errors.append(
            f"Stage 1 target {meta_symbol or '?'}/{meta_accession or '?'} "
            f"does not match candidate {candidate_target or '?'}/"
            f"{candidate_accession or '?'}"
        )

    approval_basis = str(candidate.get("approval_basis") or "")
    if repurposing_only and (
        candidate.get("is_approved_drug") is not True
        or approval_basis in {"", "unknown"}
    ):
        errors.append(
            "repurposing-only candidate lacks positive approval provenance"
        )
    if (
        candidate.get("is_approved_drug") is False
        and not candidate.get("unapproved_cap_applied")
    ):
        errors.append("unapproved candidate lacks the required score cap")
    if production_contract and candidate.get(
            "target_applicability") in {"PATHWAY_ONLY", "UNKNOWN"}:
        errors.append(
            "target applicability does not permit a headline or paid validation")
    if production_contract and (candidate.get("availability_gate") or {}).get(
            "blocks_prioritization"):
        errors.append("active ingredient is confirmed globally unavailable")
    coverage = candidate.get("candidate_source_coverage") or {}
    if production_contract and coverage.get("complete") is not True:
        errors.append("enabled candidate-source coverage is incomplete or unknown")
    if production_contract and candidate.get("paid_validation_eligible") is not True:
        errors.append(
            "candidate did not clear the final deterministic paid-validation gate")

    method = str(candidate.get("target_discovery_method") or "").lower()
    score_components = candidate.get("score_components") or {}
    ot_basis = score_components.get("ot_association_basis")
    normalized_ot = score_components.get("normalized_ot_association")
    if "pharmacological_precedent" in method and (
        ot_basis != "precedent_stamped_constant" or normalized_ot is not None
    ):
        errors.append(
            "pharmacological-precedent target carries a scored/measured "
            "Open Targets value"
        )

    readiness = contract.get("scientific_readiness") or {}
    if readiness.get("status") == "HYPOTHESIS_REQUIRES_EXPERIMENTAL_VALIDATION":
        errors.append("dossier contract mandates experimental validation")

    candidate_name = _normalized_compound_alias(candidate.get("drug_name"))
    candidate_names = {
        candidate_name,
        *(_normalized_compound_alias(value)
          for value in candidate.get("compound_aliases", [])),
    }
    candidate_ids = {
        str(value).strip().casefold()
        for value in (
            candidate.get("molecule_chembl_id"),
            candidate.get("parent_chembl_id"),
            *((candidate.get("source_chembl_ids") or [])),
            *((candidate.get("source_molecule_chembl_ids") or [])),
        )
        if value
    }
    for row in (contract.get("comparators") or {}).get(
            "target_approved_drugs", []):
        row_name = _normalized_compound_alias(row.get("name"))
        row_ids = {
            str(value).strip().casefold()
            for value in (
                row.get("molecule_chembl_id"),
                row.get("parent_chembl_id"),
                *((row.get("source_molecule_chembl_ids") or [])),
            )
            if value
        }
        if row_name in candidate_names or candidate_ids.intersection(row_ids):
            errors.append("lead candidate appears in its own comparator set")
            break

    if errors:
        raise ValueError(
            f"dossier preflight failed for {drug}: " + "; ".join(errors)
        )


def _display_token(value: Any) -> str:
    """Render internal enum/status tokens as professional reader-facing text."""
    text = _audit_value(value)
    if text == "UNKNOWN":
        return "Unknown"
    if "_" in text or (text.isupper() and any(ch.isalpha() for ch in text)):
        return text.replace("_", " ").lower().capitalize()
    return text


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

    # The provenance compatibility list can contain legacy values.  The ledger
    # is authoritative for a ChEMBL activity citation: only a ChEMBL
    # bioactivity record may contribute one, never a provider metadata id.
    ledger_activity_ids = set(qualified_target_chembl_activity_ids(
        ((candidate.get("_evidence_ledger") or {}).get("records") or []),
        target_symbol=candidate.get("target_symbol") or "",
        target_accession=candidate.get("uniprot_id") or "",
    ))
    if ledger_activity_ids:
        chembl_acts = ledger_activity_ids
    elif (candidate.get("_evidence_ledger") or {}).get("records"):
        # A populated ledger with no qualifying ChEMBL activity must render
        # none, rather than reclassifying DrugCentral/label metadata from an
        # old provenance list as ChEMBL activity.
        chembl_acts = set()

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
    for row in (candidate.get("literature_limitation") or {}).get(
            "evidence", []):
        if row.get("pmid") is not None:
            pmids.add(str(row["pmid"]))

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
             "Candidate-support evidence confidence (not measured efficacy; "
             "candidate pharmacology records only; legacy fallback: 0.6 × normalized "
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


def _affinity_provenance(candidate: dict[str, Any]) -> str:
    """Name providers behind the best persisted target-qualified affinity."""
    providers: set[str] = set()
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if record.get("qualification_status") not in (None, "", "qualified"):
            continue
        if record.get("source_type") != "bioactivity_assay":
            continue
        if str(record.get("target_evidence_scope") or "target_qualified").casefold() \
                not in {"target_qualified", "direct"}:
            continue
        if str(record.get("measurement_type") or "").casefold() not in {
            "pchembl", "pchembl_equivalent", "pchembl-equivalent",
        }:
            continue
        provider = str(record.get("provider") or "").strip()
        if provider:
            providers.add(provider)
    labels = {
        "bindingdb": "BindingDB",
        "chembl": "ChEMBL",
        "gtopdb": "GtoPdb",
    }
    names = [labels.get(value.casefold(), value) for value in sorted(providers)]
    return ", ".join(names) if names else "source not recorded"


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
    Render the ChEMBL molecule type / route for the evidence table. Pure
    classification, never affects any score. An unresolved lookup is stated
    plainly, never silently rendered as "clear".
    """
    mtype = candidate.get("chembl_molecule_type")
    oral = candidate.get("chembl_oral")
    # Missingness must stay unresolved: an unknown route must never be
    # rendered as a concrete "non-oral" (or vice versa).
    if mtype is None or oral is None:
        return "unresolved (ChEMBL molecule lookup unavailable)"
    route = "oral" if oral else "non-oral"
    return f"{mtype} / {route}"


def _discovery_method_cell(candidate: dict[str, Any]) -> str:
    """How the target was surfaced — never guessed, never blank."""
    method = str(candidate.get("target_discovery_method") or "").strip()
    if not method:
        return ("⚠ unattributed — the provenance of this target was not "
                "recorded; treat the target-disease link as unverified")
    return _display_token(method)


def _ot_association_cell(candidate: dict[str, Any]) -> str:
    """Render OT context using the Reviewer's measurement semantics."""
    components = candidate.get("score_components") or {}
    measured = components.get("normalized_ot_association")
    basis = str(components.get("ot_association_basis") or "").strip()
    raw = candidate.get("ot_association_score")
    if basis == "precedent_stamped_constant":
        return (
            f"{_fmt(raw)} (target-selection ordering value stamped by the "
            "pharmacological-precedent lane; not a measured Open Targets "
            "association and excluded from candidate scoring)"
        )
    if measured is not None:
        return f"{_fmt(measured)} (measured Open Targets target–disease association)"
    return "not observed; excluded from candidate scoring"


def _target_tier_cell(candidate: dict[str, Any]) -> str:
    """Causal-anchor tier plus the rank-demotion disclosure, when it applies."""
    tier = str(candidate.get("target_tier") or "").strip() or "unknown"
    labels = {
        "causal_anchor": "causal anchor (direct disease-target association)",
        "clinical_precedent": (
            "target-level pharmacological precedent (does not establish approval "
            "or clinical efficacy for this disease)"
        ),
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
        return f"{_display_token(basis)} ({', '.join(_display_token(p) for p in providers)})"
    return _display_token(basis)


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
    target = str(candidate.get("target_symbol") or "").upper()
    accession = str(candidate.get("uniprot_id") or "").upper()
    identities = qualified_target_chembl_activity_ids(
        ((candidate.get("_evidence_ledger") or {}).get("records") or []),
        target_symbol=target,
        target_accession=accession,
    )
    if identities:
        return (
            f"Direct assay-backed: {len(identities)} distinct qualified "
            "ChEMBL human target-matched activity record ID(s), counted by "
            "stable activity/source identity; this is not an independent-"
            "publication or independent-experiment count"
        )
    return (
        "No qualified ChEMBL human bioactivity ledger row matched this target; "
        "the dossier does not claim direct target-assay support."
    )


def _efficacy_provenance_cell(candidate: dict[str, Any]) -> str:
    """Describe modalities behind the calibrated evidence value."""
    modalities: set[str] = set()
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if record.get("qualification_status") not in (None, "", "qualified"):
            continue
        role = str(record.get("evidence_role") or "").strip()
        source_type = str(record.get("source_type") or "").strip()
        if not (
            role == "efficacy"
            or (role == "target_link"
                and source_type in ("bioactivity_assay", "mechanism"))
        ):
            continue
        if source_type:
            modalities.add(source_type.replace("_", " "))
    source = (candidate.get("score_components") or {}).get("efficacy_evidence_source")
    assay_backed = "bioactivity assay" in modalities
    if source == "legacy_pchembl_assay_confidence":
        return (
            "assay-backed legacy pChEMBL + assay-confidence calculation "
            f"({_affinity_provenance(candidate)})"
        )
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
        ("Best target-qualified pChEMBL-equivalent affinity",
         f"{_fmt(candidate.get('pchembl_value'), 2)} "
         f"({_affinity_provenance(candidate)})"),
        ("Assay confidence score (0-9)", _fmt(candidate.get("confidence_score"))),
        ("Direct ChEMBL activity basis", _direct_chembl_activity_note(candidate)),
        ("Candidate-support evidence-confidence provenance",
         _efficacy_provenance_cell(candidate)),
        ("Open Targets target-selection / association context",
         _ot_association_cell(candidate)),
        ("Tanimoto to nearest approved drug",
         f"{_fmt(candidate.get('tanimoto_score'), 3)} "
         f"({candidate.get('most_similar_approved_drug') or 'none in set'})"),
        ("Approved / known drug", (
            "⚠ NOT APPROVED FOR THE PROPOSED USE — experimental/unresolved "
            "status. Do not self-administer or use for self-treatment; any "
            "research or clinical activity requires qualified professionals "
            "and applicable institutional/regulatory oversight"
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
        ("ClinicalTrials.gov exact drug+disease trial count",
         ("⚠ query failed (API unreachable) — trial count unavailable; the "
           "ClinicalTrials.gov trial term was excluded from the score as a coverage gap "
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
            "- **Bounded ChEMBL mechanism query:** no qualifying approved-drug "
            "record was returned by the ChEMBL human mechanism endpoint in this "
            "run. This source-specific absence does **not** mean that no approved "
            "drug modulates the target; assay, DrugCentral, GtoPdb, or other "
            "evidence is reported separately."
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
        "should not be treated as measured PK or exposure.",
        f"- **Structure confidence is bounded.** This hypothesis relies on a Boltz "
        f"structure_confidence of {_fmt(sconf)} (complex pLDDT "
        f"{_fmt(plddt_complex)}) and an AFDB apo mean pLDDT of {_fmt(apo_plddt, 1)}; "
        f"the AFDB model contains NO ligand, so the protein-ligand pose is entirely "
        f"a Boltz prediction.",
        "- **Assay-type and provenance caveats.** The target-qualified quantitative "
        "affinity shown above is the best persisted pChEMBL-equivalent value, not "
        f"a median or a publication-level consensus from {_affinity_provenance(candidate)} "
        "records. Multiple database records may represent the same underlying "
        "experiment or publication; assay heterogeneity and the bounded approved-drug "
        "reference set for Tanimoto still apply.",
        "- **Absence of evidence is not evidence of absence.** A zero prior-trial "
        "count or no adverse-event signal may reflect that the pair has simply never "
        "been studied, not that it is safe or untried.",
        "- **This is a repurposing *hypothesis*, not a finding or treatment "
        "recommendation.** The responsible organization determines whether "
        "orthogonal experiments or other validation are appropriate. This report "
        "does not require, authorize, or substitute for wet-lab, translational, "
        "regulatory, or clinical review. Do not use it to self-treat, change "
        "medication, prescribe, or obtain/use an unapproved or off-label product.",
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


def _safety_lane_state(
    lane: dict[str, Any],
    *,
    structured: bool = False,
) -> str:
    """Separate lookup completion from the presence of a safety flag."""
    if not lane:
        return "NOT ASSESSED"
    if lane.get("api_error") or str(lane.get("status") or "").upper() == "ERROR":
        return "ERROR / UNKNOWN"
    verdict = str(lane.get("verdict") or "").upper()
    if verdict == "SKIPPED":
        return "SKIPPED / UNKNOWN"
    if structured and not lane.get("chembl_id"):
        return "UNRESOLVED / UNKNOWN"
    if lane.get("confirmed") or verdict == "YES":
        return "FLAG OBSERVED"
    return "OBSERVED; NO FLAG FOUND"


def _assay_audit_table(candidate: dict[str, Any]) -> str:
    """Render ledger assay summaries without presenting aggregates as raw rows."""
    target_rows = []
    cross_rows = []
    for record in ((candidate.get("_evidence_ledger") or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        if str(record.get("source_type") or "") != "bioactivity_assay":
            continue
        row = (
            _audit_value(record.get("provider")),
            _audit_value(record.get("assay_id") or record.get("source_id")),
            _audit_value(record.get("target_symbol") or record.get("target_accession")),
            _audit_value(record.get("measurement_type")),
            _audit_value(record.get("measurement_value")),
            _audit_value(record.get("measurement_unit")),
            _audit_value(record.get("target_species")),
            _audit_value(record.get("qualification_status")),
        )
        scope = str(
            record.get("target_evidence_scope") or "target_qualified"
        ).casefold()
        (cross_rows if scope in {"cross_target", "off_target"}
         else target_rows).append(row)
    if not target_rows and not cross_rows:
        return ("No ledger bioactivity-assay rows were available. This is an "
                "explicit absence of reportable rows, not evidence of no binding.")
    activity_ids = [
        str(value) for value in candidate.get("source_activity_ids", [])
        if _valid_citation_id(value)
    ]
    lines = [
        "**Target-qualified assay evidence**",
        "",
        "Rows below are the persisted ledger representation. A row may summarize "
        "multiple source activities; it is not a raw assay export.",
        "- Underlying source activity IDs: "
        + (", ".join(activity_ids) if activity_ids else "UNKNOWN"),
        "| Provider | Record / assay ID | Target | Measurement | Value | Unit | Species | Qualification |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in sorted(set(target_rows)):
        # Persisted free-text context/identifiers may contain a pipe; escape it
        # so one malformed provider value cannot corrupt the Markdown table.
        lines.append("| " + " | ".join(
            str(value).replace("|", r"\|").replace("\n", " ")
            for value in row) + " |")
    if not target_rows:
        lines.append("| NONE | — | — | — | — | — | — | — |")
    lines += [
        "",
        "**Cross-target assay records (audit only; never scored)**",
        "",
        "| Provider | Record / assay ID | Other target | Measurement | Value | Unit | Species | Qualification |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for row in sorted(set(cross_rows)):
        lines.append("| " + " | ".join(
            str(value).replace("|", r"\|").replace("\n", " ")
            for value in row) + " |")
    if not cross_rows:
        lines.append("| NONE | — | — | — | — | — | — | — |")
    return "\n".join(lines)


def _filter_approved_target_comparators(
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Remove the lead candidate from target-approved comparison rows."""
    candidate_name = _normalized_compound_alias(candidate.get("drug_name"))
    candidate_names = {
        candidate_name,
        *(_normalized_compound_alias(value)
          for value in candidate.get("compound_aliases", [])),
    }
    candidate_ids = {
        str(value).strip().casefold()
        for value in (
            candidate.get("molecule_chembl_id"),
            candidate.get("parent_chembl_id"),
            *((candidate.get("source_chembl_ids") or [])),
            *((candidate.get("source_molecule_chembl_ids") or [])),
        )
        if value
    }
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if _normalized_compound_alias(row.get("name")) in candidate_names:
            continue
        row_ids = {
            str(value).strip().casefold()
            for value in (
                row.get("molecule_chembl_id"),
                row.get("parent_chembl_id"),
                *((row.get("source_molecule_chembl_ids") or [])),
            )
            if value
        }
        if candidate_ids.intersection(row_ids):
            continue
        filtered.append(row)
    return filtered


def _comparator_table(candidate: dict[str, Any],
                      biologist_output: Optional[dict[str, Any]]) -> str:
    """Render only comparators supplied by the biologist or reviewed payload."""
    contract = candidate.get("dossier_evidence_contract") or {}
    comparators = contract.get("comparators") or {}
    target_drugs = comparators.get("target_approved_drugs")
    if target_drugs is None:
        target_drugs = ((biologist_output or {}).get("druggability_context") or {}).get(
            "approved_drugs", [])
    target_drugs = _filter_approved_target_comparators(
        candidate, target_drugs or [])
    selected = comparators.get("selected_candidates", [])
    lines = ["| Comparator | Source / relationship | Regulatory context |",
             "| --- | --- | --- |"]
    seen: set[str] = set()

    def active_moiety(row: dict[str, Any], name: str) -> tuple[str, str]:
        parent_id = str(
            row.get("parent_chembl_id")
            or row.get("active_moiety_id")
            or ""
        ).strip()
        if parent_id:
            return f"chembl:{parent_id.casefold()}", name
        # Legacy persisted contexts predate parent IDs. This conservative
        # presentation-only fallback collapses common counterion suffixes while
        # retaining the underlying source rows in the evidence ledger.
        base = re.sub(
            r"\s+(?:HYDROCHLORIDE|BENZOATE|BESYLATE|MALEATE|MALATE|"
            r"MESYLATE|CITRATE|FUMARATE|TARTRATE|SUCCINATE|PHOSPHATE|"
            r"SULFATE|NITRATE|HYDRATE)$",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        alias = _normalized_compound_alias(base)
        display = (
            "glibenclamide (glyburide)"
            if alias == "glibenclamide"
            else (base or name)
        )
        return f"name:{alias}", display

    for row in target_drugs or []:
        name = _audit_value(row.get("name"))
        key, display_name = active_moiety(row, name)
        display_key = f"name:{display_name.casefold()}"
        if key in seen or display_key in seen:
            continue
        seen.update((key, display_key))
        lines.append(
            f"| {display_name} | biologist ChEMBL target-approved active moiety | "
            f"max phase {_audit_value(row.get('max_phase'))} |")
    for row in selected or []:
        name = _audit_value(row.get("drug_name"))
        key, display_name = active_moiety(row, name)
        display_key = f"name:{display_name.casefold()}"
        if key in seen or display_key in seen:
            continue
        seen.update((key, display_key))
        lines.append(
            f"| {display_name} | same-target context (not a rank or efficacy comparison) | "
            f"approval basis {_audit_value(row.get('approval_basis'))} |")
    if len(lines) == 2:
        lines.append("| NONE RECORDED | No biologist approved-drug or reviewed-pool comparator was supplied | UNKNOWN |")
    return "\n".join(lines)


def _comparator_rationale(candidate: dict[str, Any]) -> str:
    """State comparator scope without implying rank or comparative efficacy."""
    contract = candidate.get("dossier_evidence_contract") or {}
    comparators = contract.get("comparators") or {}
    selected = comparators.get("selected_candidates") or []
    if not selected:
        return ("Comparator context: no alternative same-target candidate was "
                "persisted; no comparative efficacy or superiority is claimed.")
    return (
        "Comparator context: rows only disclose other compounds observed for the "
        "same target. The table is not a rank, efficacy, safety, or superiority comparison."
    )


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
    comparators["target_approved_drugs"] = _filter_approved_target_comparators(
        candidate,
        (biologist_output.get("druggability_context") or {}).get(
            "approved_drugs", []),
    )


def _readiness_and_context(candidate: dict[str, Any], struct: dict[str, Any]) -> str:
    contract = candidate.get("dossier_evidence_contract") or {}
    readiness = contract.get("scientific_readiness") or {}
    flagship = contract.get("flagship_readiness") or candidate.get(
        "flagship_readiness") or {}
    context = contract.get("disease_mechanism_context") or {}
    cx = (struct or {}).get("complex") or {}
    gates = readiness.get("blocking_gates") or []
    structure_state = ("AVAILABLE (prediction only)"
                       if cx.get("available") is True else
                       readiness.get("structure_prediction", "UNKNOWN"))
    evidence = candidate.get("literature_limitation") or {}
    exact_rows = [
        row for row in evidence.get("evidence", [])
        if row.get("exact_applicability") is True
    ]
    has_support = any(
        str(row.get("exact_use_label") or "").upper() == "APPLICABLE_SUPPORT"
        for row in exact_rows
    )
    has_limitation = any(
        str(row.get("exact_use_label") or "").upper() == "EXPLICIT_LIMITATION"
        for row in exact_rows
    )
    # Keep the persisted contract authoritative when present, but repair legacy
    # snapshots at render time rather than presenting support as clean efficacy
    # evidence when the same exact-use dossier contains a limitation.
    clinical_state = readiness.get("clinical_efficacy_evidence")
    if has_support and has_limitation:
        clinical_state = "MIXED_CONFLICTING"
    lines = [
        "### Evidence-stage verdict and scientific readiness\n",
        f"- **Evidence-stage verdict:** {_display_token(contract.get('evidence_stage_verdict'))}.",
        f"- **Readiness:** {_display_token(readiness.get('status'))}. This is not a clinical efficacy verdict.",
        f"- **Qualified human target-assay evidence:** "
        f"{_display_token(readiness.get('qualified_human_target_assay_evidence', readiness.get('direct_human_assay_evidence')))}. "
        "This establishes target pharmacology only, not rescue of the disease-causing variant.",
        f"- **Mutation-specific evidence:** {_display_token(readiness.get('mutation_specific_evidence'))}.",
        f"- **Disease-model evidence:** {_display_token(readiness.get('disease_model_evidence'))}.",
        f"- **Clinical efficacy evidence:** {_display_token(clinical_state)}.",
        f"- **Structure evidence:** {_display_token(structure_state)}.",
        "- **Automated score-capping flags:** "
        + (", ".join(_display_token(gate) for gate in gates)
           if gates else "None recorded")
        + ". This does not mean experimental or clinical validation is complete.",
         "\n### Flagship hypothesis\n",
        f"- **Flagship hypothesis verdict:** {_display_token(flagship.get('verdict'))}. "
        "This is a computational hypothesis screen, not an efficacy, exposure, "
        "safety, or clinical-benefit verdict.",
        f"- **Flagship next action:** {_audit_value(flagship.get('next_action'))}.",
        "- **Flagship gaps:** " + (
            "; ".join(str(item) for item in
                      (flagship.get("missing_evidence") or []))
            or "None recorded"
        ) + ".",
        *[
            f"- **Flagship finding ({_display_token(code)}):** {_audit_value(reason)}"
            for code, reason in zip(
                flagship.get("reason_codes") or [],
                flagship.get("reasons") or [],
            )
        ],
        "\n### Disease and mechanism context\n",
        f"- Disease: {_audit_value(context.get('disease_name', candidate.get('disease_name')))}; "
        f"target: {_audit_value(context.get('target_symbol', candidate.get('target_symbol')))}.",
        f"- Target discovery: {_display_token(context.get('target_discovery_method', candidate.get('target_discovery_method')))}; "
        f"therapeutic role: {_display_token(context.get('therapeutic_role', candidate.get('therapeutic_role')))}; "
        f"mechanism class: {_display_token(context.get('mechanism_class', candidate.get('mechanism_class')))}.",
        "- Process support: " + (
            ", ".join(str(x) for x in context.get("process_support", []))
            or "UNKNOWN"
        ) + ".",
    ]
    direction = candidate.get("mechanism_direction") or {}
    if direction:
        lines.extend([
            "\n### Target-level directional compatibility audit",
            f"- Verdict: {_display_token(direction.get('verdict'))}; action used: "
            f"{_display_token(direction.get('action_type_used'))}.",
            f"- Reason: {_audit_value(direction.get('reason')).rstrip('.')}.",
            f"- Citations: {_audit_value(direction.get('search_citations'))}.",
            "- Scope: this is a bounded target-level direction screen, not "
            "proof of causal sufficiency, target selectivity, mutation-specific "
            "rescue, disease-model validation, clinical efficacy, or a prescribing "
            "conclusion.",
        ])
    scope = contract.get("timothy_syndrome_cardiac_scope")
    if isinstance(scope, dict):
        lines.extend([
            "\n### Proposed Timothy syndrome mutation-specific cardiac scope",
            f"- Proposed scope: `{_audit_value(scope.get('proposed_variant_scope'))}`, exon "
            f"`{_audit_value(scope.get('proposed_exon_scope'))}`; "
            f"{_audit_value(scope.get('clinical_scope'))}.",
            f"- Genotype confirmation: {_display_token(scope.get('genotype_confirmation_status'))}. "
            "The canonical disease input does not establish TS1 or p.G406R for a specific case.",
            f"- Scope status: {_display_token(scope.get('status'))}; basis: "
            f"{_audit_value(scope.get('scope_basis'))}.",
            "- The scope and tests below are **proposed for explicit human review** "
            "and remain **future, unperformed** activities, not results.",
            "- Potential mutation-specific evidence options: " + "; ".join(
                str(item) for item in scope.get("required_tests", [])) + ".",
            "- Cardiac safety/exposure plan: " + "; ".join(
                str(item) for item in scope.get("safety_exposure_plan", [])) + ".",
        ])
    return "\n".join(lines)


def _literature_limitation_audit(candidate: dict[str, Any]) -> str:
    """Render only the persisted, mechanically checked limitation record."""
    finding = (
        (candidate.get("dossier_evidence_contract") or {}).get(
            "literature_limitation")
        or candidate.get("literature_limitation")
        or {}
    )
    lines = [
        "### Treatment-landscape and literature-limitation gate\n",
        f"- **Verdict:** {_display_token(finding.get('verdict'))}.",
        f"- **Source state:** {_display_token(finding.get('source_status'))}.",
        f"- **Prioritization effect:** "
        f"{'BLOCKED — evidence score preserved; paid validation not authorized' if finding.get('blocked') else 'No automatic literature block recorded'}.",
        f"- **Reason:** {_audit_value(finding.get('reason'))}.",
        f"- **Bounded coverage:** {_audit_value(finding.get('records_screened'))} "
        "PubMed record(s) screened across the persisted query families. A search "
        "with no qualifying limitation is not evidence of novelty, efficacy, or safety.",
    ]
    evidence = finding.get("evidence") or []
    if evidence:
        lines.extend([
            "",
            "| PMID / source | Source type | Year | Classification | Exact quotation |",
            "| --- | --- | ---: | --- | --- |",
        ])
        for row in evidence:
            pmid = _audit_value(row.get("pmid"))
            source = row.get("source_url")
            source_cell = f"[{pmid}]({source})" if source else pmid
            quote = str(row.get("quote") or "").replace("|", "\\|")
            lines.append(
                f"| {source_cell} | "
                f"{_audit_value(', '.join(row.get('publication_types') or []))} | "
                f"{_audit_value(row.get('publication_year'))} | "
                f"{_display_token(row.get('exact_use_label') or row.get('label'))} | “{quote}” |"
            )
    else:
        lines.append(
            "- No mechanically verified quotation was persisted for this state."
        )
    related = (finding.get("related_support") or {}).get("evidence") or []
    if related:
        lines.extend([
            "",
            "**Related support (disclosure only; zero score boost)**",
            "",
            "| PMID / source | Evidence level | Applicability | Exact quotation |",
            "| --- | --- | --- | --- |",
        ])
        for row in related:
            pmid = _audit_value(row.get("pmid"))
            source = row.get("source_url")
            source_cell = f"[{pmid}]({source})" if source else pmid
            quote = str(row.get("quote") or "").replace("|", "\\|")
            lines.append(
                f"| {source_cell} | {_display_token(row.get('evidence_level'))} | "
                "Not applicable to this exact candidate/use | "
                f"“{quote}” |"
            )
    context = finding.get("pharmacology_context") or {}
    if context and context.get("status") != "NOT_REQUESTED":
        lines.extend([
            "",
            "### Generic drug-pharmacology context (disclosure only)",
            f"- **Source state:** {_display_token(context.get('status'))}.",
            f"- **Coverage:** {_audit_value(context.get('records_screened'))} "
            "PubMed record(s) screened for the explicitly framed exposure, "
            "compartment, or selectivity question.",
            f"- **Boundary:** {_audit_value(context.get('reason'))}",
            "- These records are generic drug-level context. They are not exact "
            "disease/use evidence and do not change the score, literature gate, "
            "or paid-validation eligibility.",
        ])
        context_evidence = context.get("evidence") or []
        if context_evidence:
            lines.extend([
                "",
                "| PMID / source | Year | Context | Exact quotation |",
                "| --- | ---: | --- | --- |",
            ])
            for row in context_evidence:
                pmid = _audit_value(row.get("pmid"))
                source = row.get("source_url")
                source_cell = f"[{pmid}]({source})" if source else pmid
                quote = str(row.get("quote") or "").replace("|", "\\|")
                lines.append(
                    f"| {source_cell} | {_audit_value(row.get('publication_year'))} | "
                    "Generic drug-level pharmacology | “"
                    f"{quote}” |"
                )
        else:
            lines.append(
                "- No exact context quotation was extracted; the relevant "
                "exposure/selectivity question remains unresolved."
            )
    lines.append(
        "- **Policy boundary:** this is a post-benchmark production gate. It "
        "does not alter or recompute frozen benchmark results."
    )
    return "\n".join(lines)


def _trial_safety_applicability_audit(candidate: dict[str, Any]) -> str:
    """Report known, negative, and unknown states without triggering lookups."""
    audit = candidate.get("trial_audit") or {}
    trials = audit.get("trials") or []
    lines = ["### Novelty and prior-trial audit\n",
             f"- Trial query state: `{_audit_value(audit.get('query_status'))}`.",
             f"- Negative repurposing result: `{_audit_value(audit.get('negative_repurposing_result'))}`.",
             f"- Exact drug+disease trials returned: "
             f"{_audit_value(audit.get('trial_count'))} "
             "(ClinicalTrials.gov exact drug+disease query).",
             f"- ClinicalTrials.gov exact drug+disease trial count is "
             f"registry-scoped; the separate literature-publication count below "
             f"is broader."]
    literature_count = audit.get("literature_trial_count")
    if literature_count is not None:
        pmids = ", ".join(str(value) for value in audit.get(
            "literature_trial_pmids", [])) or "none recorded"
        lines += [
            f"- Exact-use clinical-trial publications found in the literature gate: "
            f"`{_audit_value(literature_count)}` (PMIDs: {pmids}).",
            f"- Literature trial limitation affecting the score: "
            f"`{_audit_value(audit.get('literature_negative_trial_evidence'))}`.",
            "- The registry count and literature-publication count are separate "
            "observations; a zero ClinicalTrials.gov result is not a global zero "
            "for prior human evidence.",
        ]
    if trials:
        lines += ["| NCT | Title | Status | Results posted | Stop reason | Stop classification |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for trial in trials:
            lines.append(
                f"| {_audit_value(trial.get('nct_id'))} | {_audit_value(trial.get('title'))} | "
                f"{_audit_value(trial.get('status'))} | "
                f"{_audit_value(trial.get('has_results'))} | "
                f"{_audit_value(trial.get('why_stopped'))} | "
                f"{_audit_value(trial.get('why_stopped_classification'))} |")
    else:
        lines.append("- No individual trial rows were returned (this does not establish novelty).")
    l1, l2 = candidate.get("safety_layer1") or {}, candidate.get("safety_layer2") or {}
    availability = candidate.get("availability_gate") or {}
    lines += [
        "\n### Safety and applicability matrix\n",
        "| Domain | Existing evidence | State |",
        "| --- | --- | --- |",
        f"| Regulatory safety | {_audit_value(candidate.get('status_badge'))} | "
        f"{'FLAGGED' if candidate.get('safety_cap_applied') else 'ELIGIBILITY OBSERVED; NO WITHDRAWAL CAP'} |",
        f"| Structured safety lane | {_audit_value(l1.get('disclosure_text'))} | "
        f"{_safety_lane_state(l1, structured=True)} |",
        f"| Independent safety lane | {_audit_value(l2.get('disclosure_text'))} | "
        f"{_safety_lane_state(l2)} |",
        f"| Active-ingredient availability | {_audit_value(availability.get('reason'))} | "
        f"{_display_token(availability.get('status'))} |",
        f"| Target applicability | {_display_token(candidate.get('target_applicability'))} | "
        f"{'ELIGIBLE' if candidate.get('headline_eligible') else 'NOT HEADLINE-ELIGIBLE'} |",
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

**Therapeutic applicability is not scored as a general compatibility term.**
Target applicability is gated separately and is
classified as direct causal, direct disease-associated, cross-target
functionally supported, pathway-only, or unknown. Pathway-only and unknown
rows receive no directional bonus and cannot headline or enter paid validation.
The ranking otherwise does not assess
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
rest of the evidence is: a *safety cap* (authority-confirmed, identity- and
scope-matched safety withdrawal), or a *mechanism-direction cap* (the drug acts on the target in the
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

- **Candidate-support evidence confidence** — a modality-aware score assembled
  from qualified candidate–target assays, curated drug–target mechanisms, and
  candidate-specific label, publication, or trial evidence. Disease–target
  genetics and pathway context are scored or disclosed separately and do not
  inflate this term. It is not a measured probability of efficacy. The table
  states whether direct qualifying ChEMBL activity supports it.
- **pChEMBL-equivalent affinity** — a source-qualified −log10 molar potency
  summary; higher = more potent. The evidence table names whether the persisted
  target-qualified assay records came from ChEMBL, BindingDB, or another source.
  Assay type, species, confidence, and aggregation rules remain source-specific.
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
    # Render from a detached projection: report generation must not rewrite
    # the persisted reviewer handoff supplied by the caller.
    candidate = copy.deepcopy(candidate)
    drug = candidate.get("drug_name", "Unknown drug")
    target = candidate.get("target_symbol", "?")
    disease = candidate.get("disease_name", "?")
    strong = candidate.get("strong_match")
    threshold = formula.get("strong_match_threshold")
    _apply_matched_biologist_context(candidate, biologist_output)
    # The matched-context projection is the final report input. Validate again
    # after that projection so no Writer enrichment can bypass the pre-Boltz
    # dossier contract gate.
    if isinstance(candidate.get("dossier_evidence_contract"), dict):
        validate_dossier_inputs(
            candidate,
            biologist_output,
            target_meta,
            repurposing_only=repurposing_only,
        )

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
    if candidate.get("literature_limitation_blocked"):
        header_note = (
            "> **NOT PRIORITIZABLE — KNOWN LITERATURE LIMITATION.** The "
            "candidate's evidence score is retained for auditability, but an "
            "applicable class/drug limitation met the deterministic blocking "
            "threshold. Paid structure validation and external prioritization "
            "are not authorized for this use.\n\n"
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

    # Target-family cross-reactivity disclosure.  This is intentionally
    # separate from the withdrawal/black-box lane: a related-target mechanism
    # can create a serious pharmacology concern without being a regulator-
    # confirmed withdrawal.  It never changes the score or eligibility.
    family_safety = candidate.get("target_family_safety_liability") or {}
    if family_safety.get("status") == "FLAGGED":
        evidence_rows = family_safety.get("evidence") or []
        mechanism_text = []
        for row in evidence_rows:
            mechanism_text.extend(row.get("mechanisms") or [])
            mechanism_text.extend(row.get("action_types") or [])
        mechanism_text = ", ".join(dict.fromkeys(str(v) for v in mechanism_text))
        evidence_clause = (
            f" Persisted related-target mechanism metadata includes {mechanism_text}."
            if mechanism_text else
            " Persisted related-target mechanism metadata was found."
        )
        parts.append(
            f"> ⚠ **Target-family cross-reactivity caution — disclosure only.** "
            f"**{drug}** has persisted mechanism evidence involving the related "
            f"cardiac-safety target **{family_safety.get('liability_target') or 'KCNH2/hERG'}** "
            f"while the pursued target is **{target}**.{evidence_clause} "
            "This does not establish potency, a selectivity ratio, or clinically "
            "relevant exposure, but it means the proposed target action cannot be "
            "treated as a clean selective mechanism. For KCNH-family hypotheses, "
            "quantitative hERG/QT, PK, dose, and cardiac-safety review is required. "
            "**This flag does not affect the composite score, hard caps, or "
            "headline eligibility.**\n\n"
        )
    elif family_safety.get("status") in {"UNKNOWN", "NOT_FOUND"}:
        lookup_clause = (
            "the related hERG/KCNH2 lookup was unavailable"
            if family_safety.get("status") == "UNKNOWN"
            else
            "no related hERG/KCNH2 mechanism identity was found in the bounded lookup"
        )
        parts.append(
            f"> ⚠ **Target-family selectivity review required — disclosure only.** "
            f"The pursued target **{target}** is in the KCNH family, and "
            f"{lookup_clause}. This does not establish selectivity or safety: "
            "hERG/KCNH2 QT liability, quantitative cross-reactivity, PK, dose, "
            "and cardiac monitoring requirements must be checked before treating "
            "the target action as a viable therapeutic mechanism. "
            "**No score change was applied.**\n\n"
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
    # Threshold of 5 is Lipinski's Rule of Five (Lipinski, Lombardo, Dominy &
    # Feeney, 1997, Adv. Drug Deliv. Rev. 23:3-25): LogP > 5 is one of four
    # criteria historically associated with poor oral absorption/permeability.
    # This banner is disclosure only — it does NOT affect any score.
    if candidate.get("high_lipophilicity_flag"):
        _xlogp_val = candidate.get("pubchem_xlogp")
        _xlogp_str = f"{_xlogp_val:.2f}" if _xlogp_val is not None else "≥ 5"
        parts.append(
            f"> ⚠ **High lipophilicity (XLogP = {_xlogp_str}) — disclosure flag.** "
            f"**{drug}** has a PubChem XLogP of {_xlogp_str}, above the threshold of 5. "
            f"This crosses one of the four criteria in **Lipinski's Rule of Five** "
            f"(Lipinski, Lombardo, Dominy & Feeney, 1997, *Advanced Drug Delivery "
            f"Reviews* 23:3–25), a widely used medicinal-chemistry heuristic under "
            f"which LogP > 5 is associated with poor oral absorption and permeability. "
            f"**This flag does not affect the composite score.** "
            f"The reviewer must judge whether lipophilicity is a material concern in the "
            f"context of the proposed repurposing indication and its intended route of "
            f"administration.\n\n"
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
        f"{_direct_chembl_activity_note(candidate).rstrip('.')}. Open Targets context: "
        f"{_ot_association_cell(candidate)}. "
        f"Tanimoto similarity is {_fmt(candidate.get('tanimoto_score'), 3)} to "
        f"{candidate.get('most_similar_approved_drug') or 'no approved analog in the set'}. "
        f"Target network context (BioGRID, physical/genetic — not mechanism): {net_str}. "
        f"The resulting composite score is "
        f"{_fmt(candidate.get('composite_score'), 4)}.\n"
    )
    # Free-form Chemist prose historically restated structured facts from a
    # different target/context and contradicted the canonical evidence tables.
    # The dossier renders only persisted, typed evidence below.

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
                f"exist (see Target druggability context). These are different "
                f"questions: target-level pharmacological precedent does not "
                f"establish an approved therapy for this disease, while absence of "
                f"a therapy link on the disease EFO record does not establish that "
                f"no manifestation-directed treatment exists. The unmet_need_score "
                f"reflects the bounded disease-level Open Targets linkage only.\n"
            )

    # 2. Evidence table
    parts.append("\n## 2. Evidence table\n")
    parts.append(_evidence_table(candidate, struct) + "\n")
    parts.append("\n" + _readiness_and_context(candidate, struct) + "\n")
    parts.append("\n" + _literature_limitation_audit(candidate) + "\n")
    parts.append("\n### Assay evidence audit\n")
    parts.append(_assay_audit_table(candidate) + "\n")
    parts.append("\n### Comparator table\n")
    parts.append(_comparator_table(candidate, biologist_output) + "\n")
    parts.append(_comparator_rationale(candidate) + "\n")

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
               target_for_candidate: Optional[Any] = None,
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
        cand_target = target
        if target_for_candidate is not None:
            resolved_target = target_for_candidate(cand)
            if resolved_target is not None:
                cand_target = resolved_target
        md = build_report_markdown(cand, struct, formula, cand_bio, cand_target,
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
