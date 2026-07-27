# Repurposing hypothesis: DIETHYLSTILBESTROL → Ferroportin disease
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.6551 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **ABCG2** was not directly linked to **Ferroportin disease** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
DIETHYLSTILBESTROL is proposed as a repurposing candidate against **Ferroportin disease** via the target **ABCG2**. It shows a ChEMBL median pChEMBL affinity of 6.30 at assay confidence 8/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.163 to ESTRONE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.6551.

_Chemist rationale:_ Diethylstilbestrol has a median pChEMBL affinity of 6.3 against ABCG2, measured at an assay confidence score of 8 out of 9, and is an approved or known drug with no BioGRID physical or genetic interactors recorded for the target. Its Tanimoto similarity (Morgan r2) to the nearest approved drug, estrone, is 0.163.

### Stage 1 prioritization scores
- **tractability_score:** 0.5674 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 6.30 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.163 (ESTRONE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 268.4, 4.83, 2, 2, 40.5, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 80.2 |
| Boltz structure confidence (0-1) | 0.755 |
| Boltz binding-pose confidence (0-1) | 0.546 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.203 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/71b9357b244ed21e5aa169b865eed5ce.cif) |
| Boltz ADME — lipophilicity (logD) | 4.255 |
| Boltz ADME — permeability | 1.157 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | NAUSEA (20), FATIGUE (12), AGITATION (11), DEATH (11), INSOMNIA (11) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (6):** 14757427, 20691492, 38959016, 40225168, 40603789, 41855270
- **ChEMBL activity IDs (1):** 11000877
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.182 | 0.0274 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.6551** |

Weighted sum before penalty = 0.6551; penalty = 0.0000; reported composite_score = 0.6551.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.546) or predicted affinity (0.203) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.755 (complex pLDDT 0.776) and an AFDB apo mean pLDDT of 80.2; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
