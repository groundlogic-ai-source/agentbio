"""Can the drug physically reach the tissue where the disease does its damage?

WHY THIS EXISTS
---------------
The pipeline scored target ENGAGEMENT -- potency, assay confidence, genetic
association -- and nothing about DISTRIBUTION. A drug that binds the right
protein at single-digit nanomolar but never reaches the diseased organ ranked
exactly as well as one that does.

A Niemann-Pick type C run demonstrated the cost. ELIGLUSTAT came top of a
397-row pool: approved drug, pChEMBL 7.8 against UGCG, the validated
non-causal target, no prior art, every gate clear. Eliglustat is also actively
effluxed from the brain by P-glycoprotein, which is why it is approved for
Gaucher disease type 1 and NOT for the neuronopathic type 3. NPC is a
neurodegenerative disease; what kills children at a median age of 13 is
neurological. Miglustat -- a far WEAKER inhibitor of the same enzyme -- is the
one used in NPC precisely because it crosses the blood-brain barrier.

WHY PHYSICOCHEMISTRY IS NOT ENOUGH (checked, not assumed)
----------------------------------------------------------
The obvious implementation is a descriptor rule: MW < 450, TPSA < 90, HBD <= 3,
logP 2-5. Eliglustat's measured descriptors are MW 404.55, TPSA 71.03, HBD 2,
logP 3.43 -- it passes every one of those thresholds. Descriptors model PASSIVE
permeability; eliglustat's barrier is ACTIVE efflux, which no descriptor sees.
A descriptor gate would have waved through the exact candidate that motivated
building this.

So the empirical signal is what counts: documented efflux-transporter substrate
status, and documented presence or absence of clinical benefit in the relevant
compartment. Descriptors are carried as disclosure only, never as the verdict.

WHAT THIS DOES NOT DO
---------------------
This answers one bounded question: is there documented evidence the drug fails
to reach the required compartment? It does not establish dose, route,
pharmacokinetics, therapeutic window, disease stage, or that a drug which DOES
reach the compartment arrives at an effective and tolerable concentration. The
dossier's therapeutic-applicability disclosure stands; this narrows one clause
of it and replaces none of it.

Fail-open by design: INSUFFICIENT_INFO does not block. The pipeline already
fails closed across ~12 providers, and adding another whole-run failure mode
costs more than it buys. Only a documented exposure failure is actionable.
"""

import os
import re
from typing import Any, Optional

from openai import OpenAI
from cache.cache import get, set as cache_set, make_key
from data_sources import holdout
from data_sources.llm_failover import call_with_backoff

SCHEMA_VERSION = "tissue-exposure-v1"

VERDICT_UNLIKELY = "COMPARTMENT_EXPOSURE_UNLIKELY"
VERDICT_PLAUSIBLE = "COMPARTMENT_EXPOSURE_PLAUSIBLE"
VERDICT_INSUFFICIENT = "INSUFFICIENT_INFO"
VERDICT_NOT_REQUIRED = "NO_COMPARTMENT_REQUIREMENT_IDENTIFIED"
VERDICT_NOT_ASSESSED = "NOT_ASSESSED"

COMPARTMENT_CNS = "central nervous system"

_CACHE_VERSION = "tissue_exposure_v1"
_TTL_DAYS = 30
_AI_TIMEOUT_SECONDS = 60.0
_AI_MAX_RETRIES = 0

#: Disease-name markers implying the therapeutic target sits behind the
#: blood-brain barrier. Deliberately conservative: a false "CNS required" only
#: triggers a disclosure, while a missed one returns the pipeline to its prior
#: behaviour of not checking at all.
_CNS_DISEASE_MARKERS = re.compile(
    r"\b(neuro\w*|neurodegenerat\w*|encephalo\w*|leukodystroph\w*|leukoencephalo\w*|"
    r"ataxi\w*|dementia|epilep\w*|seizure\w*|myoclon\w*|dystoni\w*|"
    r"parkinson\w*|huntington\w*|alzheimer\w*|cerebell\w*|cerebral|brain|"
    r"cognitive|psychomotor|spastic\w*|paraplegi\w*|polyneuropath\w*|"
    r"motor neuron|white matter|meningeal|intellectual disability)\b",
    re.IGNORECASE,
)

#: Lysosomal and metabolic diseases whose neuronopathic subtypes are the lethal
#: ones. The disease name alone often omits any neuro- marker ("Niemann-Pick
#: disease type C" contains none), so the name test would miss exactly the
#: class this gate was built for.
_NEURONOPATHIC_DISEASE_MARKERS = re.compile(
    r"\b(niemann[- ]pick|gaucher|krabbe|tay[- ]sachs|sandhoff|"
    r"metachromatic|mucopolysaccharidos\w*|sanfilippo|hunter syndrome|"
    r"hurler|batten|neuronal ceroid|pompe|fabry|"
    r"gm1 gangliosidos\w*|gm2 gangliosidos\w*|canavan|alexander disease)\b",
    re.IGNORECASE,
)

_NO_INFO_TEXT = (
    "No sufficient retrieved evidence on compartment exposure; this is unknown, "
    "not a finding that the drug does or does not reach the target tissue."
)


def requires_cns_exposure(disease_name: str) -> bool:
    """Whether treating this disease plausibly requires reaching the CNS.

    Two tests, because either alone misses real cases. "Niemann-Pick disease
    type C" contains no neurological word at all, yet its lethal course is
    neurodegenerative -- so a name-marker test alone would skip the disease
    this module exists for.
    """
    name = str(disease_name or "")
    return bool(
        _CNS_DISEASE_MARKERS.search(name)
        or _NEURONOPATHIC_DISEASE_MARKERS.search(name)
    )


def descriptor_disclosure(descriptors: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Physicochemical CNS-likeness, recorded as context and never as verdict.

    Reported so a reader can see what the descriptors claim AND how far that
    claim can be trusted. Eliglustat satisfies every threshold below and still
    does not reach the brain, because these model passive permeability and its
    barrier is active efflux.
    """
    d = descriptors or {}
    mw = d.get("molecular_weight")
    tpsa = d.get("tpsa")
    hbd = d.get("h_bond_donors")
    logp = d.get("logp")
    known = [v for v in (mw, tpsa, hbd, logp) if v is not None]
    if not known:
        return {
            "assessed": False,
            "note": "No descriptors available for physicochemical disclosure.",
        }
    passes = (
        (mw is None or mw < 450)
        and (tpsa is None or tpsa < 90)
        and (hbd is None or hbd <= 3)
        and (logp is None or 1 <= logp <= 5)
    )
    return {
        "assessed": True,
        "molecular_weight": mw,
        "tpsa": tpsa,
        "h_bond_donors": hbd,
        "logp": logp,
        "passive_cns_ruleset_passed": passes,
        "note": (
            "Passive-permeability heuristics only. They cannot detect active "
            "efflux: eliglustat passes every threshold here and is still "
            "pumped out of the CNS by P-glycoprotein. This is disclosure, not "
            "a verdict."
        ),
    }


VERDICT_NO_SYSTEMIC_ROUTE = "NO_SYSTEMIC_ROUTE_OF_ADMINISTRATION"


def systemic_route_check(candidate: dict[str, Any]) -> dict[str, Any]:
    """Can this drug's approved formulation reach the body at all?

    The compartment check above asks whether a drug crosses the blood-brain
    barrier. It answers nothing for a disease outside the CNS, and that gap
    produced a false headline: a Duchenne muscular dystrophy run promoted
    FLUTICASONE PROPIONATE at pChEMBL 10.40 (~40 pM against NR3C1, assay
    confidence 9, direction compatible, no prior art). Fluticasone propionate
    has ~1% oral bioavailability by design -- near-complete hepatic first-pass
    metabolism is the point of the molecule, which exists for local airway
    action. DMD needs chronic SYSTEMIC glucocorticoid exposure to skeletal and
    cardiac muscle. All four promoted candidates were inhaled or topical
    steroids.

    The signal is deterministic and free: ChEMBL carries route flags, and they
    separate the false positives from the real drugs cleanly.

        fluticasone propionate  oral=False topical=False parenteral=False
        halcinonide             oral=False topical=False parenteral=False
        prednisolone            oral=TRUE     <- DMD standard of care
        deflazacort             oral=TRUE     <- FDA-approved for DMD

    A drug with no systemic route cannot treat a systemic disease. Unknown
    flags are NOT a finding: absent data clears nothing, it only means the
    check could not be made.
    """
    oral = candidate.get("route_oral")
    parenteral = candidate.get("route_parenteral")
    topical = candidate.get("route_topical")
    known = [v for v in (oral, parenteral, topical) if v is not None]
    if not known:
        return {
            "assessed": False,
            "systemic_route": None,
            "reason": ("ChEMBL route-of-administration flags were unavailable, "
                       "so systemic exposure could not be checked. This is "
                       "unknown, not a clear result."),
        }
    systemic = bool(oral) or bool(parenteral)
    return {
        "assessed": True,
        "systemic_route": systemic,
        "route_oral": oral,
        "route_parenteral": parenteral,
        "route_topical": topical,
        "reason": (
            "ChEMBL records no oral or parenteral route for this drug, so its "
            "approved formulation acts locally and cannot deliver systemic "
            "exposure. Target potency does not substitute for reaching the "
            "tissue."
            if not systemic else
            "ChEMBL records a systemic route (oral or parenteral). This "
            "establishes that a systemic formulation exists, not that it "
            "achieves a therapeutic concentration at the diseased tissue."
        ),
    }


def _openai_client() -> OpenAI | None:
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=_AI_TIMEOUT_SECONDS,
        max_retries=_AI_MAX_RETRIES,
    )


def parse_verdict(text: str) -> tuple[str, str]:
    """Pull (verdict, reason) out of the classifier's reply.

    Anything unparseable is INSUFFICIENT_INFO. An unreadable answer is unknown,
    never a finding in either direction.
    """
    raw = str(text or "")
    verdict = VERDICT_INSUFFICIENT
    match = re.search(
        r"VERDICT:\s*(COMPARTMENT_EXPOSURE_UNLIKELY|"
        r"COMPARTMENT_EXPOSURE_PLAUSIBLE|INSUFFICIENT_INFO)",
        raw, re.IGNORECASE)
    if match:
        verdict = match.group(1).upper()
    reason_match = re.search(r"REASON:\s*(.+?)(?:\nCITATIONS:|\Z)", raw,
                             re.IGNORECASE | re.DOTALL)
    reason = reason_match.group(1).strip() if reason_match else _NO_INFO_TEXT
    return verdict, reason


def _envelope(verdict: str, reason: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "verdict": verdict,
        "compartment": "",
        "compartment_required": False,
        # True ONLY for a documented exposure failure. Unknown is not a finding.
        "exposure_unlikely": verdict == VERDICT_UNLIKELY,
        "reason": reason,
        "descriptor_disclosure": {},
        "search_citations": "",
        "model_used": "gpt-5.4",
        "raw": "",
    }
    payload.update(extra)
    return payload


def check_tissue_exposure(
    drug_name: str,
    disease_name: str,
    *,
    descriptors: Optional[dict[str, Any]] = None,
    candidate_chembl_ids: Optional[list[str]] = None,
    candidate_inchikey: Optional[str] = None,
) -> dict[str, Any]:
    """Ask whether the drug reaches the compartment the disease occupies.

    Returns an envelope whose ``exposure_unlikely`` is True only when retrieved
    evidence documents that the drug fails to reach the required compartment.
    INSUFFICIENT_INFO and NO_COMPARTMENT_REQUIREMENT_IDENTIFIED never block.
    """
    disclosure = descriptor_disclosure(descriptors)

    if holdout.is_active():
        return _envelope(
            VERDICT_NOT_ASSESSED,
            "Holdout is active; compartment-exposure retrieval is not run "
            "under frozen study semantics.",
            descriptor_disclosure=disclosure)

    if not requires_cns_exposure(disease_name):
        return _envelope(
            VERDICT_NOT_REQUIRED,
            "No central-nervous-system involvement was identified from the "
            "disease name, so no blood-brain-barrier requirement is asserted. "
            "Other compartment requirements are not assessed.",
            descriptor_disclosure=disclosure)

    if not str(drug_name or "").strip():
        return _envelope(VERDICT_NOT_ASSESSED, "No drug name supplied.",
                         descriptor_disclosure=disclosure)

    heldout_mode = holdout.is_active() and (
        holdout.matches_name(drug_name)
        or any(holdout.matches_molecule(m)
               for m in (candidate_chembl_ids or []) if m)
        or holdout.matches_inchikey(candidate_inchikey)
    )
    cache_key = make_key(
        _CACHE_VERSION,
        "heldout_candidate" if heldout_mode else drug_name,
        disease_name, COMPARTMENT_CNS,
    )
    cached = get(cache_key)
    if cached is not None:
        return cached

    client = _openai_client()
    if not client:
        # Deliberately not cached: a missing key is a configuration state, not
        # a finding about the drug.
        return _envelope(
            VERDICT_INSUFFICIENT,
            "Compartment-exposure check skipped — OPENAI_API_KEY not "
            "configured. " + _NO_INFO_TEXT,
            compartment=COMPARTMENT_CNS, compartment_required=True,
            descriptor_disclosure=disclosure)

    try:
        query = (
            f"Does the drug {drug_name!r} achieve therapeutically meaningful "
            f"exposure in the central nervous system in humans?\n\n"
            f"This matters because {disease_name!r} has a neurological "
            f"component, so a drug that cannot cross the blood-brain barrier "
            f"cannot address it regardless of target potency.\n\n"
            f"Address specifically:\n"
            f"1. EFFLUX: Is {drug_name!r} a documented substrate of "
            f"P-glycoprotein (ABCB1/MDR1) or BCRP? Active efflux can exclude a "
            f"drug from the brain even when its physicochemical properties "
            f"predict good passive permeability.\n"
            f"2. CLINICAL EVIDENCE: Is there published evidence that "
            f"{drug_name!r} does or does not produce neurological benefit? "
            f"Note especially any indication restricted to non-neuronopathic "
            f"forms of a disease whose neuronopathic forms exist.\n"
            f"3. MEASURED EXPOSURE: Any reported CSF or brain concentrations, "
            f"or brain-to-plasma ratios.\n\n"
            f"Cite sources."
        )
        search_response = call_with_backoff(
            lambda: client.responses.create(
                model="gpt-5.4",
                tools=[{"type": "web_search_preview"}],
                input=query,
            ),
            label="tissue-exposure-search",
            provider="openai",
            model="gpt-5.4",
        )
        search_text = (search_response.output_text or "").strip()

        classify = (
            "Classify CNS exposure for the drug below using ONLY the retrieved "
            "text.\n\n"
            f"DRUG: {drug_name}\nDISEASE: {disease_name}\n\n"
            f"RETRIEVED TEXT:\n{search_text}\n\n"
            "Reply in exactly this format:\n"
            "VERDICT: <COMPARTMENT_EXPOSURE_UNLIKELY|"
            "COMPARTMENT_EXPOSURE_PLAUSIBLE|INSUFFICIENT_INFO>\n"
            "REASON: <one sentence, grounded in the retrieved text>\n"
            "CITATIONS: <urls>\n\n"
            "Rules:\n"
            "- COMPARTMENT_EXPOSURE_UNLIKELY requires the retrieved text to "
            "state documented efflux, absent CNS benefit, or an indication "
            "restricted to non-neuronopathic disease. Inference from "
            "physicochemical properties alone is NOT sufficient.\n"
            "- COMPARTMENT_EXPOSURE_PLAUSIBLE requires positive evidence of "
            "CNS exposure or neurological benefit.\n"
            "- Anything else is INSUFFICIENT_INFO. Unknown is not a finding."
        )
        classify_response = call_with_backoff(
            lambda: client.responses.create(model="gpt-5.4", input=classify),
            label="tissue-exposure-classify",
            provider="openai",
            model="gpt-5.4",
        )
        raw = (classify_response.output_text or "").strip()
        verdict, reason = parse_verdict(raw)

        result = _envelope(
            verdict, reason,
            compartment=COMPARTMENT_CNS,
            compartment_required=True,
            descriptor_disclosure=disclosure,
            search_citations=search_text[-2000:],
            raw=raw,
        )
        cache_set(cache_key, result, ttl_days=_TTL_DAYS)
        return result

    except Exception as e:  # noqa: BLE001 — fail-open by design
        print(f"[tissue_exposure] WARNING: check failed for "
              f"'{drug_name}'/'{disease_name}': {e}")
        return _envelope(
            VERDICT_INSUFFICIENT,
            f"Compartment-exposure retrieval failed: {e}. " + _NO_INFO_TEXT,
            compartment=COMPARTMENT_CNS, compartment_required=True,
            descriptor_disclosure=disclosure)
