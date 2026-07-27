# Repurposing hypothesis: DEOXYCHOLIC ACID → Benign recurrent intrahepatic cholestasis type 2
> **NOTE:** This candidate did NOT meet the STRONG_MATCH threshold (composite 0.5680 < 0.70). It is included as the highest-ranked hypothesis for review; treat it accordingly.

> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 4 of 4 target(s) successfully evaluated.**

## 1. Hypothesis summary
DEOXYCHOLIC ACID is proposed as a repurposing candidate against **Benign recurrent intrahepatic cholestasis type 2** via the target **SLC10A2**. It shows a ChEMBL median pChEMBL affinity of 5.20 at assay confidence 8/9, an Open Targets target-disease association of 0.272, and a Tanimoto similarity of 0.610 to CHENODIOL. Target network context (BioGRID, physical/genetic — not mechanism): PPIE, C3ORF52, UPK1B, NRM, IFITM3, TEX264, EFNA5, TMEM222. The resulting composite score is 0.5680.

_Chemist rationale:_ Deoxycholic acid has a median pChEMBL affinity of 5.2 against SLC10A2, measured at an assay confidence score of 8 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.610 to the nearest approved drug, chenodiol. The target SLC10A2 has eight recorded BioGRID physical or genetic interactors: PPIE, C3ORF52, UPK1B, NRM, IFITM3, TEX264, EFNA5, and TMEM222.

### Stage 1 prioritization scores
- **tractability_score:** 0.5908 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 5.20 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.272 |
| Tanimoto to nearest approved drug | 0.610 (CHENODIOL) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 392.6, 4.48, 3, 3, 77.8, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 82.7 |
| Boltz structure confidence (0-1) | n/a |
| Boltz binding-pose confidence (0-1) | n/a |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | n/a |
| Boltz predicted structure (CIF) | n/a |
| Boltz ADME — lipophilicity (logD) | n/a |
| Boltz ADME — permeability | n/a |
| Boltz ADME — solubility | n/a |
| openFDA adverse-event signal (FAERS) | ACUTE KIDNEY INJURY (7), SWELLING (7), DRUG INEFFECTIVE (5), PRODUCT USE IN UNAPPROVED INDICATION (4), BIPOLAR DISORDER (3) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 15300568, 18853996, 26223708, 29507376, 30899697, 32647738, 34013234
- **ChEMBL activity IDs (2):** 11003216, 11003217
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.314 | 0.0943 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 0.272 | 0.0544 |
| Normalized Tanimoto similarity | 0.15 | 0.610 | 0.0915 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.5680** |

Weighted sum before penalty = 0.5680; penalty = 0.0000; reported composite_score = 0.5680.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (n/a) or predicted affinity (n/a) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of n/a (complex pLDDT n/a) and an AFDB apo mean pLDDT of 82.7; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 4 — MARALIXIBAT, MARALIXIBAT CHLORIDE, ODEVIXIBAT, ODEVIXIBAT SESQUIHYDRATE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
