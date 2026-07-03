# Repurposing hypothesis: MIGALASTAT → Fabry disease
## 1. Hypothesis summary
MIGALASTAT is proposed as a repurposing candidate against **Fabry disease** via the target **GLA**. It shows a ChEMBL median pChEMBL affinity of 7.27 at assay confidence 9/9, an Open Targets target-disease association of 0.894, and a Tanimoto similarity of 0.000 to no approved analog in the set. Target network context (BioGRID, physical/genetic — not mechanism): none mapped. The resulting composite score is 0.8500.

_Chemist rationale:_ Migalastat has a median pChEMBL affinity of 7.27 against GLA, measured at assay confidence score 9 out of 9, and is an approved drug. No nearest approved structural analog was identified within the candidate set, and no BioGRID physical or genetic interactors were recorded for the target.

### Stage 1 prioritization scores
- **tractability_score:** 0.5278 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.0450 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 7.27 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.894 |
| Tanimoto to nearest approved drug | 0.000 (none in set) |
| Approved / known drug | yes |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 163.2, -2.97, 5, 5, 93.0, 1 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 94.3 |
| Boltz structure confidence (0-1) | 0.954 |
| Boltz binding-pose confidence (0-1) | 0.580 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.142 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/af0ef1715f68013939b927a4ed4d74db.cif) |
| Boltz ADME — lipophilicity (logD) | n/a |
| Boltz ADME — permeability | n/a |
| Boltz ADME — solubility | n/a |
| openFDA adverse-event signal (FAERS) | BRONCHOPULMONARY ASPERGILLOSIS (6), CYTOMEGALOVIRUS INFECTION (4), INFECTION REACTIVATION (4), ISOSPORIASIS (4), RENAL IMPAIRMENT (3) |
| Prior trials for this exact drug+disease | 34 |
| Target discovery method | genetic_association |

## 3. Full source citations
- **PMIDs (7):** 26564084, 33172708, 33489501, 34609404, 36386210, 38047356, 40159218
- **ChEMBL activity IDs (4):** 1405782, 18001636, 18001643, 3342720
- **NCT numbers (34):** NCT00214500, NCT00283933, NCT00283959, NCT00304512, NCT00526071, NCT00925301, NCT01196871, NCT01218659, NCT01458119, NCT01476163, NCT01489995, NCT01730469, NCT01730482, NCT01853852, NCT02082327, NCT02194985, NCT02930655, NCT03135197, NCT03425539, NCT03500094, NCT03683966, NCT03737214, NCT03838237, NCT03949920, NCT04020055, NCT04049760, NCT04252066, NCT04602364, NCT04639999, NCT04804566, NCT05280548, NCT06303466, NCT06904261, NCT06906367

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 1.000 | 0.3000 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 1.000 | 0.2000 |
| Normalized Tanimoto similarity | 0.15 | 0.000 | 0.0000 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8500** |

Weighted sum before penalty = 0.8500; penalty = 0.0000; reported composite_score = 0.8500.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.580) or predicted affinity (0.142) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.954 (complex pLDDT 0.948) and an AFDB apo mean pLDDT of 94.3; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 2 — MIGALASTAT, MIGALASTAT HYDROCHLORIDE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
