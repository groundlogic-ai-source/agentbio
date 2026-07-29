# Repurposing hypothesis: VARDENAFIL → Idiopathic pulmonary arterial hypertension
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 5 of 5 target(s) successfully evaluated.**

## 1. Hypothesis summary
VARDENAFIL is proposed as a repurposing candidate against **Idiopathic pulmonary arterial hypertension** via the target **PDE5A**. It shows a ChEMBL median pChEMBL affinity of 9.15 at assay confidence 9/9, an Open Targets target-disease association of 0.900, and a Tanimoto similarity of 0.623 to VARDENAFIL HYDROCHLORIDE. Target network context (BioGRID, physical/genetic — not mechanism): AIPL1, MIOS, FGFR1OP, BLM, SUPT5H, WDR24, PATZ1, FKBP15. The resulting composite score is 0.8871.

_Chemist rationale:_ Vardenafil has a median pChEMBL affinity of 9.15 against PDE5A, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.623 to the nearest approved drug, vardenafil hydrochloride. PDE5A has eight recorded BioGRID physical or genetic interactors: AIPL1, MIOS, FGFR1OP, BLM, SUPT5H, WDR24, PATZ1, and FKBP15.

### Stage 1 prioritization scores
- **tractability_score:** 0.6183 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.0216 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 9.15 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.900 |
| Tanimoto to nearest approved drug | 0.623 (VARDENAFIL HYDROCHLORIDE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 488.6, 2.07, 1, 7, 112.9, 8 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 82.0 |
| Boltz structure confidence (0-1) | 0.812 |
| Boltz binding-pose confidence (0-1) | 0.933 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.597 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/69708ded65c5d35c4bfeda0da735daa4.cif) |
| Boltz ADME — lipophilicity (logD) | 2.425 |
| Boltz ADME — permeability | 0.246 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | DRUG INEFFECTIVE (47), DIZZINESS (45), HEADACHE (44), HYPOTENSION (43), DRUG INTERACTION (38) |
| Prior trials for this exact drug+disease | 7 |
| Target discovery method | pharmacological_precedent_via_parent_umbrella |

## 3. Full source citations
- **PMIDs (5):** 16980375, 17030688, 17396193, 33596129, 34981661
- **ChEMBL activity IDs (3):** 1506645, 1520505, 574531
- **NCT numbers (7):** NCT00705588, NCT00718952, NCT01649739, NCT04266197, NCT05343637, NCT05567367, NCT07536880

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.879 | 0.2636 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.900 | 0.1800 |
| Normalized Tanimoto similarity | 0.15 | 0.623 | 0.0935 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8871** |

Weighted sum before penalty = 0.8871; penalty = 0.0000; reported composite_score = 0.8871.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.933) or predicted affinity (0.597) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.812 (complex pLDDT 0.774) and an AFDB apo mean pLDDT of 82.0; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 7 — AVANAFIL, DIPYRIDAMOLE, IBUDILAST, PENTOXIFYLLINE, SILDENAFIL CITRATE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
