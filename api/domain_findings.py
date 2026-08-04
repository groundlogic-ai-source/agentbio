"""
Domain findings: confirmed results from the bisociation research module,
applied to Audit mode as base-rate context for matching indications.

A finding is eligible only if it passed discovery FDR *and* independent
holdout confirmation in the hypothesis registry. Findings are disclosure-only:
they never adjust scores, ranks, caps, or verdicts — they tell the reviewer
what the historical base rate says about the indication class being audited,
with the confound-survival record attached so the claim stays honest.

Indication matching is deliberately keyword-based, deterministic, and
conservative: a disease name that contains no oncology term returns no
finding (e.g. MEN2A is not flagged even though it is cancer-predisposing).
The matched term is always returned so the reviewer can see exactly why the
finding was attached.

First entry: the oncology repurposing penalty (registry hypothesis
run-518207db-H26, verified against the live registry 2026-08-04).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional

# Word-anchored keyword matcher. "malignan" intentionally lacks a trailing
# boundary so it covers malignant/malignancy; every other term is anchored on
# BOTH sides so inflections of longer words can't false-positive
# (e.g. "tumorous", "carcinomatosis", "sarcoidosis" must not match).
_ONCOLOGY_STEM_TERMS = ("malignan",)
_ONCOLOGY_FULL_TERMS = (
    "cancer", "carcinoma", "adenocarcinoma", "sarcoma", "myeloma",
    "leukemia", "leukaemia", "lymphoma", "melanoma", "glioma",
    "glioblastoma", "astrocytoma", "neuroblastoma", "medulloblastoma",
    "mesothelioma", "meningioma", "blastoma", "neoplasm",
    "tumor", "tumour",
)
_ONCOLOGY_RE = re.compile(
    r"\b("
    + "|".join(_ONCOLOGY_STEM_TERMS)
    + "|"
    + "|".join(t + r"\b" for t in _ONCOLOGY_FULL_TERMS)
    + r")",
    re.IGNORECASE,
)

ONCOLOGY_REPURPOSING_PENALTY: dict[str, Any] = {
    "id": "oncology_repurposing_penalty",
    "domain": "oncology",
    "severity": "caution",
    "title": "Oncology indications have lower repurposing success",
    "statement": (
        "Oncology indications have lower repurposing success than "
        "non-oncology indications."
    ),
    "stats": {
        "framing": "narrow (genuine-outcome controls only)",
        "test": "fisher_exact",
        "odds_ratio": 0.0724,
        "ci95": [0.0399, 0.13],
        "n": 2913,
        "discovery_fdr_q": 3.61e-12,
        "confirmation_raw_p": 5.33e-14,
    },
    "confounds": [
        {
            "name": "prior repurposing saturation of oncology drugs",
            "status": "survives",
            "detail": (
                "Adjusted OR 0.119 [0.0632, 0.2226], adj. p below display "
                "precision — the penalty persists after accounting for how "
                "often oncology drugs have already been attempted."
            ),
        },
        {
            "name": "novel-versus-established product status",
            "status": "survives",
            "detail": "Adjusted OR 0.0763 [0.0419, 0.1391].",
        },
        {
            "name": "oncology trial phase-mix skew",
            "status": "not_testable",
            "detail": (
                "No per-program phase-mix feature exists in the dataset, so "
                "this candidate explanation could not be ruled out."
            ),
        },
    ],
    "cautions": [
        (
            "Under broad outcome framing the same association is "
            "LABEL_ARTIFACT_SUSPECT: an admin-only replay reproduces it "
            "(OR 0.028 [0.0228, 0.035], n=4203), meaning the broad effect "
            "lives in the administrative-exclude class. Only the narrow, "
            "genuine-outcome framing above should be cited."
        ),
    ],
    "implication": (
        "The historical odds of repurposing success for oncology indications "
        "are roughly 14x lower than for non-oncology. Treat within-case "
        "ranks as relative to each other — even a top-ranked oncology "
        "candidate carries a higher external-validation burden than an "
        "equivalent candidate in a non-oncology indication."
    ),
    "provenance": {
        "hypothesis_id": "run-518207db-H26",
        "run_id": "run-518207db",
        "source_domain": (
            "Kessler-syndrome collision cascades in crowded orbital shells"
        ),
        "proposing_llm": "Sol",
        "registry": (
            "Bisociation hypothesis registry — passed discovery FDR "
            "(q=3.6e-12) and independent holdout confirmation (p=5.3e-14)"
        ),
    },
}


MODALITY_REPURPOSING_PENALTY: dict[str, Any] = {
    "id": "modality_repurposing_penalty",
    "domain": "modality",
    "severity": "caution",
    "title": "Non-oral biologics have lower repurposing success",
    "statement": (
        "Non-oral, non-small-molecule (biologic) agents have lower "
        "repurposing success than oral small molecules."
    ),
    "stats": {
        "framing": "narrow (genuine-outcome controls only)",
        "test": "fisher_exact",
        "odds_ratio": 0.3004,
        "ci95": [0.121, 0.657],
        "n": 2642,
        "discovery_raw_p": 1.49e-2,
        "discovery_fdr_q": 4.69e-2,
        "confirmation_raw_p": 2.80e-4,
    },
    "confounds": [
        {
            "name": "established-product maturity",
            "status": "survives",
            "detail": (
                "Adjusted OR 0.3154 [0.1314, 0.7572], adj. p=9.8e-3 — the "
                "penalty persists after accounting for product tenure."
            ),
        },
        {
            "name": "prior repurposing base rate",
            "status": "not_testable",
            "detail": (
                "Perfect separation in the 2x2 — this candidate explanation "
                "could not be ruled out."
            ),
        },
        {
            "name": "oncology late-stage phase mix",
            "status": "not_testable",
            "detail": (
                "Perfect separation in the 2x2 — this candidate explanation "
                "could not be ruled out."
            ),
        },
    ],
    "cautions": [
        (
            "Under broad outcome framing the same association is "
            "LABEL_ARTIFACT_SUSPECT: an admin-only replay reproduces it "
            "(admin OR 0.174, p=7.8e-49; broad OR 0.177 [0.139, 0.227], "
            "n=3843), meaning the broad effect lives in the administrative-"
            "exclude class. Only the narrow, genuine-outcome framing above "
            "should be cited."
        ),
    ],
    "implication": (
        "The historical odds of repurposing success for non-oral biologics "
        "are roughly 3x lower than for other agents. Treat within-case ranks "
        "as relative to each other — even a top-ranked biologic candidate "
        "carries a higher external-validation burden than an equivalent oral "
        "small molecule."
    ),
    "provenance": {
        "hypothesis_id": "run-704c0cb4-H05",
        "run_id": "run-704c0cb4",
        "source_domain": (
            "wildfire fuel-load accumulation and prescribed-burn suppression "
            "cycles"
        ),
        "proposing_llm": "Opus",
        "registry": (
            "Bisociation hypothesis registry — passed discovery FDR "
            "(q=4.7e-2) and independent holdout confirmation (p=2.8e-4)"
        ),
    },
    # Full research provenance, surfaced in-product so the caution can be
    # audited rather than taken on trust.
    "methodology": {
        "dataset": (
            "repoDB historical repurposing outcomes, enriched with ChEMBL "
            "molecule attributes"
        ),
        "records_tested": 2642,
        "base_rate": (
            "713 of 8,374 modality-resolved dataset rows (8.5%; 175 distinct "
            "drugs) are non-oral biologics"
        ),
        "predicate": (
            "NOT small molecule AND NOT orally administered — evaluated from "
            "ChEMBL molecule_type and the oral route flag"
        ),
        "generated_by": (
            "Claude Opus, from the bisociation source domain “wildfire "
            "fuel-load accumulation and prescribed-burn suppression cycles”"
        ),
        "analogy": (
            "Fire spreads through cheaply connected fuel; fuel that cannot "
            "ignite merely accumulates. Oral small molecules spread across "
            "indications at low cost, while formulation-locked biologics act "
            "as a firebreak against opportunistic redeployment."
        ),
        "analogy_status": (
            "The source-domain metaphor only determined which question was "
            "asked. It carries no evidentiary weight — only the literal "
            "predicate above was tested."
        ),
        "steps": [
            "Predicate evaluated per drug-indication pair and crossed with "
            "the recorded repurposing outcome in a 2x2 table (n=2,642).",
            "Fisher's exact test on the discovery split: raw p=1.49e-2.",
            "Benjamini-Hochberg FDR applied across the entire cumulative "
            "hypothesis log — every previously failed test counts against "
            "it — giving q=4.69e-2.",
            "Independent confirmation on a holdout split untouched during "
            "discovery: p=2.80e-4.",
            "Three candidate confounds were pre-registered by the model "
            "before adjustment; one was testable and the effect survived it, "
            "two hit perfect separation and remain unruled-out.",
        ],
        "prior_art": (
            "Not a novel claim to the field. Published analyses of "
            "repurposing databases report the same directional pattern "
            "(RepurposeDB is ~74% small molecules vs ~16% protein/biotech "
            "drugs), and the delivery constraints of biologics are well "
            "documented. The value here is internal calibration — a blind "
            "statistical pipeline recovered a known field-level pattern — "
            "not new knowledge. Cite it as a base rate, never as a discovery."
        ),
        "prior_art_citation": {
            "label": (
                "Shameer et al., Systematic analyses of drugs and disease "
                "indications in RepurposeDB, Briefings in Bioinformatics"
            ),
            "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC6192146/",
        },
    },
}


def oncology_match(disease_name: str) -> Optional[str]:
    """Return the matched oncology term in the disease name, else None."""
    if not disease_name:
        return None
    m = _ONCOLOGY_RE.search(disease_name)
    return m.group(1) if m else None


def modality_match(
    molecule_type: Optional[str], oral: Optional[bool]
) -> Optional[str]:
    """Return the matched molecule_type when the drug is a non-oral biologic.

    Mirrors the tested feature_spec all_of[NOT is_small_molecule, NOT is_oral]:
    missingness propagates — an unknown molecule_type or unknown oral flag means
    no match (the caller surfaces 'unresolved', never a silent False).
    """
    if molecule_type is None or oral is None:
        return None
    if molecule_type != "Small molecule" and not oral:
        return molecule_type
    return None


def modality_finding_for(
    molecule_type: Optional[str], oral: Optional[bool]
) -> list[dict[str, Any]]:
    """Confirmed modality finding applicable to this drug, or []."""
    matched = modality_match(molecule_type, oral)
    if matched is None:
        return []
    finding = copy.deepcopy(MODALITY_REPURPOSING_PENALTY)
    finding["matched_term"] = matched
    return [finding]


def domain_findings_for(disease_name: str) -> list[dict[str, Any]]:
    """Confirmed research findings applicable to this indication.

    Returns a list of finding payloads (currently zero or one). Each payload
    is a fresh copy annotated with the matched term, so callers may mutate
    or serialise it freely.
    """
    term = oncology_match(disease_name)
    if term is None:
        return []
    # Deep copy: nested stats/confounds/cautions/provenance dicts must never
    # be shared references, or a downstream mutation would poison the
    # module-level constant for every future request.
    finding = copy.deepcopy(ONCOLOGY_REPURPOSING_PENALTY)
    finding["matched_term"] = term
    finding["disease_name"] = disease_name
    return [finding]
