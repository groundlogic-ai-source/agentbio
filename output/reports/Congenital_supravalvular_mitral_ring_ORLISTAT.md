# Repurposing hypothesis: ORLISTAT → Congenital supravalvular mitral ring
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.6330 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **FASN** was not directly linked to **Congenital supravalvular mitral ring** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
ORLISTAT is proposed as a repurposing candidate against **Congenital supravalvular mitral ring** via the target **FASN**. It shows a ChEMBL median pChEMBL affinity of 5.94 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.111 to TAURURSODIOL. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.6330.

_Chemist rationale:_ Orlistat has a median pChEMBL affinity of 5.94 against FASN, measured at assay confidence score 9 out of 9, and is an approved drug. Its Tanimoto similarity (Morgan r2) to the nearest approved drug, Taurursodiol, is 0.111, and no BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5841 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 5.94 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.111 (TAURURSODIOL) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 495.8, 6.88, 1, 5, 81.7, 23 |
| Lipinski violations / Veber pass | 1 / no |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 90.7 |
| Boltz structure confidence (0-1) | 0.826 |
| Boltz binding-pose confidence (0-1) | 0.131 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.209 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/496212f8664d8d3e1b3d588d5c5cdce6.cif) |
| Boltz ADME — lipophilicity (logD) | 5.533 |
| Boltz ADME — permeability | 1.023 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | DRUG INTERACTION (137), DIARRHOEA (129), VOMITING (103), NAUSEA (101), FATIGUE (87) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (2):** 12079782, 18094317
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.931 | 0.2792 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.025 | 0.0038 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.6330** |

Weighted sum before penalty = 0.6330; penalty = 0.0000; reported composite_score = 0.6330.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.131) or predicted affinity (0.209) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.826 (complex pLDDT 0.854) and an AFDB apo mean pLDDT of 90.7; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
