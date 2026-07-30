# Repurposing hypothesis: PINDOLOL → Autosomal recessive hereditary chronic pancreatitis
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 5 of 5 target(s) successfully evaluated.**

> ℹ **Pathway-neighbor candidate.** The target **ADRB2** was not directly linked to **Autosomal recessive hereditary chronic pancreatitis** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
PINDOLOL is proposed as a repurposing candidate against **Autosomal recessive hereditary chronic pancreatitis** via the target **ADRB2**. It shows a ChEMBL median pChEMBL affinity of 9.40 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.644 to PROPRANOLOL. Target network context (BioGRID, physical/genetic — not mechanism): SERPINB8, SERPINF2, DDX5, ALB, LGR4, CEP250, STK38L, KRT1. The resulting composite score is 0.7209.

_Chemist rationale:_ Pindolol has a median pChEMBL affinity of 9.4 against ADRB2 measured at assay confidence score 9, and a Tanimoto similarity of 0.644 to the nearest approved drug propranolol. BioGRID records physical or genetic interactions between ADRB2 and the proteins GOPC, KCNJ1, PDZD3, SLC9A3R1, STX1A, HSPA8, DNAJB1, and DNAJA1.

### Stage 1 prioritization scores
- **tractability_score:** 0.5829 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 9.40 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.644 (PROPRANOLOL) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 248.3, 1.91, 3, 3, 57.3, 6 |
| Lipinski violations / Veber pass | 0 / yes |
| PubChem XLogP (lipophilicity) | 1.80 |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 79.1 |
| Boltz structure confidence (0-1) | 0.817 |
| Boltz binding-pose confidence (0-1) | 0.999 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.750 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/d39c3a9944ff7beb210956353c16a160.cif) |
| Boltz ADME — lipophilicity (logD) | -0.063 |
| Boltz ADME — permeability | 0.819 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | NAUSEA (46), FATIGUE (45), DYSPNOEA (42), URINARY TRACT INFECTION (42), DIZZINESS (41) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (3):** 1985057, 7725987, 7725988
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.914 | 0.2743 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.644 | 0.0967 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7209** |

Weighted sum before penalty = 0.7209; penalty = 0.0000; reported composite_score = 0.7209.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.999) or predicted affinity (0.750) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.817 (complex pLDDT 0.774) and an AFDB apo mean pLDDT of 79.1; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
