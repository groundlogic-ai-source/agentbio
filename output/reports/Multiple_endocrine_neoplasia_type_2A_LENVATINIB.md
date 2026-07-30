# Repurposing hypothesis: LENVATINIB → Multiple endocrine neoplasia type 2A
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 3 of 3 target(s) successfully evaluated.**

## 1. Hypothesis summary
LENVATINIB is proposed as a repurposing candidate against **Multiple endocrine neoplasia type 2A** via the target **RET**. It shows a ChEMBL median pChEMBL affinity of 8.82 at assay confidence 9/9, an Open Targets target-disease association of 0.860, and a Tanimoto similarity of 0.429 to CABOZANTINIB. Target network context (BioGRID, physical/genetic — not mechanism): STAT3, DOK6, DOK5, SRC, SHC1, PTPRF, DOK2, DOK4. The resulting composite score is 0.8357.

_Chemist rationale:_ Lenvatinib has a median pChEMBL affinity of 8.82 against RET at an assay confidence score of 9 out of 9, and it is an approved drug with a Tanimoto similarity of 0.429 to the nearest approved drug cabozantinib. BioGRID records physical or genetic interactions between RET and the following proteins: STAT3, DOK6, DOK5, SRC, SHC1, PTPRF, DOK2, and DOK4.

### Stage 1 prioritization scores
- **tractability_score:** 0.5812 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

> **Unmet-need reconciliation:** Open Targets links no approved therapy to this disease's own EFO record, yet 9 approved drug(s) with known mechanism against the selected target exist (see Target druggability context). For syndromic diseases this usually means an approved therapy treats a manifestation recorded under a different EFO node (e.g. medullary thyroid carcinoma for MEN2A). The unmet_need_score above reflects disease-level OT linkage only and may overstate unmet need — judge accordingly.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.82 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.860 |
| Tanimoto to nearest approved drug | 0.429 (CABOZANTINIB) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 426.9, 4.07, 3, 5, 115.6, 6 |
| Lipinski violations / Veber pass | 0 / yes |
| PubChem XLogP (lipophilicity) | 2.80 |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 78.8 |
| Boltz structure confidence (0-1) | 0.804 |
| Boltz binding-pose confidence (0-1) | 0.773 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.527 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/e2c44d8a5ca3591638cd5eac3acbbc19.cif) |
| Boltz ADME — lipophilicity (logD) | 2.612 |
| Boltz ADME — permeability | 0.538 |
| Boltz ADME — solubility | medium-confidence |
| openFDA adverse-event signal (FAERS) | HYPERTENSION (1732), DIARRHOEA (1581), FATIGUE (1242), DECREASED APPETITE (944), HYPOTHYROIDISM (815) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 16343738, 24699901, 28931560, 29175871, 29348307, 33812987, 39067973
- **ChEMBL activity IDs (1):** 15247904
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.831 | 0.2494 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.860 | 0.1720 |
| Normalized Tanimoto similarity | 0.15 | 0.429 | 0.0643 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8357** |

Weighted sum before penalty = 0.8357; penalty = 0.0000; reported composite_score = 0.8357.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.773) or predicted affinity (0.527) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.804 (complex pLDDT 0.759) and an AFDB apo mean pLDDT of 78.8; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 9 — ALECTINIB HYDROCHLORIDE, PRALSETINIB, QUIZARTINIB, REGORAFENIB, SELPERCATINIB, SORAFENIB TOSYLATE, SUNITINIB, SUNITINIB MALATE, VANDETANIB
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
