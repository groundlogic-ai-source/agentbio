"""Build the frozen audit claim set v2 — the re-validation study registered
in validation/audit_claimset_v2_preregistration.md.

Relationship to v1 (build_audit_claim_set.py): v1 FAILED its pre-registered
bars; the two pipeline defects it exposed were fixed (main @ 44a298a) and
v2 re-validates the fixed pipeline on NEW claim instances. This builder
reuses v1's stateless raw-source helpers (same public endpoints, same
citation rules) but is a separate construction with three registered
differences:

  * Instance disjointness — every drug named in any v1 claim (and every v1
    E4 generic) is excluded from every v2 class. v2 never re-tests v1 items.
  * E4 repair — a brand is accepted ONLY if the RAW ChEMBL name/synonym
    search cannot resolve it (v1's construction assumption was falsified by
    ChEMBL's synonym tables; resolvability is now verified at construction
    time against the same public ChEMBL endpoints the pipeline reads — the
    pipeline's own resolution code is never imported).
  * N3 — v1's gates PLUS "no cutoff-eligible FDA label products": label
    absence is part of the defect definition under the audit-context-v2
    detector and is verified against raw openFDA responses.
  * Composition — existing_fix takes the honest pool-bounded yield (floor
    E_FLOOR, else construction aborts); novel fills DEFECT_TOTAL - E via
    the fixed order N1 -> N4 -> N2.

INDEPENDENCE BOUNDARY (unchanged from v1): this script never imports
api.audit, api.audit_context, the N1-N4 detectors, or the audit source
lanes. Pools are read for reachability only, never as claim ground truth.

Deterministic: fixed candidate lists, fixed seed, fixed quotas. Aborts if
any live source is unhealthy at construction time, and aborts unless every
pool carries the safety-v2 stamp (registered v2 health requirement).

Usage:
    python3 -m validation.build_audit_claim_set_v2
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import build_audit_claim_set as b

CUTOFF = b.CUTOFF                      # 2026-08-10 (unchanged, registered)
SEED = 20260811                        # distinct from v1 (20260810)
OUT_JSON = "validation/audit_claim_set_v2.json"
OUT_LOG = "validation/audit_claimset_v2_construction_log.md"
V1_JSON = "validation/audit_claim_set_v1.json"

DEFECT_TOTAL = 60
CONTROL_TOTAL = 40                     # 32 pool-free + 8 pool-context
E_FLOOR = 1                            # registered minimum E-group yield (Amendment 2)

# E4 candidates: (brand, generic, pool job). Generics are members of the
# refreshed pools not named in any v1 claim. Expected yield is near zero:
# v1 proved ChEMBL synonym resolution covers major brands — that is the
# finding, and the resolution probe below enforces it honestly.
E4_BRANDS_V2 = [
    ("Toprol-XL", "METOPROLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Lopressor", "METOPROLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Tenormin", "ATENOLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Hemangeol", "PROPRANOLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Inderal", "PROPRANOLOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Betapace", "SOTALOL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Serevent", "SALMETEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Rythmol", "PROPAFENONE", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Levophed", "NOREPINEPHRINE", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("Pacerone", "AMIODARONE", "2de0698b458b4be28218830a3dad4710"),
    ("Cordarone", "AMIODARONE", "2de0698b458b4be28218830a3dad4710"),
    ("Votrient", "PAZOPANIB", "2de0698b458b4be28218830a3dad4710"),
    ("Caprelsa", "VANDETANIB", "2de0698b458b4be28218830a3dad4710"),
    ("Impavido", "MILTEFOSINE", "2de0698b458b4be28218830a3dad4710"),
    ("Korlym", "MIFEPRISTONE", "2de0698b458b4be28218830a3dad4710"),
    ("Tykerb", "LAPATINIB", "2de0698b458b4be28218830a3dad4710"),
    ("Cytomel", "LIOTHYRONINE", "2de0698b458b4be28218830a3dad4710"),
    ("Xospata", "GILTERITINIB", "61f542324d214a869b324fe41060bebb"),
    ("Inrebic", "FEDRATINIB", "61f542324d214a869b324fe41060bebb"),
    ("Cabometyx", "CABOZANTINIB", "61f542324d214a869b324fe41060bebb"),
]

# N3 candidates: (drug, pool target, pool job) — preclinical tool compounds
# with primary literature, selected from domain knowledge; verified below
# against offline approval datasets, Europe PMC trial metadata, and raw
# openFDA label absence.
N3_CANDIDATES_V2 = [
    ("NVP-AST487", "RET", "61f542324d214a869b324fe41060bebb"),
    ("SPP86", "RET", "61f542324d214a869b324fe41060bebb"),
    ("AL082D06", "NR3C1", "2de0698b458b4be28218830a3dad4710"),
    ("CGP 20712A", "ADRB2", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("SR 59230A", "ADRB2", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
]

N1_CANDIDATES_V2 = [
    ("Amlodipine and benazepril", "Hypertension"),
    ("Valsartan and hydrochlorothiazide", "Hypertension"),
    ("Olmesartan and amlodipine", "Hypertension"),
    ("Atorvastatin and amlodipine", "Cardiovascular risk reduction"),
    ("Sacubitril and valsartan", "Heart failure"),
    ("Buprenorphine and naloxone", "Opioid dependence"),
    ("Dorzolamide and timolol", "Glaucoma"),
    ("Emtricitabine and tenofovir alafenamide", "HIV-1 infection"),
    ("Dolutegravir and lamivudine", "HIV-1 infection"),
    ("Hydrocodone and acetaminophen", "Pain"),
    ("Naproxen and esomeprazole", "Osteoarthritis"),
    ("Glecaprevir and pibrentasvir", "Hepatitis C"),
]

N4_CANDIDATES_V2 = [
    ("Netarsudil", "Systemic arterial hypertension"),
    ("Loteprednol etabonate", "Systemic lupus erythematosus"),
    ("Nepafenac", "Migraine"),
    ("Difluprednate", "Major depressive disorder"),
    ("Bepotastine", "Generalized anxiety disorder"),
    ("Epinastine", "Insomnia"),
    ("Alcaftadine", "Attention deficit hyperactivity disorder"),
    ("Cromolyn sodium", "Asthma"),
    # spares (used only on shortfall, in order)
    ("Fluorometholone", "Major depressive disorder"),
    ("Lodoxamide", "Generalized anxiety disorder"),
    ("Pemirolast", "Insomnia"),
    ("Ciclesonide", "Asthma"),
]

POOL_CONTEXT_CONTROLS_V2 = [
    ("TIOTROPIUM", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("INDACATEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("GLYCOPYRRONIUM", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("VILANTEROL", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("ACLIDINIUM", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("PREDNISOLONE", "2de0698b458b4be28218830a3dad4710"),
    ("TRIAMCINOLONE ACETONIDE", "2de0698b458b4be28218830a3dad4710"),
    ("FLUTICASONE FUROATE", "2de0698b458b4be28218830a3dad4710"),
    # spares (used only on shortfall, in order)
    ("UMECLIDINIUM", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("REVEFENACIN", "cddaa8e1fbe84309854e7dc6cdd8a71a"),
    ("METHYLPREDNISOLONE", "2de0698b458b4be28218830a3dad4710"),
    ("DEXAMETHASONE", "2de0698b458b4be28218830a3dad4710"),
]

_QUOTAS = {"E1": 4, "E3": 4, "E4": 8,   # E2 takes the pool-bounded remainder
           "N1": 8, "N2": 7, "N3": 7, "N4": 8}

_claims: list[dict] = []
_log: list[str] = []
_used_drugs: set[str] = set()
_V1_EXCLUDED: set[str] = set()


def log(line: str) -> None:
    print(f"[build-v2] {line}", flush=True)
    _log.append(line)


def _load_v1_exclusions() -> set[str]:
    """Every drug named in a v1 claim, plus v1's E4 generics (blocked there
    too). v2 re-validates the pipeline, never v1's items."""
    excluded = {b._norm(c["input"]["drug_name"])
                for c in json.load(open(V1_JSON))["claims"]}
    excluded |= {b._norm(generic) for _, generic, _ in b.E4_BRANDS}
    return excluded


def _available(name: str) -> bool:
    key = b._norm(name)
    return key not in _used_drugs and key not in _V1_EXCLUDED


def _add_claim(group: str, defect_class: str, input_fields: dict,
               expected: dict, citation: dict, note: str) -> None:
    key = b._norm(input_fields["drug_name"])
    assert key not in _V1_EXCLUDED, f"v1 instance leaked into v2: {key}"
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


def chembl_resolves(name: str) -> bool:
    """Raw ChEMBL resolution probe for the repaired E4 gate: any molecule
    returned by the public name/synonym full-text search counts as
    resolvable. Conservative (fuzzy false-positives exclude a brand), which
    only ever shrinks E4 — never invents an invalid claim.

    Fail-closed: retries 3x against transient ChEMBL flapping (observed
    2026-08-10: status.json oscillating 200/500 with HTML error pages on
    API endpoints), then aborts construction rather than guessing."""
    data = None
    for attempt in range(3):
        data = b._get_json(
            "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
            params={"q": name, "limit": 1})
        if data is not None:
            break
        if attempt < 2:
            log(f"  ChEMBL probe retry for '{name}' (attempt "
                f"{attempt + 1} failed)")
            time.sleep(8)
    if data is None:
        raise SystemExit(f"[build-v2] ChEMBL resolution probe failed for "
                         f"'{name}' after 3 attempts — refusing to "
                         f"construct E4 on a guess")
    return bool((data.get("page_meta") or {}).get("total_count"))


def build_e_class(pools: dict) -> int:
    """E4 brands / E1 withdrawn / E3 direction / E2 boxed-not-withdrawn.
    Returns the E-group yield (composition is construction-determined)."""
    # --- E4 first (brands block their generics from other classes) --------
    log("E4 unresolved_name_honesty — accepted ONLY if raw ChEMBL search "
        "cannot resolve the brand (v1 assumption repaired)")
    e4_added = 0
    for brand, generic, job_id in E4_BRANDS_V2:
        if e4_added >= _QUOTAS["E4"]:
            break
        if generic.upper() not in pools[job_id]["names"]:
            log(f"  EXCLUDE E4 {brand}: generic {generic} not in pool "
                f"{job_id[:8]} (reachability)")
            continue
        rows = b.ofda_label_rows(brand)
        cit = b._ofda_citation(rows)
        match = [r for r in rows
                 if brand.upper() in [x.upper() for x in
                                      (r.get("openfda") or {})
                                      .get("brand_name") or []]]
        if not cit or not match:
            log(f"  EXCLUDE E4 {brand}: no cutoff-eligible FDA label naming "
                f"the brand (unverifiable ground truth)")
            continue
        if chembl_resolves(brand):
            log(f"  EXCLUDE E4 {brand}: raw ChEMBL search RESOLVES the "
                f"brand — v1's falsified assumption; not a valid "
                f"unresolvable-name claim")
            continue
        _add_claim("existing_fix", "E4_unresolved_name_honesty", {
            "disease_name": pools[job_id]["disease"],
            "drug_name": brand,
            "job_id_hint": job_id,
            "claim": {},
        }, {"status": "unresolved"}, cit,
            f"Brand name of {generic} per FDA label; verified NON-resolving "
            f"in raw ChEMBL at construction; a name that does not resolve "
            f"must read UNRESOLVED, never a false authoritative ABSENT.")
        _used_drugs.add(b._norm(generic))   # generic blocked everywhere else
        e4_added += 1

    # --- E1: genuinely withdrawn (ChEMBL withdrawn_flag) ------------------
    log("E1 safety_withdrawal — refreshed-pool safety-flagged drugs verified "
        "against ChEMBL withdrawn_flag")
    flagged = sorted({d for p in pools.values()
                      for d in p["safety_capped"] + p["blackbox"]})
    e1_added = 0
    for drug in flagged:
        if e1_added >= _QUOTAS["E1"]:
            break
        if not _available(drug):
            continue
        mol = b.chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E1 {drug}: no ChEMBL molecule record "
                f"(withdrawal unverifiable)")
            continue
        if not mol["withdrawn_flag"]:
            continue  # not E1 material; still E2-eligible below
        if not mol["citation"]:
            log(f"  EXCLUDE E1 {drug}: ChEMBL release date not before cutoff")
            continue
        job_id = b.pool_of(pools, drug)
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

    # --- E3: mechanism-direction (ChEMBL action_type on pool target) ------
    log("E3 direction_incompatible — MM mechanism-capped drugs verified "
        "against ChEMBL action_type on NR3C1")
    mm = pools["2de0698b458b4be28218830a3dad4710"]
    e3_added = 0
    for drug in mm["mech_capped"]:
        if e3_added >= _QUOTAS["E3"]:
            break
        if not _available(drug):
            continue
        mol = b.chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E3 {drug}: no ChEMBL molecule record")
            continue
        mech = b.chembl_action_type(mol["molecule_chembl_id"], "NR3C1")
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

    # --- E2: boxed warning, NOT withdrawn — the pool-bounded remainder ----
    log("E2 boxed_warning_not_withdrawal — refreshed-pool black-box drugs "
        "verified against raw FDA labels")
    e2_added = 0
    for drug in sorted({d for p in pools.values() for d in p["blackbox"]}):
        if not _available(drug):
            continue
        mol = b.chembl_molecule(drug)
        if not mol:
            log(f"  EXCLUDE E2 {drug}: no ChEMBL molecule record")
            continue
        if mol["withdrawn_flag"]:
            continue  # belongs to E1 territory
        rows = b.ofda_label_rows(drug)
        cit = b._ofda_citation(rows)
        boxed = [r for r in rows if r.get("boxed_warning")]
        if not cit or not boxed:
            log(f"  EXCLUDE E2 {drug}: no cutoff-eligible FDA label with a "
                f"boxed warning (ground truth unverifiable)")
            continue
        job_id = b.pool_of(pools, drug)
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

    e_total = e1_added + e2_added + e3_added + e4_added
    log(f"E-group total: {e_total} "
        f"(E1={e1_added} E2={e2_added} E3={e3_added} E4={e4_added})")
    if e_total < E_FLOOR:
        raise SystemExit(
            f"[build-v2] E-group yield {e_total} < registered floor "
            f"{E_FLOOR} — construction aborts; the composition rule must be "
            f"amended in audit_claimset_v2_preregistration.md before retry")
    return e_total


def build_n_class(pools: dict, novel_target: int) -> None:
    # --- N1: combination products -----------------------------------------
    log("N1 combination-product splitting — fixed combo list verified "
        "against raw FDA labels (>=2 active substances)")
    n1_added = 0
    for name, disease in N1_CANDIDATES_V2:
        if n1_added >= _QUOTAS["N1"]:
            break
        if not _available(name):
            continue
        rows = b.ofda_label_rows(name)
        cit = b._ofda_citation(rows)
        n_subs = max((len(b._ofda_substances(r)) for r in rows), default=0)
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

    # --- N4: local-only routes claimed systemic ---------------------------
    log("N4 dose/route implausibility — local-only drugs claimed oral/"
        "systemic")
    n4_added = 0
    for name, disease in N4_CANDIDATES_V2:
        if n4_added >= _QUOTAS["N4"]:
            break
        if not _available(name):
            continue
        rows = b.ofda_label_rows(name)
        cit = b._ofda_citation(rows)
        routes = sorted({route for r in rows for route in b._ofda_routes(r)})
        if not cit or not routes:
            log(f"  EXCLUDE N4 {name}: no cutoff-eligible label with routes")
            continue
        if not all(route in b.LOCAL_ROUTES for route in routes):
            log(f"  EXCLUDE N4 {name}: labeled routes {routes} include a "
                f"systemic route — not a local-only drug (ground truth "
                f"fails)")
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

    # --- N3: preclinical-only tool compounds ------------------------------
    log("N3 species/preclinical-only — v1 gates PLUS no cutoff-eligible FDA "
        "label (label absence is part of the v2 defect definition)")
    repodb_rows = list(csv.DictReader(open(b.DATASET)))
    n3_added = 0
    for drug, target, job_id in N3_CANDIDATES_V2:
        if n3_added >= _QUOTAS["N3"]:
            break
        if not _available(drug):
            continue
        if b.repodb_has_drug(repodb_rows, drug) or b.drugcentral_has_drug(drug):
            log(f"  EXCLUDE N3 {drug}: appears in an approval dataset — "
                f"not preclinical-only (ground truth fails)")
            continue
        if drug.upper() in pools[job_id]["names"]:
            log(f"  EXCLUDE N3 {drug}: present in pool {job_id[:8]} "
                f"(reachability requires absence)")
            continue
        label_rows = b.ofda_label_rows(drug)
        if b._ofda_citation(label_rows):
            log(f"  EXCLUDE N3 {drug}: cutoff-eligible FDA label exists — "
                f"the v2 detector correctly treats this as unresolved, not "
                f"flagged (ground truth fails)")
            continue
        ct_hits = b.europepmc_clinical_trial_hits(drug)
        if ct_hits is None or ct_hits > 0:
            log(f"  EXCLUDE N3 {drug}: Europe PMC clinical-trial hits "
                f"= {ct_hits} (human clinical evidence may exist)")
            continue
        cit = b.europepmc_primary_paper(f'TITLE_ABS:"{drug}"')
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
            f"Unapproved tool compound with no FDA label and no "
            f"clinical-trial literature; mechanism evidence for {target} "
            f"is preclinical-only (primary citation {cit['identifier']}). "
            f"A clinical-grade framing must raise N3.")
        n3_added += 1

    # --- N2: biologics claimed as small molecules -------------------------
    log("N2 biologic modality mis-scope — enriched-dataset non-small-"
        "molecule rows verified against raw FDA labels (BLA)")
    rows_ds = repodb_rows
    bio = sorted({r["drug_name"] for r in rows_ds
                  if r["chembl_molecule_type"] not in ("Small molecule", "")
                  and r["status"] == "Approved"})
    ind_of = {r["drug_name"]: r["ind_name"] for r in rows_ds
              if r["chembl_molecule_type"] not in ("Small molecule", "")}
    n2_added = 0
    for name in bio:
        if n2_added >= _QUOTAS["N2"]:
            break
        if not _available(name):
            continue
        rows = b.ofda_label_rows(name)
        cit = b._ofda_citation(rows)
        bla = cit and any(str(a).upper().startswith("BLA")
                          for a in cit["application_numbers"])
        if not cit or not bla:
            continue  # not BLA-verifiable; skip silently (large universe)
        _add_claim("novel", "N2_biologic_modality_mis_scope", {
            "disease_name": ind_of.get(name, "unspecified"),
            "drug_name": name,
            "claim": {"modality": "small molecule"},
        }, {"finding": {"code": "N2", "status": "flagged"}}, cit,
            "FDA application number is a BLA (biologic); a claim framed as "
            "a small molecule must raise N2.")
        n2_added += 1

    # Novel-group shortfall reallocation: N1 -> N4 -> N2 (registered order)
    novel_total = n1_added + n2_added + n3_added + n4_added
    log(f"N-group before reallocation: {novel_total}/{novel_target} "
        f"(N1={n1_added} N2={n2_added} N3={n3_added} N4={n4_added})")
    deficit = novel_target - novel_total
    if deficit > 0:
        log(f"  REALLOCATING {deficit} novel shortfall per fixed order "
            f"N1 -> N4 -> N2")
        for name, disease in N1_CANDIDATES_V2:
            if deficit == 0:
                break
            if not _available(name):
                continue
            rows = b.ofda_label_rows(name)
            cit = b._ofda_citation(rows)
            n_subs = max((len(b._ofda_substances(r)) for r in rows),
                         default=0)
            if not cit or n_subs < 2:
                continue
            _add_claim("novel", "N1_combination_product_splitting", {
                "disease_name": disease, "drug_name": name, "claim": {},
            }, {"finding": {"code": "N1", "status": "flagged"}}, cit,
                f"FDA label lists {n_subs} active substances (reallocation).")
            deficit -= 1
        for name, disease in N4_CANDIDATES_V2:
            if deficit == 0:
                break
            if not _available(name):
                continue
            rows = b.ofda_label_rows(name)
            cit = b._ofda_citation(rows)
            routes = sorted({route for r in rows
                             for route in b._ofda_routes(r)})
            if not cit or not routes or not all(
                    route in b.LOCAL_ROUTES for route in routes):
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
            if not _available(name):
                continue
            rows = b.ofda_label_rows(name)
            cit = b._ofda_citation(rows)
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
                f"the registered composition; must be amended before retry")
            raise SystemExit(2)


def build_controls(pools: dict) -> None:
    rows_ds = list(csv.DictReader(open(b.DATASET)))
    from validation.select_benchmark_cases import _dev_suite_drugs
    dev_drugs = _dev_suite_drugs()

    log("Controls (pool-free) — seeded sample of approved single-ingredient "
        "oral small molecules; label verifies cleanliness")
    eligible = sorted({r["drug_name"] for r in rows_ds
                       if r["status"] == "Approved"
                       and r["chembl_molecule_type"] == "Small molecule"
                       and "+" not in r["drug_name"]
                       and b._norm(r["drug_name"]) not in dev_drugs})
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
        if attempts > 200:
            log("  CONTROL SAMPLING BUDGET EXHAUSTED (200 attempts)")
            break
        if not _available(name):
            continue
        if b.pool_of(pools, name):
            continue  # pool-context drugs are a separate control block
        rows = b.ofda_label_rows(name)
        cit = b._ofda_citation(rows)
        if not cit:
            continue
        n_subs = max((len(b._ofda_substances(r)) for r in rows), default=0)
        routes = sorted({route for r in rows for route in b._ofda_routes(r)})
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

    log("Controls (pool-context) — approved drugs absent from the pooled "
        "case")
    added_ctx = 0
    for name, job_id in POOL_CONTEXT_CONTROLS_V2:
        if added_ctx >= 8:
            break
        if not _available(name):
            log(f"  EXCLUDE control {name}: already claimed")
            continue
        if name.upper() in pools[job_id]["names"]:
            log(f"  EXCLUDE control {name}: present in pool {job_id[:8]}")
            continue
        rows = b.ofda_label_rows(name)
        cit = b._ofda_citation(rows)
        n_subs = max((len(b._ofda_substances(r)) for r in rows), default=0)
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
            "Approved single-ingredient small molecule audited against a "
            "real case it is absent from; no N1-N4 finding may be flagged.")
        added_ctx += 1
    log(f"  pool-context controls: {added_ctx}/8")
    if added_ctx < 8:
        # Amendment 4: the fixed pool-context list is structurally small
        # (in-pool membership and the single-ingredient label rule exclude
        # most inhaler-class names).  Fill the shortfall from the same
        # approved single-ingredient small-molecule universe as the
        # pool-free controls, with an independent seeded RNG so the
        # pool-free sample sequence is unchanged.  Ground truth is
        # identical: verifiable cutoff-eligible single-ingredient FDA label
        # AND absence from the assigned pooled case's pool.
        log("  pool-context dynamic fill (Amendment 4): seeded sample of "
            "approved single-ingredient small molecules absent from the "
            "assigned pooled case")
        cases = sorted({job for _, job in POOL_CONTEXT_CONTROLS_V2})
        rng_ctx = random.Random(SEED + 1)
        dyn = sorted({r["drug_name"] for r in rows_ds
                      if r["status"] == "Approved"
                      and r["chembl_molecule_type"] == "Small molecule"
                      and "+" not in r["drug_name"]
                      and b._norm(r["drug_name"]) not in dev_drugs})
        rng_ctx.shuffle(dyn)
        attempts_ctx = 0
        for name in dyn:
            if added_ctx >= 8:
                break
            attempts_ctx += 1
            if attempts_ctx > 120:
                log("  POOL-CONTEXT SAMPLING BUDGET EXHAUSTED (120 attempts)")
                break
            job_id = cases[added_ctx % len(cases)]
            if not _available(name):
                continue
            if name.upper() in pools[job_id]["names"]:
                continue
            rows = b.ofda_label_rows(name)
            cit = b._ofda_citation(rows)
            n_subs = max((len(b._ofda_substances(r)) for r in rows),
                         default=0)
            if not cit or n_subs != 1:
                continue
            _add_claim("control", "none", {
                "disease_name": pools[job_id]["disease"],
                "drug_name": name,
                "job_id_hint": job_id,
                "claim": {"modality": "small molecule"},
            }, {"no_finding_flagged": True}, cit,
                "Approved single-ingredient small molecule audited against "
                "a real case it is absent from; no N1-N4 finding may be "
                "flagged. (Dynamic fill, Amendment 4.)")
            added_ctx += 1
        log(f"  pool-context controls after dynamic fill: {added_ctx}/8 "
            f"({attempts_ctx} attempts)")
    if added + added_ctx != CONTROL_TOTAL:
        raise SystemExit(
            f"[build-v2] control group quota not met: "
            f"{added + added_ctx}/{CONTROL_TOTAL}")


FREEZE_MANIFEST = "validation/audit_claimset_v2_freeze_manifest.json"


def _refuse_if_frozen_and_scored() -> None:
    """Fail closed rather than overwrite the frozen artifact of record.

    The builder writes OUT_JSON/OUT_LOG unconditionally. Once v2 was frozen
    and its single scored run spent, a later re-run of the
    `build-audit-claimset-v2` workflow silently regenerated the claim set:
    the 100 claims were byte-identical but `created_at` moved, which changed
    the file sha and broke the freeze manifest's binding (see Amendment 7).
    A frozen, scored study must never be rebuildable by accident.
    """
    if not os.path.exists(FREEZE_MANIFEST):
        return
    try:
        with open(FREEZE_MANIFEST) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"[build-v2] REFUSED: freeze manifest {FREEZE_MANIFEST} exists "
            f"but is unreadable ({exc}); refusing to overwrite the claim set")
    scored = manifest.get("scored_results") or {}
    if not scored.get("results_sha256"):
        return
    if os.environ.get("AUDIT_V2_REBUILD_OVERRIDE") == "1":
        log("[build-v2] WARNING: rebuilding a frozen, scored claim set "
            "because AUDIT_V2_REBUILD_OVERRIDE=1. This invalidates the "
            "freeze binding and must be recorded as an amendment.")
        return
    raise SystemExit(
        "[build-v2] REFUSED: audit claim-set v2 is frozen and its single "
        f"scored run is spent (results sha {scored['results_sha256'][:16]}). "
        f"Rebuilding would overwrite the artifact of record and break the "
        f"freeze binding in {FREEZE_MANIFEST}. To construct a successor "
        "study, write a new builder with its own pre-registration. Set "
        "AUDIT_V2_REBUILD_OVERRIDE=1 only to deliberately destroy this "
        "freeze.")


def main() -> None:
    global _V1_EXCLUDED
    _refuse_if_frozen_and_scored()
    b.health_precheck()
    _V1_EXCLUDED = _load_v1_exclusions()
    log(f"v1 instance exclusion set: {len(_V1_EXCLUDED)} names")

    pools = b.load_pools()
    # Registered v2 health requirement: pools must carry the safety-v2 stamp
    # (unstamped pools are the stale-badge defect v2 is re-validating).
    for job_id in b.POOLS:
        snap = json.load(open(f"output/candidates/{job_id}.json"))
        if snap.get("safety_schema_version") != "safety-v2":
            raise SystemExit(
                f"[build-v2] pool {job_id[:8]} lacks the safety-v2 stamp — "
                f"run scripts/refresh_pool_safety.py first")
    log(f"Pools loaded (reachability only, all safety-v2 stamped): "
        + ", ".join(f"{p['disease']} n={len(p['names'])}"
                    for p in pools.values()))

    e_total = build_e_class(pools)
    build_n_class(pools, DEFECT_TOTAL - e_total)
    build_controls(pools)

    groups: dict[str, int] = {}
    classes: dict[str, int] = {}
    for c in _claims:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
        classes[c["defect_class"]] = classes.get(c["defect_class"], 0) + 1
    if groups.get("control") != CONTROL_TOTAL:
        raise SystemExit(f"[build-v2] control quota violated: "
                         f"{groups.get('control')}/{CONTROL_TOTAL}")
    if groups.get("existing_fix", 0) + groups.get("novel", 0) != DEFECT_TOTAL:
        raise SystemExit(f"[build-v2] defect total violated: "
                         f"{groups.get('existing_fix', 0)}+"
                         f"{groups.get('novel', 0)} != {DEFECT_TOTAL}")

    ordered = sorted(_claims, key=lambda c: (c["group"], c["defect_class"],
                                             b._norm(c["input"]["drug_name"])))
    counters: dict[str, int] = {}
    for claim in ordered:
        if claim["group"] == "control":
            key = "C"
        else:
            key = claim["defect_class"].split("_")[0]
        counters[key] = counters.get(key, 0) + 1
        claim["claim_id"] = f"{key}-{counters[key]:02d}"

    payload = {
        "version": "audit-claim-set-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "citation_cutoff": CUTOFF,
        "construction_protocol":
            "validation/audit_claimset_v2_preregistration.md",
        "predecessor": "validation/audit_claim_set_v1.json",
        "seed": SEED,
        "group_totals": groups,
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
        "# Audit claim-set v2 — construction and exclusion log",
        "",
        f"- Constructed: {payload['created_at']}",
        f"- Seed: {SEED} · cutoff: {CUTOFF}",
        f"- Protocol: validation/audit_claimset_v2_preregistration.md",
        f"- Predecessor: validation/audit_claim_set_v1.json (instances "
        f"excluded: {len(_V1_EXCLUDED)} names)",
        f"- Claims: {len(_claims)} "
        + "(" + ", ".join(f"{k}={v}" for k, v in sorted(groups.items())) + ")",
        "- Classes: " + ", ".join(f"{k}={v}" for k, v in sorted(classes.items())),
        "",
        "## Construction events (every acceptance AND exclusion)",
        "",
    ]
    with open(OUT_LOG, "w") as fh:
        fh.write("\n".join(header) + "\n" + "\n".join(_log) + "\n")
    print(f"[build-v2] DONE: {len(_claims)} claims -> {OUT_JSON}; "
          f"log -> {OUT_LOG}")


if __name__ == "__main__":
    main()
