# Repurposing hypothesis: ALECTINIB → Multiple endocrine neoplasia type 2A
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ⚠ **Mutation-specific approval (disclosure).** ALECTINIB's approved / known indication explicitly names ALK-positive. This is a DISCLOSURE flag only: it does NOT assert that the repurposing target **RET** in **Multiple endocrine neoplasia type 2A** carries that mutation, and it does not change any score. The reviewer must judge whether the mutation-scoped precedent transfers to this indication.

## 1. Hypothesis summary
ALECTINIB is proposed as a repurposing candidate against **Multiple endocrine neoplasia type 2A** via the target **RET**. It shows a ChEMBL median pChEMBL affinity of 7.86 at assay confidence 9/9, an Open Targets target-disease association of 0.860, and a Tanimoto similarity of 0.262 to GILTERITINIB. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.7993.

_Chemist rationale:_ Alectinib is an approved drug with a median pChEMBL affinity of 7.86 against RET, measured at an assay confidence score of 9 out of 9. Its Tanimoto similarity (Morgan r2) to the nearest approved drug, gilteritinib, is 0.262, and no BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5812 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.86 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.860 |
| Tanimoto to nearest approved drug | 0.262 (GILTERITINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | ⚠ YES — indication names: ALK-positive |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 482.6, 4.77, 1, 5, 72.4, 3 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 78.8 |
| Boltz structure confidence (0-1) | 0.791 |
| Boltz binding-pose confidence (0-1) | 0.405 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.394 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/185fc2ec3ad17e26b3e70bc14d73445f.cif) |
| Boltz ADME — lipophilicity (logD) | 3.627 |
| Boltz ADME — permeability | 0.338 |
| Boltz ADME — solubility | high-risk |
| openFDA adverse-event signal (FAERS) | DISEASE PROGRESSION (186), ANAEMIA (168), FATIGUE (139), DRUG INEFFECTIVE (127), CONSTIPATION (118) |
| Prior trials for this exact drug+disease | 1 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 16343738, 24699901, 28931560, 29175871, 29348307, 33812987, 39067973
- **ChEMBL activity IDs (2):** 16655171, 16655172
- **NCT numbers (1):** NCT03194893

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.713 | 0.2140 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.235 | 0.0353 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7993** |

Weighted sum before penalty = 0.7993; penalty = 0.0000; reported composite_score = 0.7993.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.405) or predicted affinity (0.394) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.791 (complex pLDDT 0.746) and an AFDB apo mean pLDDT of 78.8; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 9 — ALECTINIB HYDROCHLORIDE, PRALSETINIB, QUIZARTINIB, REGORAFENIB, SELPERCATINIB
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
