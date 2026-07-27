# Repurposing hypothesis: IMIPRAMINE → Xanthoma disseminatum
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.4000 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
IMIPRAMINE is proposed as a repurposing candidate against **Xanthoma disseminatum** via the target **SMPD1**. It shows a ChEMBL median pChEMBL affinity of 5.30 at assay confidence 9/9, an Open Targets target-disease association of 0.886, and a Tanimoto similarity of 0.489 to CHLORPROMAZINE. Target network context (BioGRID, physical/genetic — not mechanism): ⚠ BioGRID query failed (API error) — network context unavailable. The resulting composite score is 0.4000.

_Chemist rationale:_ Imipramine has a median pChEMBL affinity of 5.3 against SMPD1, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.489 to the nearest approved drug chlorpromazine. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5614 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 5.30 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.886 |
| Tanimoto to nearest approved drug | 0.489 (CHLORPROMAZINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 280.4, 3.88, 0, 2, 6.5, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 88.0 |
| Boltz structure confidence (0-1) | 0.796 |
| Boltz binding-pose confidence (0-1) | 0.227 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.126 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/97423350fd19a6d1cfac4666b9927d74.cif) |
| Boltz ADME — lipophilicity (logD) | 2.370 |
| Boltz ADME — permeability | 1.069 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | DRUG INEFFECTIVE (337), NAUSEA (269), FATIGUE (267), COMPLETED SUICIDE (238), HEADACHE (230) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (1):** 18412247
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.329 | 0.0986 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.886 | 0.1772 |
| Normalized Tanimoto similarity | 0.15 | 0.489 | 0.0733 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.4000** |

Weighted sum before penalty = 0.6991; penalty = 0.0000; reported composite_score = 0.4000.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.227) or predicted affinity (0.126) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.796 (complex pLDDT 0.796) and an AFDB apo mean pLDDT of 88.0; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
