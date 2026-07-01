# Repurposing hypothesis: LEVAMISOLE → Developmental anomaly of metabolic origin
## 1. Hypothesis summary
LEVAMISOLE is proposed as a repurposing candidate against **Developmental anomaly of metabolic origin** via the target **ALPL**. It shows a ChEMBL median pChEMBL affinity of 4.71 at assay confidence 9/9, an Open Targets target-disease association of 0.900, and a Tanimoto similarity of 0.968 to LEVAMISOLE HYDROCHLORIDE. Target network context (BioGRID, physical/genetic — not mechanism): FBXO6, EEF1A1, SRPK2, ALOX5, SPCS2, SEC11C, MAPKAPK2, PIK3C2B. The resulting composite score is 0.7350.

_Chemist rationale:_ Levamisole has a median pChEMBL affinity of 4.71 against ALPL, measured at assay confidence score 9, and is an approved drug with a Tanimoto similarity of 0.968 to the nearest approved drug levamisole hydrochloride. The ALPL target has eight recorded BioGRID physical or genetic interactors: FBXO6, EEF1A1, SRPK2, ALOX5, SPCS2, SEC11C, MAPKAPK2, and PIK3C2B.

### Stage 1 prioritization scores
- **tractability_score:** 0.6538 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 4.71 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.900 |
| Tanimoto to nearest approved drug | 0.968 (LEVAMISOLE HYDROCHLORIDE) |
| Approved / known drug | yes |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 204.3, 2.15, 0, 3, 15.6, 1 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 93.3 |
| Boltz structure confidence (0-1) | 0.915 |
| Boltz binding-pose confidence (0-1) | 0.207 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.099 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/1a69eb7c97d16a1580c42b8d4f345aff.cif) |
| Boltz ADME — lipophilicity (logD) | 0.877 |
| Boltz ADME — permeability | 1.171 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | TOXICITY TO VARIOUS AGENTS (272), DRUG ABUSE (152), DRUG USE DISORDER (79), SUBSTANCE USE DISORDER (79), DRUG INTERACTION (71) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (4):** 18694600, 18776917, 18792033, 2265442
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.117 | 0.0350 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 1.000 | 0.1500 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7350** |

Weighted sum before penalty = 0.7350; penalty = 0.0000; reported composite_score = 0.7350.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.207) or predicted affinity (0.099) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.915 (complex pLDDT 0.920) and an AFDB apo mean pLDDT of 93.3; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
