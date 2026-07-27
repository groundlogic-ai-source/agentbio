# Repurposing hypothesis: FORODESINE HYDROCHLORIDE → Autosomal recessive non-syndromic intellectual disability
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **PNP** was not directly linked to **Autosomal recessive non-syndromic intellectual disability** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
FORODESINE HYDROCHLORIDE is proposed as a repurposing candidate against **Autosomal recessive non-syndromic intellectual disability** via the target **PNP**. It shows a ChEMBL median pChEMBL affinity of 9.32 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.975 to FORODESINE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.7415.

_Chemist rationale:_ Forodesine hydrochloride has a median pChEMBL affinity of 9.32 against PNP, measured at an assay confidence score of 9 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.975 to the nearest approved drug, forodesine. No physical or genetic interactors for the target are recorded in BioGRID.

### Stage 1 prioritization scores
- **tractability_score:** 0.5692 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 9.32 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.975 (FORODESINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 302.7, -1.60, 6, 6, 134.3, 2 |
| Lipinski violations / Veber pass | 1 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 93.3 |
| Boltz structure confidence (0-1) | 0.940 |
| Boltz binding-pose confidence (0-1) | 0.675 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.201 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/18c239fe1f29b5d2f1626844706d43c2.cif) |
| Boltz ADME — lipophilicity (logD) | -0.799 |
| Boltz ADME — permeability | -0.114 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | MALIGNANT NEOPLASM PROGRESSION (13), NEUTROPHIL COUNT DECREASED (4), STOMATITIS (4), BLOOD LACTATE DEHYDROGENASE INCREASED (3), DEATH (3) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 26191710
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.805 | 0.2415 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 1.000 | 0.1500 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7415** |

Weighted sum before penalty = 0.7415; penalty = 0.0000; reported composite_score = 0.7415.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.675) or predicted affinity (0.201) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.940 (complex pLDDT 0.927) and an AFDB apo mean pLDDT of 93.3; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
