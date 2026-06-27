# Repurposing hypothesis: CHEMBL592615 → Pompe disease
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.6992 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

## 1. Hypothesis summary
CHEMBL592615 is proposed as a repurposing candidate against **Pompe disease** via the target **PIK3CA**. It shows a ChEMBL median pChEMBL affinity of 9.40 at assay confidence 8/9, an Open Targets target-disease association of 0.259, and a Tanimoto similarity of 0.000 to no approved analog in the set. Target network context (BioGRID, physical/genetic — not mechanism): PIK3R3, ATR, RASGRP3, HRAS, RASD2, LCK, NOTCH1, ADAP1. The resulting composite score is 0.6992.

_Chemist rationale:_ CHEMBL592615 has a median pChEMBL affinity of 9.4 against PIK3CA recorded at an assay confidence score of 8 out of 9, and it is not an approved drug with no nearest approved structural analog identified in the candidate set. PIK3CA has eight BioGRID-listed physical or genetic interactors: PIK3R3, ATR, RASGRP3, HRAS, RASD2, LCK, NOTCH1, and ADAP1.

### Stage 1 prioritization scores
- **tractability_score:** 0.7233 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.3500 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 9.40 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.259 |
| Tanimoto to nearest approved drug | 0.000 (none in set) |
| Approved / known drug | no |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 575.7, 2.15, 3, 10, 137.1, 9 |
| Lipinski violations / Veber pass | 1 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 92.4 |
| Boltz structure confidence (0-1) | 0.920 |
| Boltz binding-pose confidence (0-1) | 0.952 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.612 |
| Boltz predicted structure (CIF) | https://boltz-platform-prod-compute-api-storage.s3.us-east-1.amazonaws.com/org/311afb47-841a-494f-ab03-1ea7fa58b70a/workspace/ws_ai6T67fapbF5DEbF8qVZ/prediction/sab_pred_Rv2mxNyWgi4jL9OyWL90/output/public/sample_0_predicted_structure.cif?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=REDACTED&X-Amz-Date=20260627T035513Z&X-Amz-Expires=1800&X-Amz-Security-Token=REDACTED&X-Amz-Signature=REDACTED&X-Amz-SignedHeaders=host&x-id=GetObject |
| Boltz ADME — lipophilicity (logD) | 1.298 |
| Boltz ADME — permeability | 0.356 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | none reported |
| Prior trials for this exact drug+disease | 0 |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 3120625
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.571 | 0.1714 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.000 | 0.0000 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty)** | | | **0.6992** |

Weighted sum before penalty = 0.6992; penalty = 0.0000; reported composite_score = 0.6992.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.952) or predicted affinity (0.612) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.920 (complex pLDDT 0.905) and an AFDB apo mean pLDDT of 92.4; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.
