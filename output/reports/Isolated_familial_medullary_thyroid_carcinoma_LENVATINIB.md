# Repurposing hypothesis: LENVATINIB → Isolated familial medullary thyroid carcinoma
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
LENVATINIB is proposed as a repurposing candidate against **Isolated familial medullary thyroid carcinoma** via the target **RET**. It shows a ChEMBL median pChEMBL affinity of 8.82 at assay confidence 9/9, an Open Targets target-disease association of 0.860, and a Tanimoto similarity of 0.429 to CABOZANTINIB. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.9375.

_Chemist rationale:_ Lenvatinib has a median pChEMBL affinity of 8.82 against RET at assay confidence score 9, and is an approved drug with a Tanimoto similarity of 0.429 to the nearest approved drug cabozantinib. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5812 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.82 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.860 |
| Tanimoto to nearest approved drug | 0.429 (CABOZANTINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 426.9, 4.07, 3, 5, 115.6, 6 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 78.8 |
| Boltz structure confidence (0-1) | 0.787 |
| Boltz binding-pose confidence (0-1) | 0.829 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.523 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/e2c44d8a5ca3591638cd5eac3acbbc19.cif) |
| Boltz ADME — lipophilicity (logD) | 2.612 |
| Boltz ADME — permeability | 0.538 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | HYPERTENSION (1732), DIARRHOEA (1581), MALIGNANT NEOPLASM PROGRESSION (1574), FATIGUE (1242), DECREASED APPETITE (944) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 15247904
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.583 | 0.0875 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.9375** |

Weighted sum before penalty = 0.9375; penalty = 0.0000; reported composite_score = 0.9375.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.829) or predicted affinity (0.523) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.787 (complex pLDDT 0.737) and an AFDB apo mean pLDDT of 78.8; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 9 — ALECTINIB HYDROCHLORIDE, PRALSETINIB, QUIZARTINIB, REGORAFENIB, SELPERCATINIB
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
