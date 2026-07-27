# Repurposing hypothesis: SORAFENIB → Isolated familial medullary thyroid carcinoma
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ⚠ **Mutation-specific approval (disclosure).** SORAFENIB's approved / known indication explicitly names BCR-ABL. This is a DISCLOSURE flag only: it does NOT assert that the repurposing target **RET** in **Isolated familial medullary thyroid carcinoma** carries that mutation, and it does not change any score. The reviewer must judge whether the mutation-scoped precedent transfers to this indication.

## 1. Hypothesis summary
SORAFENIB is proposed as a repurposing candidate against **Isolated familial medullary thyroid carcinoma** via the target **RET**. It shows a ChEMBL median pChEMBL affinity of 8.05 at assay confidence 9/9, an Open Targets target-disease association of 0.860, and a Tanimoto similarity of 0.291 to CABOZANTINIB. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.8255.

_Chemist rationale:_ Sorafenib has a median pChEMBL affinity of 8.05 against RET, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.291 to the nearest approved drug cabozantinib. No BioGRID physical or genetic interactors are recorded for the RET target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5812 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.05 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.860 |
| Tanimoto to nearest approved drug | 0.291 (CABOZANTINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | ⚠ YES — indication names: BCR-ABL |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 464.8, 5.55, 3, 4, 92.3, 5 |
| Lipinski violations / Veber pass | 1 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 78.8 |
| Boltz structure confidence (0-1) | 0.802 |
| Boltz binding-pose confidence (0-1) | 0.804 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.511 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/21446eda9cae1523ba5dfdb52b7226ce.cif) |
| Boltz ADME — lipophilicity (logD) | 4.305 |
| Boltz ADME — permeability | 0.758 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | DIARRHOEA (675), OFF LABEL USE (630), FATIGUE (458), PALMAR-PLANTAR ERYTHRODYSAESTHESIA SYNDROME (432), NAUSEA (366) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (3):** 15247902, 3446178, 9533009
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.770 | 0.2310 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.296 | 0.0445 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8255** |

Weighted sum before penalty = 0.8255; penalty = 0.0000; reported composite_score = 0.8255.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.804) or predicted affinity (0.511) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.802 (complex pLDDT 0.755) and an AFDB apo mean pLDDT of 78.8; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 9 — ALECTINIB HYDROCHLORIDE, PRALSETINIB, QUIZARTINIB, REGORAFENIB, SELPERCATINIB
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
