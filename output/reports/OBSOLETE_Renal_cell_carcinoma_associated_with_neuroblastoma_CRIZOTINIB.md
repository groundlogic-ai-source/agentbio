# Repurposing hypothesis: CRIZOTINIB → OBSOLETE: Renal cell carcinoma associated with neuroblastoma
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ⚠ **Mutation-specific approval (disclosure).** CRIZOTINIB's approved / known indication explicitly names ROS1-positive, ALK-positive. This is a DISCLOSURE flag only: it does NOT assert that the repurposing target **MET** in **OBSOLETE: Renal cell carcinoma associated with neuroblastoma** carries that mutation, and it does not change any score. The reviewer must judge whether the mutation-scoped precedent transfers to this indication.

## 1. Hypothesis summary
CRIZOTINIB is proposed as a repurposing candidate against **OBSOLETE: Renal cell carcinoma associated with neuroblastoma** via the target **MET**. It shows a ChEMBL median pChEMBL affinity of 8.10 at assay confidence 8/9, an Open Targets target-disease association of 0.865, and a Tanimoto similarity of 0.235 to CERITINIB. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.7629.

_Chemist rationale:_ Crizotinib has a median pChEMBL affinity of 8.1 against MET, measured at an assay confidence score of 8 out of 9, and is an approved drug. Its Tanimoto similarity (Morgan r2) to the nearest approved drug, ceritinib, is 0.235, and no BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5862 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.10 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.865 |
| Tanimoto to nearest approved drug | 0.235 (CERITINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | ⚠ YES — indication names: ROS1-positive, ALK-positive |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 450.4, 5.04, 2, 5, 78.0, 5 |
| Lipinski violations / Veber pass | 1 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 79.2 |
| Boltz structure confidence (0-1) | 0.812 |
| Boltz binding-pose confidence (0-1) | 0.924 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.623 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/7f9f47241a12bddfd51d5c53a7e39238.cif) |
| Boltz ADME — lipophilicity (logD) | 1.961 |
| Boltz ADME — permeability | 0.530 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | DEATH (695), NEOPLASM PROGRESSION (461), DISEASE PROGRESSION (284), NAUSEA (268), VOMITING (246) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (3):** 2206967, 3104239, 6302080
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.743 | 0.2229 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.081 | 0.0122 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7629** |

Weighted sum before penalty = 0.7629; penalty = 0.0000; reported composite_score = 0.7629.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.924) or predicted affinity (0.623) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.812 (complex pLDDT 0.770) and an AFDB apo mean pLDDT of 79.2; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 8 — AMIVANTAMAB, BEPERMINOGENE PERPLASMID, CABOZANTINIB S-MALATE, CAPMATINIB, CAPMATINIB HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
