# Repurposing hypothesis: EPALRESTAT → Galactokinase deficiency
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.6457 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Pathway-neighbor candidate.** The target **AKR1B1** was not directly linked to **Galactokinase deficiency** via Open Targets; it was discovered because it co-participates in the same Reactome pathway(s) as the primary causal gene. Open Targets association score for this target: 0.000 (0 = no direct link). The drug–target binding evidence (pChEMBL, confidence) is real; only the disease-relevance link is inferred from pathway adjacency.

## 1. Hypothesis summary
EPALRESTAT is proposed as a repurposing candidate against **Galactokinase deficiency** via the target **AKR1B1**. It shows a ChEMBL median pChEMBL affinity of 7.00 at assay confidence 9/9, an Open Targets target-disease association of 0.000, and a Tanimoto similarity of 0.177 to TOLMETIN. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.6457.

_Chemist rationale:_ Epalrestat has a median pChEMBL affinity of 6.995 against AKR1B1, measured at assay confidence score 9 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.177 to the nearest approved drug, tolmetin. No physical or genetic interactors for the target are recorded in BioGRID.

### Stage 1 prioritization scores
- **tractability_score:** 0.6246 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.00 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.000 |
| Tanimoto to nearest approved drug | 0.177 (TOLMETIN) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 319.4, 2.92, 1, 4, 57.6, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 97.2 |
| Boltz structure confidence (0-1) | 0.967 |
| Boltz binding-pose confidence (0-1) | 0.425 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.135 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/7715e166eb47094663aef99acc5c220a.cif) |
| Boltz ADME — lipophilicity (logD) | -1.262 |
| Boltz ADME — permeability | -0.485 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | HYPOGLYCAEMIA (22), HEPATIC FUNCTION ABNORMAL (17), PYREXIA (15), BLOOD GLUCOSE INCREASED (14), CARDIAC FAILURE (14) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | pathway_neighbor |

## 3. Full source citations
- **PMIDs (8):** 11139256, 11978884, 15024738, 17517531, 29893426, 32807972, 33977035, 8908517
- **ChEMBL activity IDs (12):** 1035306, 16851280, 18212152, 18934373, 19184532, 20599888, 20599898, 20599903, 22836866, 3357323, 3501367, 8023957
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.841 | 0.2523 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.000 | 0.0000 |
| Normalized Tanimoto similarity | 0.15 | 0.289 | 0.0433 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.6457** |

Weighted sum before penalty = 0.6457; penalty = 0.0000; reported composite_score = 0.6457.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.425) or predicted affinity (0.135) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.967 (complex pLDDT 0.970) and an AFDB apo mean pLDDT of 97.2; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
