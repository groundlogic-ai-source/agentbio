# Repurposing hypothesis: EBASTINE → Tuberous sclerosis complex
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.5696 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 5 of 5 target(s) successfully evaluated.**

> ℹ **Pathway-neighbor candidate.** The target **PRKAA2** was not directly linked to **Tuberous sclerosis complex** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
EBASTINE is proposed as a repurposing candidate against **Tuberous sclerosis complex** via the target **PRKAA2**. It shows a ChEMBL median pChEMBL affinity of 7.58 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.155 to ENTRECTINIB. Target network context (BioGRID, physical/genetic — not mechanism): TGFBR1, YY1, MTOR, RPTOR, DHFR, RYR1, RYR3, ITPR1. The resulting composite score is 0.5696.

_Chemist rationale:_ Ebastine has a median pChEMBL affinity of 7.58 against PRKAA2 at an assay confidence score of 9 out of 9, and it is an approved or known drug with a Tanimoto similarity of 0.155 to the nearest approved drug, entrectinib. The PRKAA2 target has eight recorded BioGRID interactors—MSN, YWHAZ, EZR, ACTA1, AKT1, NEFL, RHEB, and RPS6KA1.

### Stage 1 prioritization scores
- **tractability_score:** 0.6632 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.0410 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.58 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.155 (ENTRECTINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 469.7, 7.22, 0, 3, 29.5, 9 |
| Lipinski violations / Veber pass | 1 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 76.7 |
| Boltz structure confidence (0-1) | 0.724 |
| Boltz binding-pose confidence (0-1) | 0.153 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.240 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/e9309a1002f3befb170a5998446ebd00.cif) |
| Boltz ADME — lipophilicity (logD) | 4.933 |
| Boltz ADME — permeability | 0.557 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | URTICARIA (342), PRURITUS (301), FATIGUE (270), COVID-19 (263), OFF LABEL USE (245) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 25872880
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.654 | 0.1963 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.155 | 0.0233 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.5696** |

Weighted sum before penalty = 0.5696; penalty = 0.0000; reported composite_score = 0.5696.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.153) or predicted affinity (0.240) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.724 (complex pLDDT 0.686) and an AFDB apo mean pLDDT of 76.7; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 5 — EVEROLIMUS, PIMECROLIMUS, SIROLIMUS, TACROLIMUS, TEMSIROLIMUS
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
