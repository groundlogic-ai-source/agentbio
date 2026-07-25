# Repurposing hypothesis: PHYSOSTIGMINE → Hereditary butyrylcholinesterase deficiency
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

## 1. Hypothesis summary
PHYSOSTIGMINE is proposed as a repurposing candidate against **Hereditary butyrylcholinesterase deficiency** via the target **BCHE**. It shows a ChEMBL median pChEMBL affinity of 7.46 at assay confidence 9/9, an Open Targets target-disease association of 0.805, and a Tanimoto similarity of 0.786 to PHYSOSTIGMINE SALICYLATE. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.9262.

_Chemist rationale:_ Physostigmine has a median pChEMBL affinity of 7.46 against BCHE, measured at assay confidence score 9 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.786 to the nearest approved drug, physostigmine salicylate. No physical or genetic interactors for the target are recorded in BioGRID.

### Stage 1 prioritization scores
- **tractability_score:** 0.5854 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 1.0000 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.46 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.805 |
| Tanimoto to nearest approved drug | 0.786 (PHYSOSTIGMINE SALICYLATE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 275.4, 1.77, 1, 4, 44.8, 1 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 93.4 |
| Boltz structure confidence (0-1) | 0.871 |
| Boltz binding-pose confidence (0-1) | 0.450 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.153 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/d09c3ac47b03f187fcdc063f07eee6cd.cif) |
| Boltz ADME — lipophilicity (logD) | 0.817 |
| Boltz ADME — permeability | 1.833 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | DELIRIUM (30), ANTICHOLINERGIC SYNDROME (29), DRUG INTERACTION (20), OVERDOSE (20), TOXICITY TO VARIOUS AGENTS (17) |
| Prior trials for this exact drug+disease | 0 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (6):** 18075469, 29631548, 30593143, 38420074, 39814397, 40778538
- **ChEMBL activity IDs (23):** 117581, 1191527, 1206877, 1284248, 1413695, 1491404, 1699449, 1808320, 1808321, 1840539, 226310, 2367616, 2435546, 350518, 350519, 361652, 361653, 454432, 486565, 683108, 728066, 731682, 772462
- **NCT numbers (0):** none

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.875 | 0.2625 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.758 | 0.1137 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.9262** |

Weighted sum before penalty = 0.9262; penalty = 0.0000; reported composite_score = 0.9262.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.450) or predicted affinity (0.153) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.871 (complex pLDDT 0.913) and an AFDB apo mean pLDDT of 93.4; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 4 — PROPANIDID, RIVASTIGMINE, RIVASTIGMINE TARTRATE, TACRINE HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
