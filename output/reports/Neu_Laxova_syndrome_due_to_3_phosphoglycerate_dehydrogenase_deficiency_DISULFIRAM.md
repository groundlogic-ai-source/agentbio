# Repurposing hypothesis: DISULFIRAM → Neu-Laxova syndrome due to 3-phosphoglycerate dehydrogenase deficiency
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.4000 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
DISULFIRAM is proposed as a repurposing candidate against **Neu-Laxova syndrome due to 3-phosphoglycerate dehydrogenase deficiency** via the target **PHGDH**. It shows a ChEMBL median pChEMBL affinity of 6.23 at assay confidence 9/9, an Open Targets target-disease association of 0.796, and a Tanimoto similarity of 0.000 to no approved analog in the set. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.4000.

_Chemist rationale:_ Disulfiram has a median pChEMBL affinity of 6.23 against PHGDH, recorded at an assay confidence score of 9 out of 9. Disulfiram is an approved drug with no nearest approved structural analog in the candidate set and no BioGRID interactors listed for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5774 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 6.23 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.796 |
| Tanimoto to nearest approved drug | 0.000 (none in set) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 296.6, 3.62, 0, 4, 6.5, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 92.9 |
| Boltz structure confidence (0-1) | 0.869 |
| Boltz binding-pose confidence (0-1) | 0.627 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.224 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/6043fd17c13b13e8f193281363aa98c2.cif) |
| Boltz ADME — lipophilicity (logD) | 2.254 |
| Boltz ADME — permeability | 0.431 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | DRUG INTERACTION (129), OFF LABEL USE (124), FATIGUE (86), DRUG INEFFECTIVE (68), TOXICITY TO VARIOUS AGENTS (67) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (6):** 24836451, 25152457, 25913727, 26960553, 30348640, 32077105
- **ChEMBL activity IDs (1):** 24998091
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.000 | 0.0000 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.4000** |

Weighted sum before penalty = 0.8500; penalty = 0.0000; reported composite_score = 0.4000.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.627) or predicted affinity (0.224) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.869 (complex pLDDT 0.882) and an AFDB apo mean pLDDT of 92.9; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
