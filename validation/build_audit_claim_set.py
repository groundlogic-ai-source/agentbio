"""Build the frozen audit claim set v1 — implements
validation/audit_claimset_construction_protocol.md exactly.

INDEPENDENCE BOUNDARY (protocol §4, Amendment 2):
  * This script NEVER imports api.audit, api.audit_context, the N1–N4
    detectors, or the audit source lanes (data_sources.openfda,
    data_sources.pubtator_assertions). Ground truth is read from RAW external
    artifacts (openFDA label JSON, ChEMBL API records, Europe PMC metadata)
    and committed offline datasets (repoDB enriched CSV, DrugCentral 2023
    snapshot). Pipeline parse behavior is never inspected here.
  * Persisted candidate pools are read ONLY for reachability (is the drug
    auditable against a real case) — pool flags are never claim ground truth.

Deterministic: fixed candidate lists, fixed sampling seed, fixed quotas.
Aborts entirely if any live source is unhealthy at construction time (never
build a claim set during an outage — see harness-outage-poisoning lesson).

Usage:
    python3 -m validation.build_audit_claim_set
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CUTOFF = "2026-08-10"
SEED = 20260810
OUT_JSON = "validation/audit_claim_set_v1.json"
OUT_LOG = "validation/audit_claimset_construction_log.md"

DATASET = "data_prep/output/enriched_dataset.csv"
DRUGCENTRAL = "data_sources/drugcentral_2023_snapshot.sqlite"

POOLS = {
    "2de0698b458b4be28218830a3dad4710": "Multiple myeloma",           # top target NR3C1
    "61f542324d214a869b324fe41060bebb": "Multiple endocrine neoplasia type 2A",  # RET
    "cddaa8e1fbe84309854e7dc6cdd8a71a": "Autosomal recessive hereditary chronic pancreatitis",  # ADRB2
}
POOL_TARGETS = {
    "2de0698b458b4be28218830a3dad4710": "NR3C1",
    "61f542324d214a869b324fe41060bebb": "RET",
    "cddaa8e1fbe84309854e7dc6cdd8a71a": "ADRB2",
}

LOCAL_ROUTES = {
    "topical", "ophthalmic", "otic", "intra-articular", "intralesional",
    "intradermal", "vaginal", "rectal", "nasal",
}

# Fixed candidate lists (declared in the construction protocol).
N1_CANDIDATES = [
    ("Sulfamethoxazole and trimethoprim", "Bacterial infections"),
    ("Amoxicillin and clavulanic acid", "Bacterial infections"),
    ("Ledipasvir and sofosbuvir", "Hepatitis C"),
    ("Sofosbuvir and velpatasvir", "Hepatitis C"),
    ("Fluticasone propionate and salmeterol", "Asthma"),
    ("Budesonide and formoterol", "Asthma"),
    ("Ezetimibe and simvastatin", "Hypercholesterolemia"),
    ("Abacavir and lamivudine", "HIV-1 infection"),
    ("Emtricitabine and tenofovir disoproxil fumarate", "HIV-1 infection"),
    ("Losartan and hydrochlorothiazide", "Hypertension"),
]

N4_CANDIDATES = [
    ("Latanoprost", "Systemic arterial hypertension"),
    ("Bimatoprost", "Migraine"),
    ("Travoprost", "Systemic arterial hypertension"),
    ("Tafluprost", "Migraine"),
    ("Dorzolamide", "Systemic arterial hypertension"),
    ("Brinzolamide", "Migraine"),
    ("Brimonidine", "Major depressive disorder"),
    ("Oxymetazoline", "Attention deficit hyperactivity disorder"),
    # spares (used only on shortfall, in order)
    ("Lifitegrast", "Systemic lupus erythematosus"),
    ("Olopatadine", "Generalized anxiety disorder"),
    ("Azelastine", "Insomnia"),
    ("Ketotifen", "Irritable bowel syndrome"),
]

N3_CANDIDATES = [
    # (drug, pool target, pool job) — preclinical-only tool compounds with no
    # regulatory approval, selected from domain knowledge; verified below
    # against offline approval datasets + Europe PMC metadata.
    ("CORT108297", "NR3C1", "2de0698b458b4be28218830a3dad4710"),
    ("CORT113176", "NR3C1", "2de0698b458b4be28218830a3dad4710"),
    ("AD80", "RET", "61f542324d214a869b324fe41060bebb"),
    ("Pz-1", "RET", "61f542324d214a869b324fe41060bebb"),
    ("RPI-1", "RET", "61f542324d214a869b324fe41060bebb"),
    ("ICI 118551", "ADRB2", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Butoxamine", "ADRB2", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
]

E4_BRANDS = [
    # (brand, generic, pool job) — generic must be a pool member not claimed
    # in any other class; ground truth = FDA label brand↔generic mapping.
    ("Elavil", "AMITRIPTYLINE", "61f542324d214a869b324fe41060bebb"),
    ("Pamelor", "NORTRIPTYLINE", "61f542324d214a869b324fe41060bebb"),
    ("Flexeril", "CYCLOBENZAPRINE", "61f542324d214a869b324fe41060bebb"),
    ("Zyvox", "LINEZOLID", "61f542324d214a869b324fe41060bebb"),
    ("Cardura", "DOXAZOSIN", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Coreg", "CARVEDILOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Enablex", "DARIFENACIN", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Visken", "PINDOLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    # spares (used only on shortfall, in order) — generics are pool members
    # not claimed in any other class
    ("Sprycel", "DASATINIB", "61f542324d214a869b324fe41060bebb"),
    ("Tarceva", "ERLOTINIB", "61f542324d214a869b324fe41060bebb"),
    ("Nexavar", "SORAFENIB", "61f542324d214a869b324fe41060bebb"),
    ("Alecensa", "ALECTINIB", "61f542324d214a869b324fe41060bebb"),
    ("Ibrance", "PALBOCICLIB", "61f542324d214a869b324fe41060bebb"),
    ("Lamisil", "TERBINAFINE", "2de0698b458b4be28218830a3dad4710"),
    ("Tafinlar", "DABRAFENIB", "2de0698b458b4be28218830a3dad4710"),
]

POOL_CONTEXT_CONTROLS = [
    ("BUDESONIDE", "2de0698b458b4be28218830a3dad4710"),
    ("PREDNISONE", "2de0698b458b4be28218830a3dad4710"),
    ("HYDROCORTISONE", "2de0698b458b4be28218830a3dad4710"),
    ("DEFLAZACORT", "2de0698b458b4be28218830a3dad4710"),
    ("SELPERCATINIB", "61f542324d214a869b324fe41060bebb"),
    ("PRALSETINIB", "61f542324d214a869b324fe41060bebb"),
    ("TERBUTALINE", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("OLODATEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    # spares (used only on shortfall, in order) — non-oral single-ingredient
    # drugs absent from every pool, so no collision with the oral pool-free
    # control sample
    ("CICLESONIDE", "2de0698b458b4be28218830a3dad4710"),
    ("MOMETASONE FUROATE", "2de0698b458b4be28218830a3dad4710"),
    ("BECLOMETHASONE DIPROPIONATE", "2de0698b458b4be28218830a3dad4710"),
    ("ARFORMOTEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("PIRBUTEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("FLUNISOLIDE", "2de0698b458b4be28218830a3dad4710"),
]

_QUOTAS = {"E1": 4, "E2": None, "E3": 4, "E4": 8,   # E2 takes the remainder
           "N1": 8, "N2": 7, "N3": 7, "N4": 8}
GROUP_TOTALS = {"existing_fix": 30, "novel": 30, "control": 40}

_log: list[str] = []
_used_drugs: set[str] = set()   # one claim per normalized drug name


def log(line: str) -> None:
    _log.append(line)
    print(f"[build] {line}", flush=True)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _get_json(url: str, params: Optional[dict] = None,
              timeout: int = 20) -> Optional[Any]:
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None
    finally:
        time.sleep(0.15)  # polite pacing for anonymous API access


# --------------------------------------------------------------------------- #
# Health precheck — abort construction entirely during any outage.
# --------------------------------------------------------------------------- #

def health_precheck() -> None:
    probes = {
        "chembl": ("https://www.ebi.ac.uk/chembl/api/data/status.json", None),
        "openfda": ("https://api.fda.gov/drug/label.json",
                    {"search": 'openfda.generic_name:"IBUPROFEN"', "limit": 1}),
        "europepmc": ("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                      {"query": "EXT_ID:39084004", "format": "json"}),
        "pubtator": ("https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/",
                     {"text": "aspirin @@GENE_PTGS1"}),
    }
    failed = [name for name, (url, params) in probes.items()
              if _get_json(url, params) is None]
    if failed:
        raise SystemExit(
            f"[build] REFUSED: live sources unhealthy at construction: {failed}. "
            "Never construct a claim set during an outage.")


# --------------------------------------------------------------------------- #
# Raw-source verifiers (ground truth from raw artifacts, never from pipeline)
# --------------------------------------------------------------------------- #

_chembl_release: Optional[dict] = None


def chembl_release() -> dict:
    global _chembl_release
    if _chembl_release is None:
        status = _get_json("https://www.ebi.ac.uk/chembl/api/data/status.json")
        if not status:
            raise SystemExit("[build] REFUSED: ChEMBL status unreadable")
        _chembl_release = {
            "release": status.get("chembl_db_version"),
            "release_date": status.get("chembl_release_date"),
            "date_before_cutoff": bool(
                status.get("chembl_release_date")
                and status["chembl_release_date"] < CUTOFF),
        }
    return _chembl_release


def ofda_label_rows(name: str) -> list[dict]:
    data = _get_json(
        "https://api.fda.gov/drug/label.json",
        {"search": (f'openfda.generic_name:"{name}" OR '
                    f'openfda.brand_name:"{name}"'), "limit": 25})
    return (data or {}).get("results") or []


def _ofda_citation(rows: list[dict]) -> Optional[dict]:
    """Best citation row: must carry set_id + parseable effective_time < cutoff."""
    best = None
    for row in rows:
        eff = str(row.get("effective_time") or "")
        if not re.match(r"^\d{8}$", eff):
            continue
        iso = f"{eff[:4]}-{eff[4:6]}-{eff[6:]}"
        if iso >= CUTOFF:
            continue
        openfda = row.get("openfda") or {}
        cand = {
            "source": "fda_label",
            "identifier": row.get("set_id"),
            "artifact_date": iso,
            "release": f"spl_version:{row.get('version')}",
            "url": (f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm"
                    f"?setid={row.get('set_id')}"),
            "application_numbers": openfda.get("application_number") or [],
        }
        if best is None or iso > best["artifact_date"]:
            best = cand
    return best


def _ofda_substances(row: dict) -> list[str]:
    subs = (row.get("openfda") or {}).get("substance_name") or []
    return sorted({str(s).strip().upper() for s in subs if str(s).strip()})


def _ofda_routes(row: dict) -> list[str]:
    routes = (row.get("openfda") or {}).get("route") or []
    return sorted({str(r).strip().lower() for r in routes if str(r).strip()})


def chembl_molecule(name: str) -> Optional[dict]:
    data = _get_json("https://www.ebi.ac.uk/chembl/api/data/molecule.json",
                     {"pref_name__iexact": name})
    mols = (data or {}).get("molecules") or []
    if not mols:
        return None
    m = mols[0]
    rel = chembl_release()
    return {
        "molecule_chembl_id": m.get("molecule_chembl_id"),
        "pref_name": m.get("pref_name"),
        "withdrawn_flag": bool(m.get("withdrawn_flag")),
        "withdrawn_year": m.get("withdrawn_year"),
        "withdrawn_country": m.get("withdrawn_country"),
        "max_phase": m.get("max_phase"),
        "citation": {
            "source": "chembl_molecule",
            "identifier": m.get("molecule_chembl_id"),
            "artifact_date": rel["release_date"],
            "release": rel["release"],
            "url": (f"https://www.ebi.ac.uk/chembl/compound_report_card/"
                    f"{m.get('molecule_chembl_id')}/"),
        } if rel["date_before_cutoff"] else None,
    }


def chembl_action_type(mol_id: str, target_symbol: str) -> Optional[dict]:
    data = _get_json("https://www.ebi.ac.uk/chembl/api/data/mechanism.json",
                     {"molecule_chembl_id": mol_id})
    mechs = (data or {}).get("mechanisms") or []
    rel = chembl_release()
    for mech in mechs:
        tid = mech.get("target_chembl_id")
        tdata = _get_json(
            f"https://www.ebi.ac.uk/chembl/api/data/target/{tid}.json")
        comps = (tdata or {}).get("target_components") or []
        symbols = {str(c.get("component_synonyms") and "" or "")
                   for c in comps}
        genes = set()
        for c in comps:
            for syn in c.get("target_component_synonyms") or []:
                if syn.get("syn_type") == "GENE_SYMBOL":
                    genes.add(str(syn.get("component_synonym")))
        if target_symbol in genes:
            return {
                "action_type": mech.get("action_type"),
                "target_chembl_id": tid,
                "citation": {
                    "source": "chembl_mechanism",
                    "identifier": (f"{mol_id}|{tid}|"
                                   f"{mech.get('mec_id') or mech.get('record_id')}"),
                    "artifact_date": rel["release_date"],
                    "release": rel["release"],
                    "url": (f"https://www.ebi.ac.uk/chembl/compound_report_card/"
                            f"{mol_id}/"),
                } if rel["date_before_cutoff"] else None,
            }
    return None


def europepmc_primary_paper(query: str) -> Optional[dict]:
    data = _get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": query, "format": "json", "resultType": "core",
         "pageSize": 25, "sort": "FIRST_PDATE asc"})
    hits = ((data or {}).get("resultList") or {}).get("result") or []
    for hit in hits:
        types = ((hit.get("pubTypeList") or {}).get("pubType")) or []
        if any(t.lower() in ("review", "systematic review", "meta-analysis",
                             "editorial") for t in types):
            continue
        date = hit.get("firstPublicationDate") or ""
        if not date or date >= CUTOFF:
            continue
        return {
            "source": "europe_pmc",
            "identifier": f"PMID:{hit.get('pmid') or hit.get('id')}",
            "artifact_date": date,
            "release": None,
            "url": f"https://europepmc.org/article/MED/{hit.get('pmid') or hit.get('id')}",
            "title": hit.get("title"),
            "pub_types": types,
        }
    return None


def europepmc_clinical_trial_hits(drug: str) -> Optional[int]:
    data = _get_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
        {"query": f'TITLE_ABS:"{drug}" AND PUB_TYPE:"clinical-trial"',
         "format": "json", "pageSize": 1})
    if data is None:
        return None
    return int((data.get("hitCount") or 0))


def drugcentral_has_drug(name: str) -> bool:
    db = sqlite3.connect(DRUGCENTRAL)
    try:
        cur = db.execute("SELECT 1 FROM structures WHERE upper(name) = ? LIMIT 1",
                         (name.upper(),))
        return cur.fetchone() is not None
    finally:
        db.close()


def repodb_has_drug(rows: list[dict], name: str) -> bool:
    return any(_norm(r["drug_name"]) == _norm(name) for r in rows)


# --------------------------------------------------------------------------- #
# Pools (reachability only — never ground truth)
# --------------------------------------------------------------------------- #

def load_pools() -> dict[str, dict]:
    pools = {}
    for job_id, disease in POOLS.items():
        path = f"output/candidates/{job_id}.json"
        cands = json.load(open(path)).get("candidates", [])
        pools[job_id] = {
            "disease": disease,
            "target": POOL_TARGETS[job_id],
            "names": {str(c.get("drug_name") or "").upper() for c in cands},
            "safety_capped": sorted({str(c.get("drug_name") or "").upper()
                                     for c in cands if c.get("safety_cap_applied")}),
            "mech_capped": sorted({str(c.get("drug_name") or "").upper()
                                   for c in cands if c.get("mechanism_cap_applied")}),
            "blackbox": sorted({str(c.get("drug_name") or "").upper()
                                for c in cands if c.get("black_box_advisory")}),
        }
    return pools


def pool_of(pools: dict, drug: str) -> Optional[str]:
    for job_id, pool in pools.items():
        if drug.upper() in pool["names"]:
            return job_id
    return None


# --------------------------------------------------------------------------- #
# Claim builders
# --------------------------------------------------------------------------- #

_claims: list[dict] = []


def _add_claim(group: str, defect_class: str, input_fields: dict,
               expected: dict, citation: dict, note: str) -> None:
    key = _norm(input_fields["drug_name"])
    _used_drugs.add(key)
    _claims.append({
        "group": group,
        "defect_class": defect_class,
        "input": input_fields,
        "truth": {"expected": expected, "citation": citation, "note": note},
    })
    log(f"  ACCEPT {group}/{defect_class}: {input_fields['drug_name']} "
        f"(citation {citation['source']} {citation['identifier']} "
        f"dated {citation['artifact_date']})")


def build_e_class(pools: dict) -> None:
    """E1 withdrawn / E2 boxed-not-withdrawn / E3 direction / E4 brand names."""
    # --- E4 first (brands block their generics from other classes) ----------
    log("E4 unresolved_name_honesty — candidates from fixed brand map")
    e4_added = 0
    for brand, generic, job_id in E4_BRANDS:
        if e4_added >= _QUOTAS["E4"]:
            break
        if generic.upper() not in pools[job_id]["names"]:
            log(f"  EXCLUDE E4 {brand}: generic {generic} not in pool "
                f"{job_id[:8]} (reachability)")
            continue
        rows = ofda_label_rows(brand)
        cit = _ofda_citation(rows)
        match = [r for r in rows
                 if brand.upper() in [b.upper() for b in
                                      (r.get("openfda") or {}).get("brand_name") or []]]
        if not cit or not match:
            log(f"  EXCLUDE E4 {brand}: no cutoff-eligible FDA label naming "
                f"the brand (unverifiable ground truth)")
            continue
        _add_claim("existing_fix", "E4_unresolved_name_honesty", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": brand,
            "job_id_hint": job_id,
            "claim": {},
        }, {"status": "unresolved"}, cit,
            f"Brand name of {generic} per FDA label; a name that does not "
            f"resolve must read UNRESOLVED, never a false authoritative ABSENT.")
        _used_drugs.add(_norm(generic))   # generic blocked everywhere else
        e4_added += 1

    # --- E1: genuinely withdrawn (ChEMBL withdrawn_flag) --------------------
    log("E1 safety_withdrawal — pool safety/blackbox-flagged drugs verified "
        "against ChEMBL withdrawn_flag")
    flagged = sorted({d for p in pools.values()
                      for d in p["safety_capped"] + p["blackbox"]})
    e1_added = 0
    for drug in flagged:
        if e1_added >= _QUOTAS["E1"]:
            break
        if _norm(drug) in _used_drugs:
            continue
        mol = chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E1 {drug}: no ChEMBL molecule record "
                f"(withdrawal unverifiable)")
            continue
        if not mol["withdrawn_flag"]:
            continue  # not E1 material; still E2-eligible below
        if not mol["citation"]:
            log(f"  EXCLUDE E1 {drug}: ChEMBL release date not before cutoff")
            continue
        job_id = pool_of(pools, drug)
        _add_claim("existing_fix", "E1_safety_withdrawal", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": drug,
            "job_id_hint": job_id,
            "claim": {},
        }, {"status": "found", "cap_applied": True,
            "cap_reason_contains": "Safety cap"}, mol["citation"],
            f"Withdrawn from market ({mol['withdrawn_country']} "
            f"{mol['withdrawn_year']}) per {mol['citation']['release']}; "
            f"the audit must disclose the safety cap.")
        e1_added += 1
    if e1_added < _QUOTAS["E1"]:
        log(f"  SHORTFALL E1: {e1_added}/{_QUOTAS['E1']} — remainder "
            f"reallocates to E2 per protocol §2")

    # --- E3: mechanism-direction (ChEMBL action_type on pool target) --------
    log("E3 direction_incompatible — MM mechanism-capped drugs verified "
        "against ChEMBL action_type on NR3C1")
    mm = pools["2de0698b458b4be28218830a3dad4710"]
    e3_added = 0
    for drug in mm["mech_capped"]:
        if e3_added >= _QUOTAS["E3"]:
            break
        if _norm(drug) in _used_drugs:
            continue
        mol = chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E3 {drug}: no ChEMBL molecule record")
            continue
        mech = chembl_action_type(mol["molecule_chembl_id"], "NR3C1")
        if not mech or not mech["citation"]:
            log(f"  EXCLUDE E3 {drug}: no ChEMBL mechanism record on NR3C1")
            continue
        if str(mech["action_type"]).upper() not in ("ANTAGONIST", "INHIBITOR",
                                                    "BLOCKER", "MODULATOR"):
            log(f"  EXCLUDE E3 {drug}: ChEMBL action_type "
                f"{mech['action_type']} is not incompatibility-class for a "
                f"glucocorticoid-activation indication (ground truth fails)")
            continue
        _add_claim("existing_fix", "E3_direction_incompatible", {
            "disease_name": mm["disease"],
            "drug_name": drug,
            "job_id_hint": "2de0698b458b4be28218830a3dad4710",
            "claim": {},
        }, {"status": "found", "cap_reason_contains": "Mechanism-direction cap"},
            mech["citation"],
            f"ChEMBL records action_type={mech['action_type']} on NR3C1; a "
            f"glucocorticoid-agonist indication is directionally incompatible.")
        e3_added += 1
    if e3_added < _QUOTAS["E3"]:
        log(f"  SHORTFALL E3: {e3_added}/{_QUOTAS['E3']} — remainder "
            f"reallocates to E2 per protocol §2")

    # --- E2: boxed warning, NOT withdrawn — takes the E-group remainder -----
    e2_target = GROUP_TOTALS["existing_fix"] - e1_added - e3_added - e4_added
    log(f"E2 boxed_warning_not_withdrawal — quota {e2_target} "
        f"(30 minus E1 {e1_added} + E3 {e3_added} + E4 {e4_added})")
    e2_added = 0
    for drug in flagged:
        if e2_added >= e2_target:
            break
        if _norm(drug) in _used_drugs:
            continue
        mol = chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E2 {drug}: no ChEMBL molecule record")
            continue
        if mol["withdrawn_flag"]:
            continue  # belongs to E1 territory
        rows = ofda_label_rows(drug)
        cit = _ofda_citation(rows)
        boxed = [r for r in rows if r.get("boxed_warning")]
        if not cit or not boxed:
            log(f"  EXCLUDE E2 {drug}: no cutoff-eligible FDA label with a "
                f"boxed warning (ground truth unverifiable)")
            continue
        job_id = pool_of(pools, drug)
        _add_claim("existing_fix", "E2_boxed_warning_not_withdrawal", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": drug,
            "job_id_hint": job_id,
            "claim": {},
        }, {"status": "found", "black_box_advisory": True,
            "safety_cap_applied": False}, cit,
            "FDA label carries a boxed warning but the drug remains marketed "
            "(ChEMBL withdrawn_flag=false): the audit must show a black-box "
            "advisory and must NOT raise a market-withdrawal safety cap.")
        e2_added += 1
    if e2_added < e2_target:
        log(f"  SHORTFALL E2: {e2_added}/{e2_target} — "
            f"RECORDED; group total must still reach 30")
    log(f"E-group total: {e1_added + e2_added + e3_added + e4_added}/30")


def build_n_class(pools: dict) -> None:
    # --- N1: combination products -------------------------------------------
    log("N1 combination-product splitting — fixed combo list verified against "
        "raw FDA labels (>=2 active substances)")
    n1_added = 0
    for name, disease in N1_CANDIDATES:
        if n1_added >= _QUOTAS["N1"]:
            break
        rows = ofda_label_rows(name)
        cit = _ofda_citation(rows)
        n_subs = max((len(_ofda_substances(r)) for r in rows), default=0)
        if not cit or n_subs < 2:
            log(f"  EXCLUDE N1 {name}: label unverifiable or <2 substances "
                f"(max substances seen: {n_subs})")
            continue
        _add_claim("novel", "N1_combination_product_splitting", {
            "disease_name": disease,
            "drug_name": name,
            "claim": {},
        }, {"finding": {"code": "N1", "status": "flagged"}}, cit,
            f"FDA label lists {n_subs} active substances; auditing the "
            f"combination as a single agent must raise N1.")
        n1_added += 1

    # --- N4: local-only routes claimed systemic ------------------------------
    log("N4 dose/route implausibility — local-only drugs claimed oral/systemic")
    n4_added = 0
    for name, disease in N4_CANDIDATES:
        if n4_added >= _QUOTAS["N4"]:
            break
        if _norm(name) in _used_drugs:
            continue
        rows = ofda_label_rows(name)
        cit = _ofda_citation(rows)
        routes = sorted({route for r in rows for route in _ofda_routes(r)})
        if not cit or not routes:
            log(f"  EXCLUDE N4 {name}: no cutoff-eligible label with routes")
            continue
        if not all(route in LOCAL_ROUTES for route in routes):
            log(f"  EXCLUDE N4 {name}: labeled routes {routes} include a "
                f"systemic route — not a local-only drug (ground truth fails)")
            continue
        _add_claim("novel", "N4_dose_route_implausibility", {
            "disease_name": disease,
            "drug_name": name,
            "claim": {"route": "oral",
                      "context": "systemic plasma exposure"},
        }, {"finding": {"code": "N4", "status": "flagged"}}, cit,
            f"All labeled routes are local {routes}; an oral/systemic "
            f"exposure claim must raise N4.")
        n4_added += 1

    # --- N2: biologics claimed as small molecules ----------------------------
    log("N2 biologic modality mis-scope — enriched-dataset non-small-molecule "
        "rows verified against raw FDA labels (BLA application number)")
    rows_ds = list(csv.DictReader(open(DATASET)))
    bio = sorted({r["drug_name"] for r in rows_ds
                  if r["chembl_molecule_type"] not in ("Small molecule", "")
                  and r["status"] == "Approved"})
    ind_of = {r["drug_name"]: r["ind_name"] for r in rows_ds
              if r["chembl_molecule_type"] not in ("Small molecule", "")}
    n2_added = 0
    for name in bio:
        if n2_added >= _QUOTAS["N2"]:
            break
        if _norm(name) in _used_drugs:
            continue
        rows = ofda_label_rows(name)
        cit = _ofda_citation(rows)
        bla = cit and any(str(a).upper().startswith("BLA")
                          for a in cit["application_numbers"])
        if not cit or not bla:
            continue  # not BLA-verifiable; skip silently (large universe)
        _add_claim("novel", "N2_biologic_modality_mis_scope", {
            "disease_name": ind_of.get(name, "unspecified"),
            "drug_name": name,
            "claim": {"modality": "small molecule"},
        }, {"finding": {"code": "N2", "status": "flagged"}}, cit,
            "FDA application number is a BLA (biologic); a claim framed as a "
            "small molecule must raise N2.")
        n2_added += 1

    # --- N3: preclinical-only tool compounds ---------------------------------
    log("N3 species/preclinical-only — tool compounds vs pool targets, "
        "verified absent from approval datasets + Europe PMC primary paper")
    repodb_rows = list(csv.DictReader(open(DATASET)))
    n3_added = 0
    for drug, target, job_id in N3_CANDIDATES:
        if repodb_has_drug(repodb_rows, drug) or drugcentral_has_drug(drug):
            log(f"  EXCLUDE N3 {drug}: appears in an approval dataset — "
                f"not preclinical-only (ground truth fails)")
            continue
        if drug.upper() in pools[job_id]["names"]:
            log(f"  EXCLUDE N3 {drug}: present in pool {job_id[:8]} "
                f"(reachability requires absence)")
            continue
        ct_hits = europepmc_clinical_trial_hits(drug)
        if ct_hits is None or ct_hits > 0:
            log(f"  EXCLUDE N3 {drug}: Europe PMC clinical-trial hits "
                f"= {ct_hits} (human clinical evidence may exist)")
            continue
        cit = europepmc_primary_paper(f'TITLE_ABS:"{drug}"')
        if not cit:
            log(f"  EXCLUDE N3 {drug}: no cutoff-eligible primary paper "
                f"(citation unverifiable)")
            continue
        _add_claim("novel", "N3_species_preclinical_only_mismatch", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": drug,
            "job_id_hint": job_id,
            "claim": {},
        }, {"finding": {"code": "N3", "status": "flagged"}}, cit,
            f"Unapproved tool compound with no clinical-trial literature; "
            f"mechanism evidence for {target} is preclinical-only "
            f"(primary citation {cit['identifier']}). A clinical-grade "
            f"framing must raise N3.")
        n3_added += 1

    # Novel-group shortfall reallocation: N1 -> N4 -> N2 (protocol §2)
    novel_total = n1_added + n2_added + n3_added + n4_added
    log(f"N-group before reallocation: {novel_total}/30 "
        f"(N1={n1_added} N2={n2_added} N3={n3_added} N4={n4_added})")
    deficit = GROUP_TOTALS["novel"] - novel_total
    if deficit > 0:
        log(f"  REALLOCATING {deficit} novel shortfall per fixed order "
            f"N1 -> N4 -> N2")
        for name, disease in N1_CANDIDATES:
            if deficit == 0:
                break
            if _norm(name) in _used_drugs:
                continue
            rows = ofda_label_rows(name)
            cit = _ofda_citation(rows)
            n_subs = max((len(_ofda_substances(r)) for r in rows), default=0)
            if not cit or n_subs < 2:
                continue
            _add_claim("novel", "N1_combination_product_splitting", {
                "disease_name": disease, "drug_name": name, "claim": {},
            }, {"finding": {"code": "N1", "status": "flagged"}}, cit,
                f"FDA label lists {n_subs} active substances (reallocation).")
            deficit -= 1
        for name, disease in N4_CANDIDATES:
            if deficit == 0:
                break
            if _norm(name) in _used_drugs:
                continue
            rows = ofda_label_rows(name)
            cit = _ofda_citation(rows)
            routes = sorted({route for r in rows for route in _ofda_routes(r)})
            if not cit or not routes or not all(
                    route in LOCAL_ROUTES for route in routes):
                continue
            _add_claim("novel", "N4_dose_route_implausibility", {
                "disease_name": disease, "drug_name": name,
                "claim": {"route": "oral",
                          "context": "systemic plasma exposure"},
            }, {"finding": {"code": "N4", "status": "flagged"}}, cit,
                f"All labeled routes local {routes} (reallocation).")
            deficit -= 1
        for name in bio:
            if deficit == 0:
                break
            if _norm(name) in _used_drugs:
                continue
            rows = ofda_label_rows(name)
            cit = _ofda_citation(rows)
            if not cit or not any(str(a).upper().startswith("BLA")
                                  for a in cit["application_numbers"]):
                continue
            _add_claim("novel", "N2_biologic_modality_mis_scope", {
                "disease_name": ind_of.get(name, "unspecified"),
                "drug_name": name, "claim": {"modality": "small molecule"},
            }, {"finding": {"code": "N2", "status": "flagged"}}, cit,
                "BLA biologic claimed as small molecule (reallocation).")
            deficit -= 1
        if deficit:
            log(f"  UNFILLED NOVEL DEFICIT: {deficit} — construction fails "
                f"group quota; must be resolved before freeze")
            raise SystemExit(2)


def build_controls(pools: dict) -> None:
    rows_ds = list(csv.DictReader(open(DATASET)))
    from validation.select_benchmark_cases import _dev_suite_drugs
    dev_drugs = _dev_suite_drugs()

    log("Controls (pool-free) — seeded sample of approved single-ingredient "
        "oral small molecules; label verifies cleanliness")
    eligible = sorted({r["drug_name"] for r in rows_ds
                       if r["status"] == "Approved"
                       and r["chembl_molecule_type"] == "Small molecule"
                       and "+" not in r["drug_name"]
                       and _norm(r["drug_name"]) not in dev_drugs})
    rng = random.Random(SEED)
    rng.shuffle(eligible)
    ind_of = {}
    for r in rows_ds:
        ind_of.setdefault(r["drug_name"], r["ind_name"])
    added = 0
    attempts = 0
    for name in eligible:
        if added >= 32:
            break
        attempts += 1
        if attempts > 120:
            log("  CONTROL SAMPLING BUDGET EXHAUSTED (120 attempts)")
            break
        if _norm(name) in _used_drugs:
            continue
        if pool_of(pools, name):
            continue  # pool-context drugs are a separate control block
        rows = ofda_label_rows(name)
        cit = _ofda_citation(rows)
        if not cit:
            continue
        n_subs = max((len(_ofda_substances(r)) for r in rows), default=0)
        routes = sorted({route for r in rows for route in _ofda_routes(r)})
        if n_subs != 1 or "oral" not in routes:
            continue  # cleanliness ground truth not established
        _add_claim("control", "none", {
            "disease_name": ind_of.get(name, "unspecified"),
            "drug_name": name,
            "claim": {"route": "oral", "modality": "small molecule",
                      "context": "systemic plasma exposure"},
        }, {"no_finding_flagged": True}, cit,
            "Single-ingredient oral small molecule with cutoff-eligible FDA "
            "label; no N1-N4 finding may be flagged.")
        added += 1
    log(f"  pool-free controls: {added}/32 ({attempts} attempts)")

    log("Controls (pool-context) — approved drugs absent from the pooled case")
    added_ctx = 0
    for name, job_id in POOL_CONTEXT_CONTROLS:
        if name.upper() in pools[job_id]["names"]:
            log(f"  EXCLUDE control {name}: present in pool {job_id[:8]}")
            continue
        rows = ofda_label_rows(name)
        cit = _ofda_citation(rows)
        n_subs = max((len(_ofda_substances(r)) for r in rows), default=0)
        if not cit or n_subs != 1:
            log(f"  EXCLUDE control {name}: label unverifiable or not "
                f"single-ingredient")
            continue
        _add_claim("control", "none", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": name,
            "job_id_hint": job_id,
            "claim": {"modality": "small molecule"},
        }, {"no_finding_flagged": True}, cit,
            "Approved single-ingredient small molecule audited against a real "
            "case it is absent from; no N1-N4 finding may be flagged.")
        added_ctx += 1
    log(f"  pool-context controls: {added_ctx}/8")
    if added + added_ctx != GROUP_TOTALS["control"]:
        raise SystemExit(
            f"[build] control group quota not met: {added + added_ctx}/40")


# --------------------------------------------------------------------------- #

def main() -> None:
    health_precheck()
    pools = load_pools()
    log(f"Pools loaded (reachability only): "
        + ", ".join(f"{p['disease']} n={len(p['names'])}" for p in pools.values()))

    build_e_class(pools)
    build_n_class(pools)
    build_controls(pools)

    groups: dict[str, int] = {}
    classes: dict[str, int] = {}
    for c in _claims:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
        classes[c["defect_class"]] = classes.get(c["defect_class"], 0) + 1
    for group, total in GROUP_TOTALS.items():
        if groups.get(group) != total:
            raise SystemExit(
                f"[build] group {group} quota violated: "
                f"{groups.get(group)}/{total}")

    ordered = sorted(_claims, key=lambda c: (c["group"], c["defect_class"],
                                             _norm(c["input"]["drug_name"])))
    counters: dict[str, int] = {}
    for claim in ordered:
        if claim["group"] == "control":
            key = "C"
        else:
            key = claim["defect_class"].split("_")[0]
        counters[key] = counters.get(key, 0) + 1
        claim["claim_id"] = f"{key}-{counters[key]:02d}"

    payload = {
        "version": "audit-claim-set-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "citation_cutoff": CUTOFF,
        "construction_protocol":
            "validation/audit_claimset_construction_protocol.md",
        "seed": SEED,
        "pools_used_for_reachability": {
            job_id: {"disease": p["disease"], "top_target": p["target"]}
            for job_id, p in pools.items()},
        "claims": ordered,
    }
    blob = json.dumps(payload, indent=2, sort_keys=False)
    payload["claim_set_sha256"] = hashlib.sha256(blob.encode()).hexdigest()
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2)

    header = [
        "# Audit claim-set v1 — construction and exclusion log",
        "",
        f"- Constructed: {payload['created_at']}",
        f"- Seed: {SEED} · cutoff: {CUTOFF}",
        f"- Protocol: validation/audit_claimset_construction_protocol.md",
        f"- Claims: {len(_claims)} "
        + "(" + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())) + ")",
        "- Classes: " + ", ".join(f"{k}={v}" for k, v in sorted(classes.items())),
        "",
        "## Construction events (every acceptance AND exclusion)",
        "",
    ]
    with open(OUT_LOG, "w") as fh:
        fh.write("\n".join(header) + "\n" + "\n".join(_log) + "\n")
    print(f"[build] DONE: {len(_claims)} claims -> {OUT_JSON}; log -> {OUT_LOG}")


if __name__ == "__main__":
    main()
