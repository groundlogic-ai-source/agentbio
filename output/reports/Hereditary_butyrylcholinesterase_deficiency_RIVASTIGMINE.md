# Repurposing hypothesis: RIVASTIGMINE → Hereditary butyrylcholinesterase deficiency
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
RIVASTIGMINE is proposed as a repurposing candidate against **Hereditary butyrylcholinesterase deficiency** via the target **BCHE**. It shows a ChEMBL median pChEMBL affinity of 7.43 at assay confidence 9/9, an Open Targets target-disease association of 0.805, and a Tanimoto similarity of 0.327 to PYRIDOSTIGMINE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.8368.

_Chemist rationale:_ Rivastigmine has a median pChEMBL affinity of 7.43 against BCHE, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.327 to the nearest approved drug pyridostigmine. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5854 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.43 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.805 |
| Tanimoto to nearest approved drug | 0.327 (PYRIDOSTIGMINE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 250.3, 2.76, 0, 3, 32.8, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 93.4 |
| Boltz structure confidence (0-1) | 0.887 |
| Boltz binding-pose confidence (0-1) | 0.511 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.248 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/390a3afc4426d3a079668ade08f9628c.cif) |
| Boltz ADME — lipophilicity (logD) | -0.060 |
| Boltz ADME — permeability | 1.861 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | HALLUCINATION (896), FALL (877), DEATH (676), CONFUSIONAL STATE (633), DRUG INEFFECTIVE (595) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (6):** 18075469, 29631548, 30593143, 38420074, 39814397, 40778538
- **ChEMBL activity IDs (7):** 1413723, 1487306, 1699433, 1808319, 1924056, 2356422, 839078
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.864 | 0.2592 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.184 | 0.0276 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8368** |

Weighted sum before penalty = 0.8368; penalty = 0.0000; reported composite_score = 0.8368.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.511) or predicted affinity (0.248) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.887 (complex pLDDT 0.896) and an AFDB apo mean pLDDT of 93.4; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 4 — PROPANIDID, RIVASTIGMINE, RIVASTIGMINE TARTRATE, TACRINE HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
