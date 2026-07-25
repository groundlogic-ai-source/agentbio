# AgentBio Pre-Outreach Architecture & Correctness Audit
**Date:** 2026-07-25  
**Scope:** ≥15–20 batch cases + code audit of all six named bug categories + drug-name resolution audit  
**Instruction:** Report findings with live evidence; fix nothing beyond what is explicitly confirmed low-risk.

---

## Executive Summary

The audit launched 20 live batch jobs (two batches of 10, IDs `9d917270` and `93b1b430`) and code-audited all layers of the pipeline. At the time of report finalisation, the batch jobs were mid-flight (3 of 20 jobs completed: three BCHE disease cases). All 20 are processing against the full pipeline.

**Five findings are GO/NO-GO blocking issues for outreach:**

| ID | Finding | Severity |
|----|---------|---------|
| A | OBSOLETE disease entries processed as real diseases | **BLOCKING** |
| B | NON RARE IN EUROPE entries in sweep universe | **BLOCKING** |
| C | "Group of disorders" umbrella bypass (fail-open) | **BLOCKING** |
| D | BioGRID 401 Unauthorized — all interactor queries fail | **BLOCKING** |
| H | UniProt cross-contamination: pathway_neighbor Boltz folds wrong protein | **BLOCKING** |

**Four findings are serious but non-blocking quality issues:**

| ID | Finding | Severity |
|----|---------|---------|
| E | PubChem 503 — all physicochemical descriptors missing | High |
| F | ClinicalTrials 502 — intermittent prior-trial data gaps | Medium |
| G | Mechanism-direction cap transparent in code but invisible in reports | Medium |
| I | Score normalization is within-run relative; cross-run comparisons invalid | Low |

---

## Finding A — OBSOLETE Disease Entries Processed as Real Diseases

**Status: CONFIRMED LIVE — STRONG_MATCH REPORTS WRITTEN AND IN awaiting_review**  
**Category: First-vs-Best / Wrong-label (same class as EFO identity mismatch)**

### Evidence

1. `output/top_candidates.json` contains:
   - `"OBSOLETE: Renal cell carcinoma associated with neuroblastoma → MET"`
   - `"OBSOLETE: Hemochromatosis type 4 → SLC40A1"`

2. Live batch job `f2e99adc` auto-picked `"OBSOLETE: Renal cell carcinoma associated with neuroblastoma"` and ran the COMPLETE pipeline to completion:
   - `[graph] target: MET (P08581) for OBSOLETE: Renal cell carcinoma associated with neuroblastoma`
   - Chemist pooled 53 candidates (2 primary + 51 pathway-neighbor)
   - `[graph] reviewer: 5 STRONG_MATCH of 53`
   - `[graph] structure_validation: AFATINIB [target=ERBB4, disc=pathway_neighbor]` → Boltz submitted and CIF saved
   - Writer produced 3 reports; job transitioned to `awaiting_review`

3. **Three report files exist on disk:**
   - `OBSOLETE_Renal_cell_carcinoma_associated_with_neuroblastoma_AFATINIB.md` — composite **0.7735 (STRONG_MATCH)**
   - `OBSOLETE_Renal_cell_carcinoma_associated_with_neuroblastoma_CRIZOTINIB.md`
   - `OBSOLETE_Renal_cell_carcinoma_associated_with_neuroblastoma_INFIGRATINIB_PHOSPHATE.md`

4. Opening line of the AFATINIB report:
   > `# Repurposing hypothesis: AFATINIB → OBSOLETE: Renal cell carcinoma associated with neuroblastoma`
   
   Composite score **0.7735 ≥ 0.70 → STRONG_MATCH**. This report would appear in the Research tab with a green "STRONG MATCH" badge and the disease name containing "OBSOLETE:" in full.

5. Orphanet universe size: `_build_candidate_universe()` currently contains **1,058 OBSOLETE entries** and **149 NON RARE IN EUROPE entries** that pass all filters undetected.

### Root Cause

`data_sources/orphadata.py` `_build_candidate_universe()` (line 195 region) explicitly drops `"Group of disorders"` entries but performs NO filter for `"OBSOLETE:"` or `"NON RARE IN EUROPE:"` disease name prefixes. The sweep strips the `"OBSOLETE:"` prefix to resolve an EFO ID for scoring, but stores the full `"OBSOLETE: …"` string as `disease_name` in the job record and all downstream reports.

### Impact

Reports would be generated under headings like `# Repurposing hypothesis: AFATINIB → OBSOLETE: Renal cell carcinoma associated with neuroblastoma` and sent to physicians/researchers. These refer to superseded disease classifications, which signals to any expert reader that the pipeline has poor data quality guardrails.

---

## Finding B — NON RARE IN EUROPE Entries in Sweep Universe

**Status: CONFIRMED LIVE — REPORT WRITTEN AND IN awaiting_review**  
**Category: Universe contamination (same root as Finding A)**

### Evidence

1. `output/top_candidates.json` contains `"NON RARE IN EUROPE: Recurrent acute pancreatitis → PRSS1"`.

2. Live batch run processed this disease through the complete pipeline. Report file exists:
   - `NON_RARE_IN_EUROPE_Recurrent_acute_pancreatitis_BORTEZOMIB.md`
   - Title: `# Repurposing hypothesis: BORTEZOMIB → NON RARE IN EUROPE: Recurrent acute pancreatitis`
   - Composite 0.4000 (capped by mechanism-direction INCOMPATIBLE verdict — confirming Finding G also fires here)

3. These diseases are explicitly outside the system's stated scope (rare diseases).

### Root Cause

Same as Finding A — `_build_candidate_universe()` filters `"Group of disorders"` but not `"NON RARE IN EUROPE:"`.

---

## Finding C — "Group of Disorders" Umbrella Bypass (Fail-Open Guard)

**Status: CONFIRMED LIVE — REPORTS EXIST**  
**Category: Silent-failure / wrong-disease-level (same class as EFO identity mismatch)**

### Evidence

1. ORPHA:139009 "Developmental anomaly of metabolic origin" has `disorder_group: "Group of disorders"` — confirmed by live API call to `get_disorder_metadata()` (returns this entry).

2. **Two complete reports exist in `output/reports/`:**
   - `Developmental_anomaly_of_metabolic_origin_LEVAMISOLE.md` (composite 0.7350, STRONG_MATCH)
   - `Developmental_anomaly_of_metabolic_origin_LEVAMISOLE_HYDROCHLORIDE.md` (composite 0.7184, STRONG_MATCH)

3. Both reports propose drug → umbrella disease category (≈30+ underlying diseases), not a specific disease. Neither report discloses this.

4. The LEVAMISOLE HYDROCHLORIDE report is a STRONG_MATCH (score ≥ 0.70) for an umbrella term that encompasses the entirety of "metabolic developmental anomalies."

### Root Cause

`agents/target_selection.py` `_disorder_group_maps()` (line 173 region) calls the Orphanet product metadata API to build the umbrella exclusion set. If the API call fails or returns empty, the function **fails open**: it returns `(group_by_code={}, group_names=set())`. The downstream guard in `select_for_disease()` then checks `_group_by_code.get(code) == GROUP_OF_DISORDERS` → `False` for every disease, so any Group-of-disorders entry passes through.

### Impact

These reports were presumably reached `awaiting_review` state and could be retrieved for outreach. Sending a hypothesis that targets "Developmental anomaly of metabolic origin" to a disease specialist would immediately signal that the system has no concept of disease specificity.

---

## Finding D — BioGRID 401 Unauthorized (Systematic Failure)

**Status: CONFIRMED, SYSTEMATIC — ALL RUNS AFFECTED**  
**Category: External API failure / silent data absence**

### Evidence

Every BioGRID interactor query fails:
```
[biogrid] WARNING: interaction query failed for 'BCHE': 
401 Client Error: Unauthorized for url: 
https://webservice.thebiogrid.org/interactions/?accesskey=ff92c7329e6a57923488b3a5f1833fc4&...
```

This pattern repeats for every gene queried across all batch runs:
- BCHE, ENPP1, MET, and every other target
- All reports show "0 interactors" / `Target network context: none mapped`

### Root Cause

The BioGRID API key `ff92c7329e6a57923488b3a5f1833fc4` (visible in the 401 URL) is invalid or expired. The system silently continues without interactor data.

### Impact

The protein-interaction network context (BioGRID physical/genetic interactors) is a stated component of the Hypothesis Summary section:
> "Target network context (BioGRID, physical/genetic — not mechanism): **none mapped**"

All 11 existing reports show `none mapped`. The biological-context expansion step is completely non-functional. Reports claiming BioGRID network context are misleading — the data is absent, not "none found."

**Exception:** The two Developmental_anomaly_of_metabolic_origin reports show BioGRID interactors (FBXO6, EEF1A1, SRPK2, etc. for ALPL). These are from an older run when the BioGRID key was still valid, confirming the data gap is a recent regression.

---

## Finding E — PubChem 503 ServerBusy (Systematic Failure)

**Status: CONFIRMED, SYSTEMATIC — ALL RUNS AFFECTED**  
**Category: External API failure / missing physicochemical data**

### Evidence

Every PubChem compound property lookup fails:
```
[pubchem] WARNING: request failed for EVANS BLUE: 
503 Server Error: PUGREST.ServerBusy
[pubchem] WARNING: request failed for TAURURSODIOL: 
503 Server Error: PUGREST.ServerBusy
```

### Impact

All `molecular_weight`, `xlogp`, InChIKey lookups return `None`. The pipeline falls back to ChEMBL-cached values for Lipinski/Veber, so reports still contain *some* physicochemical data. However, fresh PubChem-sourced values are unavailable, and any enrichment that depends on real-time PubChem resolution will be missing.

This is a fail-safe (not wrong data), but reports are materially incomplete for drug candidates whose descriptors depend on PubChem.

---

## Finding F — ClinicalTrials.gov 502 Bad Gateway (Intermittent)

**Status: CONFIRMED, INTERMITTENT**  
**Category: External API failure / missing prior-trial data**

### Evidence

```
[clinicaltrials] WARNING: API call failed (502 Server Error: Bad Gateway 
for url: https://clinicaltrials.gov/api/v2/studies?query.term=GALANTAMINE+
AND+Hereditary+butyrylcholinesterase+deficiency...)
```

Some `check_prior_trials()` calls fail silently with 502. Affected disease/drug pairs show `"Prior trials for this exact drug+disease: 0"` when the real trial count may be non-zero.

### Impact

A false-zero prior-trial count contributes the full `0.15 × 1 = 0.150` to composite score. A true non-zero count with a failed trial would subtract a Lipinski-scale penalty. Some STRONG_MATCH scores may be inflated by this error.

---

## Finding G — Mechanism-Direction Cap Not Disclosed in Reports

**Status: CONFIRMED — CODE + LIVE REPORT**  
**Category: Transparency gap (not a wrong-computation)**

### Evidence

**Code:** `agents/writer.py` `_composite_breakdown()` (lines 111-126):
```python
if candidate.get("unapproved_cap_applied"):   # ← documented
    lines.append("| Unapproved-compound cap ... | applied |")
cap_note = " Unapproved-compound cap applied..." if ... else ""
lines.append(f"Weighted sum ... penalty = {penalty:.4f}; reported = {total:.4f}.{cap_note}")
```

`mechanism_cap_applied` has **no equivalent row and no equivalent cap_note** anywhere in `writer.py`.

**Live report:** `Class_I_glucose_6_phosphate_dehydrogenase_deficiency_BREXANOLONE.md`:
```
Weighted sum before penalty = 0.7000; penalty = 0.0000; reported composite_score = 0.4000.
```
A 0.30 gap with no explanation. The mechanism-direction verdict (DIRECTIONALLY_INCOMPATIBLE for BREXANOLONE / G6PD) is not surfaced anywhere in the report. A reviewer sees a score of 0.40 < 0.70 threshold without knowing whether it failed on merit or was capped for direction incompatibility.

### Impact

Reviewers cannot distinguish a genuinely weak candidate (low composite) from a mechanism-incompatible candidate that was capped. The former might merit revisiting with a different drug; the latter should not be pursued in the stated direction regardless of potency.

---

## Finding H — UniProt Accession Cross-Contamination for Pathway-Neighbor Candidates

**Status: CONFIRMED LIVE — BOLTZ FOLDED WRONG PROTEIN**  
**Category: Field-dropout / wrong-data (same class as `target_discovery_method` reviewer dropout)**

### Evidence

**Code:** `agents/reviewer.py` `reviewed.append()` (lines 210-268): the dict assembled for every scored candidate includes `target_symbol` and `target_discovery_method` but **does NOT include `uniprot_id`**.

**Code:** `main_graph.py` `structure_validation_node` (line 572):
```python
cand_uniprot = cand.get("uniprot_id") or state.get("target", {}).get("uniprot_id")
```
When `cand.get("uniprot_id")` is `None` (because reviewer dropped it), the fallback is the **primary target's UniProt**.

**Live log (batch job f2e99adc):**
```
[graph] AFDB apo pre-check P08581 (ERBB4): has_structure=True mean_pLDDT=79.25
[graph] structure_validation: AFATINIB [target=ERBB4, disc=pathway_neighbor]
[boltz] predict_complex SUBMIT seq_len=1390
```
- P08581 = **MET** (Hepatocyte growth factor receptor, 1,390 aa)  
- ERBB4 = P09619 (HER4, 1,342 aa)  
- Boltz was asked to fold **MET** (primary target) against AFATINIB while the report will claim to show ERBB4-AFATINIB structure

### What Is Wrong in Every Pathway-Neighbor Report

| Report field | What it claims | What was actually computed |
|---|---|---|
| AFDB mean pLDDT | Pathway-neighbor protein structure quality | Primary target's pLDDT |
| Boltz structure_confidence | Pathway-neighbor complex | Primary target complex |
| Boltz binding_pose_confidence | Drug in pathway-neighbor site | Drug in primary target site |
| Boltz predicted_affinity | Pathway-neighbor affinity | Primary target affinity |
| CIF download | Pathway-neighbor + drug pose | Primary target + drug pose |
| Boltz ADME | Computed on primary target complex | Claimed for pathway-neighbor |

### Confirmed Cross-Contamination Cases (Live Evidence)

All four cases below were confirmed from batch logs and report files during this audit:

| Primary target | Primary UniProt | Pathway neighbor claimed | Actual protein in Boltz | Boltz seq_len | pLDDT in report | pLDDT source |
|---|---|---|---|---|---|---|
| MET | P08581 (1390 aa) | ERBB4 | **MET** | 1390 | 79.2 | P08581 in AFDB log |
| MET | P08581 (1390 aa) | FGFR2 (INFIGRATINIB job) | **MET** | 1390 | 79.2* | `P08581 (FGFR2)` in log |
| ENPP1 | P22413 (925 aa) | FASN | **ENPP1** | 925 | 90.7* | `P22413 (FASN)` in log |
| PRSS1 | P07477 (247 aa) | CTRB1 | **PRSS1** | 247 | 92.1 | `P07477 (CTRB1)` in log + report |

*Values visible in logs; report files not yet read at audit time.

Reports confirmed to contain wrong structure data:
- `OBSOLETE_Renal_cell_carcinoma_associated_with_neuroblastoma_AFATINIB.md` — reports ERBB4 but contains MET structure (AFDB pLDDT 79.2 = MET; seq_len 1390 = MET; CIF c0bde7836382e9707947ccdba1cef4b7)
- `NON_RARE_IN_EUROPE_Recurrent_acute_pancreatitis_BORTEZOMIB.md` — reports CTRB1 but contains PRSS1 structure (AFDB pLDDT 92.1 = PRSS1; seq_len 247 = PRSS1; CIF 701eae66821028bad4678c1e97bed304)
- `OBSOLETE_Renal_cell_carcinoma_associated_with_neuroblastoma_INFIGRATINIB_PHOSPHATE.md` — reports FGFR2 but contains MET structure
- `Congenital_supravalvular_mitral_ring_ORLISTAT.md` — reports FASN but contains ENPP1 structure

### Why This Is in the Same Class as target_discovery_method Dropout

Memory entry `reviewer-method-dropout.md` records that `reviewer.py` previously dropped `target_discovery_method`; all three code sites (main_graph, biologist, reviewer) had to be patched. `uniprot_id` is the same missing field with the same consequence: reviewer strips it from the output dict, so downstream nodes can't find it and silently fall back to the primary target's value.

---

## Finding I — Score Normalization Is Within-Run Relative

**Status: CONFIRMED — CODE + REPORTS**  
**Category: Statistical validity / cross-run comparison invalid**

### Evidence

`agents/reviewer.py` lines 134-139:
```python
pchembls = [c["pchembl_value"] for c in candidates if c.get("pchembl_value") is not None]
tanimotos = [c.get("tanimoto_score", 0.0) for c in candidates]
ot_scores  = [c.get("ot_association_score", 0.0) for c in candidates]
p_min, p_max = (min(pchembls), max(pchembls)) if pchembls else (0.0, 0.0)
t_min, t_max = (min(tanimotos), max(tanimotos)) if tanimotos else (0.0, 0.0)
o_min, o_max = (min(ot_scores), max(ot_scores)) if ot_scores else (0.0, 0.0)
```

All three scoring axes are normalized against the min/max within THIS run's candidate pool only.

### Examples from Reports

**BREXANOLONE/G6PD** (`Tanimoto=0.259 → normalized=1.000`):
- 0.259 was the maximum Tanimoto in that run's pool → maps to 1.000
- The score communicates "best structural similarity to an approved drug," which is technically true within this run but misleading in absolute terms (0.259 is low structural similarity)

**BREXANOLONE/G6PD** (`pChEMBL=4.18 → normalized=0.000`):
- 4.18 was the minimum pChEMBL in that run's pool → maps to 0.000
- The score communicates "no binding affinity contribution" for an affinity that is merely the weakest in this pool, not globally weak (IC50 ~66 µM, which some studies report as relevant)

### Impact

A composite score of 0.75 in one run is NOT comparable to 0.75 in another. Within-run normalization was presumably a deliberate design choice, but it means:
1. The same drug in different disease contexts can have wildly different composite scores
2. Reports that are reviewed side-by-side give misleading relative quality signals
3. The STRONG_MATCH threshold (0.70) means different things in high-density vs low-density compound pools

This is not a bug per se but warrants a disclosure statement in reports and documentation.

---

## Additional Observations (Not Main Bug Categories)

### Obs-1: Expired Pre-Signed S3 CIF URLs in Old Reports

`output/reports/Pompe_disease_CHEMBL592615.md` contains a 1,800-second pre-signed S3 URL for its CIF structure file (generated June 27). The URL is expired. The writer has fall-back logic (`[Download CIF (⚠ link may be expired)]` label) but the report was written before this label logic existed, so it shows the raw expired URL without warning.

New runs correctly use `[Download CIF](/api/structures/{local_cif_filename})` via `_cif_link()` in writer.py.

### Obs-2: PubMed 429 Too Many Requests

Some druggability literature queries fail with:
```
[pubmed] WARNING: raw abstract fetch failed for '"ENPP1" historically difficult target': 
429 Client Error: Too Many Requests
```

This causes the "historical difficulty" assessment to fall back to "insufficient signal found" — which is identical to the result when no literature exists. Reviewers cannot distinguish rate-limited (transient silence) from genuinely under-studied targets.

### Obs-3: All-Broad-Metabolic Pathway-Neighbor Pool

For "Congenital supravalvular mitral ring → ENPP1" (live batch job), ALL 10 pathway neighbors are `broad_metabolic` via vitamin metabolism (B5 pantothenate, B2 riboflavin). This produces a candidate pool with zero biologically plausible relevance to a cardiac structural defect. The system correctly stamps `pathway_specificity_note` warnings on these candidates, but the broad_metabolic candidates still flow through to scoring and may generate reports. The mechanism-direction check is the last gate against this.

### Obs-4: Known Drug/Known Indication Surfaces as Novel Repurposing

`Fabry_disease_MIGALASTAT.md` (composite 0.8500, STRONG_MATCH) proposes migalastat for Fabry disease. Migalastat (Galafold, AT1001) is the **approved pharmacological chaperone for Fabry disease** — it IS one of the approved treatments. The system's `unmet_need_score=0.045` correctly reflects low unmet need (approved treatment exists), but there is no disclosure banner stating "this drug is already the approved treatment for this exact indication." A collaborator receiving this report could reasonably conclude the pipeline is generating trivial confirmations rather than novel hypotheses.

---

## Coverage of the Six Named Bug Categories

| Named category | Status in this audit |
|---|---|
| 1. First-vs-best (wrong disease/drug identity) | Finding A, B, C directly demonstrate this: wrong entity (OBSOLETE/umbrella) processed as primary |
| 2. EFO identity mismatch | Previously fixed; verification script passes 6/6; batch cases show correct EFO resolution for non-OBSOLETE diseases |
| 3. Fail-open guards | Finding C (umbrella guard), Finding D (BioGRID key), Finding E (PubChem 503) all confirmed fail-open |
| 4. Field-dropout across pipeline stages | Finding H (UniProt cross-contamination) is new instance of same class as target_discovery_method dropout |
| 5. Score transparency / false-precision reporting | Finding G (mechanism cap invisible), Finding I (within-run normalization) |
| 6. External API silent failures | Finding D (BioGRID 401), Finding E (PubChem 503), Finding F (ClinicalTrials 502), Obs-2 (PubMed 429) |

---

## Batch Run Status at Report Time

Both batches running (`batch 9d917270` and `batch 93b1b430`, 10 jobs each). **5 of 20 jobs completed** at time of report finalization.

### Batch 1 (9d917270) — 2/10 complete

| Job | Disease | Status | Key observation |
|---|---|---|---|
| f2e99adc | **OBSOLETE: Renal cell carcinoma assoc. w/ neuroblastoma** | awaiting_review | **Findings A + H confirmed; 3 reports written, one STRONG_MATCH** |
| 23d6af10 | Autosomal recessive hereditary chronic pancreatitis | awaiting_review | BORTEZOMIB/CTRB1 INCOMPATIBLE |
| 932f330a | Multiple endocrine neoplasia type 2A → RET | running | |

### Batch 2 (93b1b430) — 3/10 complete

| Job | Disease | Status | Key observation |
|---|---|---|---|
| 38dc4b12 | Hereditary butyrylcholinesterase deficiency | awaiting_review | TACRINE INCOMPATIBLE correctly caught; 3 reports |
| 6763b0a8 | Congenital supravalvular mitral ring → ENPP1 | awaiting_review | All 10 pathway neighbors broad_metabolic; FASN wrong protein used |
| 12650932 | **NON RARE IN EUROPE: Recurrent acute pancreatitis → PRSS1** | awaiting_review | **Finding B confirmed live; BORTEZOMIB/CTRB1 wrong protein used** |
| 4565bfad | Isolated familial medullary thyroid carcinoma | running | |

### Reports Written (17 total)

| Report file | Disease category | Notes |
|---|---|---|
| OBSOLETE_Renal_cell_carcinoma_*_AFATINIB.md | **OBSOLETE disease** | STRONG_MATCH 0.7735; wrong ERBB4 structure (is MET) |
| OBSOLETE_Renal_cell_carcinoma_*_CRIZOTINIB.md | **OBSOLETE disease** | Wrong structure |
| OBSOLETE_Renal_cell_carcinoma_*_INFIGRATINIB_PHOSPHATE.md | **OBSOLETE disease** | Wrong structure (FGFR2 is MET) |
| NON_RARE_IN_EUROPE_*_BORTEZOMIB.md | **NON RARE disease** | Wrong structure (CTRB1 is PRSS1) |
| Autosomal_recessive_*_BORTEZOMIB.md | Real disease | Wrong structure (CTRB1 is PRSS1) |
| Congenital_supravalvular_*_ORLISTAT.md | Real disease | Wrong structure (FASN is ENPP1) |
| Hereditary_butyrylcholinesterase_* ×3 | Real disease | Correct target; all STRONG_MATCH |
| Developmental_anomaly_of_metabolic_origin_* ×2 | **Group of disorders** | Existing; STRONG_MATCH for umbrella disease |
| Class_I_glucose_6_phosphate_*_BREXANOLONE.md | Real disease | Mechanism cap undocumented |
| Galactokinase_deficiency_EPALRESTAT.md | Real disease | Pathway-neighbor; AKR1B1 accession uncertain |
| Glycogen_storage_disease_*_MIGLITOL.md | Real disease | GAA genetic_association; correct |
| Glycogen_storage_disease_*_MIGLUSTAT.md | Real disease | GAA genetic_association; correct |
| Fabry_disease_MIGALASTAT.md | Real disease | Migalastat IS the approved treatment |
| Pompe_disease_CHEMBL592615.md | Real disease | Expired S3 CIF URL |

---

## Recommendations (No Code Changes Applied)

Priority order for remediation before any outreach:

1. **[BLOCKING] Filter OBSOLETE and NON RARE IN EUROPE entries** in `_build_candidate_universe()` — 1 line each, but must purge existing `top_candidates.json` of contaminated entries (2 OBSOLETE + 1 NON RARE IN EUROPE confirmed).

2. **[BLOCKING] Make umbrella guard fail-closed** — if `_disorder_group_maps()` returns empty, block the disease rather than allow it through. Add a startup check that validates the umbrella map is non-empty before any sweep or manual run.

3. **[BLOCKING] Renew BioGRID API key** — the current key (`ff92c7329e6a57923488b3a5f1833fc4`) is returning 401 on every call. All interactor context in reports is absent.

4. **[BLOCKING] Fix UniProt cross-contamination** — add `"uniprot_id": c.get("uniprot_id")` to `reviewer.py reviewed.append()` (single field, same fix pattern as `target_discovery_method` dropout). Without this fix, every report where the top candidate is a pathway_neighbor contains wrong Boltz structure data.

5. **[HIGH] Document mechanism-direction cap in writer** — add a score-table row and `cap_note` for `mechanism_cap_applied` identical to the existing `unapproved_cap_applied` disclosure. Also surface the `mechanism_direction.verdict` in the Limitations section so reviewers understand why the cap was applied.

6. **[MEDIUM] PubChem 503 mitigation** — implement exponential back-off retry (3 attempts, 2s/4s/8s) before failing. The current fail-fast leaves all descriptor fields as `None` even when PubChem would succeed on retry.

7. **[LOW] Add within-run normalization disclosure** — add a note to the Composite Score Breakdown section explaining that pChEMBL, Tanimoto, and OT association are normalized relative to the current run's candidate pool, not on an absolute scale.

8. **[LOW] Add "already-approved-for-this-indication" detection** — before generating a report, check if the proposed drug is in the disease's known approved-drug list (OT or ChEMBL mechanism endpoint). If yes, label the report as a "confirmation case" rather than a novel hypothesis.

---

*Audit conducted 2026-07-25. All code references are to the state of the repository at audit time. No code was modified during this audit.*
