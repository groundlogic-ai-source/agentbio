# Repurposing hypothesis: NICARDIPINE → Benign recurrent intrahepatic cholestasis type 2
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 4 of 4 target(s) successfully evaluated.**

> ⚠ **DILI/safety-screening target (disclosure).** **ABCB11** is a well-known pharmaceutical safety-profiling target. Drug companies routinely measure IC50/Ki of candidate drugs against ABCB11 to detect DRUG-INDUCED LIVER INJURY (DILI) or cardiac toxicity risk *before* regulatory submission — not because those drugs are intended to treat diseases caused by ABCB11 dysfunction. The pChEMBL value in this report may therefore come from a **safety-screening assay** (recording a toxicity liability) rather than a therapeutic-intent binding study. Verify the source assay context in ChEMBL before treating this binding data as evidence of a therapeutic mechanism. This disclosure does not affect any score.

## 1. Hypothesis summary
NICARDIPINE is proposed as a repurposing candidate against **Benign recurrent intrahepatic cholestasis type 2** via the target **ABCB11**. It shows a ChEMBL median pChEMBL affinity of 5.10 at assay confidence 9/9, an Open Targets target-disease association of 0.858, and a Tanimoto similarity of 0.612 to NIMODIPINE. Target network context (BioGRID, physical/genetic — not mechanism): NR4A1, LY6D, AP2A1, DDIT4L, HIST2H3PS2, NUDCD2, PARK2, SRGN. The resulting composite score is 0.7034.

_Chemist rationale:_ Nicardipine has a median pChEMBL affinity of 5.1 against ABCB11, measured at assay confidence score 9, and is an approved drug with a Tanimoto similarity of 0.612 to the nearest approved drug nimodipine. The target ABCB11 has the following BioGRID physical/genetic interactors on record: NR4A1, LY6D, AP2A1, DDIT4L, HIST2H3PS2, NUDCD2, PARK2, and SRGN.

### Stage 1 prioritization scores
- **tractability_score:** 0.5908 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 5.10 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.858 |
| Tanimoto to nearest approved drug | 0.612 (NIMODIPINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 479.5, 3.68, 1, 8, 111.0, 9 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 83.1 |
| Boltz structure confidence (0-1) | n/a |
| Boltz binding-pose confidence (0-1) | n/a |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | n/a |
| Boltz predicted structure (CIF) | n/a |
| Boltz ADME — lipophilicity (logD) | n/a |
| Boltz ADME — permeability | n/a |
| Boltz ADME — solubility | n/a |
| openFDA adverse-event signal (FAERS) | ACUTE KIDNEY INJURY (311), PREMATURE BABY (238), HYPOTENSION (230), OFF LABEL USE (230), DRUG INEFFECTIVE (222) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 15300568, 18853996, 26223708, 29507376, 30899697, 32647738, 34013234
- **ChEMBL activity IDs (4):** 18051765, 18052008, 18129012, 22396460
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.300 | 0.0900 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.858 | 0.1716 |
| Normalized Tanimoto similarity | 0.15 | 0.612 | 0.0918 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.7034** |

Weighted sum before penalty = 0.7034; penalty = 0.0000; reported composite_score = 0.7034.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (n/a) or predicted affinity (n/a) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of n/a (complex pLDDT n/a) and an AFDB apo mean pLDDT of 83.1; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **No approved drug currently exists with a known mechanism against this target** (ChEMBL mechanism endpoint, Homo sapiens only).
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
