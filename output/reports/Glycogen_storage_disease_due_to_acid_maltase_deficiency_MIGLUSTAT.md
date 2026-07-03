# Repurposing hypothesis: MIGLUSTAT → Glycogen storage disease due to acid maltase deficiency
## 1. Hypothesis summary
MIGLUSTAT is proposed as a repurposing candidate against **Glycogen storage disease due to acid maltase deficiency** via the target **GAA**. It shows a ChEMBL median pChEMBL affinity of 7.00 at assay confidence 9/9, an Open Targets target-disease association of 0.877, and a Tanimoto similarity of 0.677 to MIGLITOL. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.8413.

_Chemist rationale:_ Miglustat has a median pChEMBL affinity of 7.0 against GAA, measured at assay confidence score 9 out of 9, and is an approved or known drug with a Tanimoto similarity of 0.677 to the nearest approved drug miglitol. No BioGRID physical or genetic interactors are recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5649 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.0548 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.00 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.877 |
| Tanimoto to nearest approved drug | 0.677 (MIGLITOL) |
| Approved / known drug | yes |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 219.3, -1.45, 4, 5, 84.2, 4 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 91.9 |
| Boltz structure confidence (0-1) | 0.918 |
| Boltz binding-pose confidence (0-1) | 0.889 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.184 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/af33636a3a30b6ed49c4fb1cb2215957.cif) |
| Boltz ADME — lipophilicity (logD) | n/a |
| Boltz ADME — permeability | n/a |
| Boltz ADME — solubility | n/a |
| openFDA adverse-event signal (FAERS) | DIARRHOEA (31), PNEUMONIA (24), SEIZURE (24), PYREXIA (14), DISEASE PROGRESSION (13) |
| Prior trials for this exact drug+disease | 1 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (0):** none
- **ChEMBL activity IDs (3):** 24394162, 2947461, 3204094
- **NCT numbers (1):** NCT02675465

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.644 | 0.1933 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.653 | 0.0979 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8413** |

Weighted sum before penalty = 0.8413; penalty = 0.0000; reported composite_score = 0.8413.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.889) or predicted affinity (0.184) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.918 (complex pLDDT 0.902) and an AFDB apo mean pLDDT of 91.9; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 2 — MIGLITOL, VOGLIBOSE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
