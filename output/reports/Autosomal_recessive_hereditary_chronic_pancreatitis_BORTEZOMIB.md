# Repurposing hypothesis: BORTEZOMIB → Autosomal recessive hereditary chronic pancreatitis
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.4000 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **CTRB1** was not directly linked to **Autosomal recessive hereditary chronic pancreatitis** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

> ⚠ **Mutation-specific approval (disclosure).** BORTEZOMIB's approved / known indication explicitly names BCR-ABL. This is a DISCLOSURE flag only: it does NOT assert that the repurposing target **CTRB1** in **Autosomal recessive hereditary chronic pancreatitis** carries that mutation, and it does not change any score. The reviewer must judge whether the mutation-scoped precedent transfers to this indication.

## 1. Hypothesis summary
BORTEZOMIB is proposed as a repurposing candidate against **Autosomal recessive hereditary chronic pancreatitis** via the target **CTRB1**. It shows a ChEMBL median pChEMBL affinity of 6.50 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.087 to ACARBOSE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.4000.

_Chemist rationale:_ Bortezomib has a median pChEMBL affinity of 6.5 against CTRB1 at assay confidence score 9, and it is an approved drug with a Tanimoto similarity of 0.087 to the nearest approved drug acarbose. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5829 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 6.50 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.087 (ACARBOSE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | ⚠ YES — indication names: BCR-ABL |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 384.2, 0.36, 4, 6, 124.4, 9 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 92.1 |
| Boltz structure confidence (0-1) | 0.957 |
| Boltz binding-pose confidence (0-1) | 0.942 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.421 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/701eae66821028bad4678c1e97bed304.cif) |
| Boltz ADME — lipophilicity (logD) | -0.095 |
| Boltz ADME — permeability | -0.118 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | PLASMA CELL MYELOMA (5209), OFF LABEL USE (3116), DRUG INEFFECTIVE (2259), NEUROPATHY PERIPHERAL (1834), THROMBOCYTOPENIA (1783) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 89211
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 1.000 | 0.1500 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.4000** |

Weighted sum before penalty = 0.8000; penalty = 0.0000; reported composite_score = 0.4000.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.942) or predicted affinity (0.421) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.957 (complex pLDDT 0.950) and an AFDB apo mean pLDDT of 92.1; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
