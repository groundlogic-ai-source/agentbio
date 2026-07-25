# Repurposing hypothesis: INFIGRATINIB PHOSPHATE → OBSOLETE: Renal cell carcinoma associated with neuroblastoma
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **FGFR2** was not directly linked to **OBSOLETE: Renal cell carcinoma associated with neuroblastoma** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
INFIGRATINIB PHOSPHATE is proposed as a repurposing candidate against **OBSOLETE: Renal cell carcinoma associated with neuroblastoma** via the target **FGFR2**. It shows a ChEMBL median pChEMBL affinity of 8.70 at assay confidence 8/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.939 to INFIGRATINIB. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.7305.

_Chemist rationale:_ Infigratinib phosphate has a median pChEMBL affinity of 8.7 against FGFR2, measured at an assay confidence score of 8 out of 9, and is recorded as an approved or known drug with a Tanimoto similarity of 0.939 to the nearest approved drug, infigratinib. No physical or genetic interactors for the target are recorded in BioGRID.

### Stage 1 prioritization scores
- **tractability_score:** 0.5862 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.70 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.939 (INFIGRATINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 658.5, 4.43, 5, 9, 172.8, 8 |
| Lipinski violations / Veber pass | 1 / no |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 79.2 |
| Boltz structure confidence (0-1) | 0.801 |
| Boltz binding-pose confidence (0-1) | 0.775 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.482 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/672cf97e45b3b755ae47ee1bc4a1effd.cif) |
| Boltz ADME — lipophilicity (logD) | 0.060 |
| Boltz ADME — permeability | 0.185 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | none reported |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (3):** 6386438, 6386453, 6386462
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.871 | 0.2611 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.944 | 0.1416 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7305** |

Weighted sum before penalty = 0.7305; penalty = 0.0000; reported composite_score = 0.7305.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.775) or predicted affinity (0.482) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.801 (complex pLDDT 0.761) and an AFDB apo mean pLDDT of 79.2; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 8 — AMIVANTAMAB, BEPERMINOGENE PERPLASMID, CABOZANTINIB S-MALATE, CAPMATINIB, CAPMATINIB HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
