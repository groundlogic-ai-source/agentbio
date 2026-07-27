# Repurposing hypothesis: MELAGATRAN → Ligneous conjunctivitis
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.6627 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
MELAGATRAN is proposed as a repurposing candidate against **Ligneous conjunctivitis** via the target **PLG**. It shows a ChEMBL median pChEMBL affinity of 5.75 at assay confidence 9/9, an Open Targets target-disease association of 0.812, and a Tanimoto similarity of 0.215 to PENTAMIDINE. Target network context (BioGRID, physical/genetic — not mechanism): ⚠ BioGRID query failed (API error) — network context unavailable. The resulting composite score is 0.6627.

_Chemist rationale:_ Melagatran has a median pChEMBL affinity of 5.75 against PLG, measured at assay confidence score 9 out of 9, and is an approved or known drug. Its Tanimoto similarity (Morgan r2) to the nearest approved drug, pentamidine, is 0.215, and no BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5605 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 5.75 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.812 |
| Tanimoto to nearest approved drug | 0.215 (PENTAMIDINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 429.5, 0.81, 5, 5, 148.6, 9 |
| Lipinski violations / Veber pass | 0 / no |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 82.8 |
| Boltz structure confidence (0-1) | 0.880 |
| Boltz binding-pose confidence (0-1) | 0.961 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.246 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/d168aa8cff4c5ec638a36cb0cfd48acc.cif) |
| Boltz ADME — lipophilicity (logD) | -0.454 |
| Boltz ADME — permeability | -0.619 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | ABDOMINAL PAIN (1), GASTRIC ULCER PERFORATION (1), VOMITING (1) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 17900274, 30058940, 37612758, 37818495, 39081406, 40929001, 41408135
- **ChEMBL activity IDs (3):** 13414439, 1716732, 19054715
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.393 | 0.1179 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.812 | 0.1625 |
| Normalized Tanimoto similarity | 0.15 | 0.215 | 0.0323 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.6627** |

Weighted sum before penalty = 0.6627; penalty = 0.0000; reported composite_score = 0.6627.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.961) or predicted affinity (0.246) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.880 (complex pLDDT 0.854) and an AFDB apo mean pLDDT of 82.8; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 6 — AMINOCAPROIC ACID, ANISTREPLASE, APROTININ, DEFIBROTIDE SODIUM, STREPTOKINASE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
