# Repurposing hypothesis: BREXANOLONE → Class I glucose-6-phosphate dehydrogenase deficiency
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.4000 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
BREXANOLONE is proposed as a repurposing candidate against **Class I glucose-6-phosphate dehydrogenase deficiency** via the target **G6PD**. It shows a ChEMBL median pChEMBL affinity of 4.18 at assay confidence 9/9, an Open Targets target-disease association of 0.851, and a Tanimoto similarity of 0.259 to PRASTERONE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.4000.

_Chemist rationale:_ Brexanolone has a median pChEMBL affinity of 4.18 against G6PD, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.259 to the nearest approved drug prasterone. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5898 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 4.18 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.851 |
| Tanimoto to nearest approved drug | 0.259 (PRASTERONE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 318.5, 4.60, 1, 2, 37.3, 1 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 94.4 |
| Boltz structure confidence (0-1) | 0.770 |
| Boltz binding-pose confidence (0-1) | 0.388 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.089 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/04939a92a52c935a420ad69f6fb40da9.cif) |
| Boltz ADME — lipophilicity (logD) | 4.092 |
| Boltz ADME — permeability | 1.202 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | ACUTE LUNG INJURY (2), BRADYCARDIA (2), DIZZINESS (2), HYPOTENSION (2), MULTIPLE ORGAN DYSFUNCTION SYNDROME (2) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 10959852
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.000 | 0.0000 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 1.000 | 0.1500 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.4000** |

Weighted sum before penalty = 0.7000; penalty = 0.0000; reported composite_score = 0.4000.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.388) or predicted affinity (0.089) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.770 (complex pLDDT 0.823) and an AFDB apo mean pLDDT of 94.4; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
