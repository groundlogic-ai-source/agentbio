# Repurposing hypothesis: PHYSOSTIGMINE SALICYLATE → Hereditary butyrylcholinesterase deficiency
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
PHYSOSTIGMINE SALICYLATE is proposed as a repurposing candidate against **Hereditary butyrylcholinesterase deficiency** via the target **BCHE**. It shows a ChEMBL median pChEMBL affinity of 7.80 at assay confidence 8/9, an Open Targets target-disease association of 0.805, and a Tanimoto similarity of 0.786 to PHYSOSTIGMINE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.9415.

_Chemist rationale:_ Physostigmine salicylate has a median pChEMBL affinity of 7.8 against BCHE, measured at an assay confidence score of 8 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.786 to the nearest approved drug, physostigmine. No physical or genetic interactors for the target are recorded in BioGRID.

### Stage 1 prioritization scores
- **tractability_score:** 0.5854 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.80 |
| Assay confidence score (0-9) | 8 |
| Open Targets association score | 0.805 |
| Tanimoto to nearest approved drug | 0.786 (PHYSOSTIGMINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 413.5, 2.86, 3, 6, 102.3, 2 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 93.4 |
| Boltz structure confidence (0-1) | 0.886 |
| Boltz binding-pose confidence (0-1) | 0.552 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.267 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/79178d9a9b7fa23c115783e4b6078e91.cif) |
| Boltz ADME — lipophilicity (logD) | -0.380 |
| Boltz ADME — permeability | 0.115 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | ANTICHOLINERGIC SYNDROME (2), CONFUSIONAL STATE (2), DELIRIUM (2), DRUG ABUSE (2), DRUG INTERACTION (2) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (6):** 18075469, 29631548, 30593143, 38420074, 39814397, 40778538
- **ChEMBL activity IDs (1):** 833027
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 0.889 | 0.1778 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.758 | 0.1137 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.9415** |

Weighted sum before penalty = 0.9415; penalty = 0.0000; reported composite_score = 0.9415.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.552) or predicted affinity (0.267) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.886 (complex pLDDT 0.914) and an AFDB apo mean pLDDT of 93.4; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 4 — PROPANIDID, RIVASTIGMINE, RIVASTIGMINE TARTRATE, TACRINE HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
