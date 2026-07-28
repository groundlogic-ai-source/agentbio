# Repurposing hypothesis: DEXAMETHASONE → Multiple myeloma
> **Repurposing-only pool:** the candidate compounds for this target were restricted to FDA-approved / known drugs (ChEMBL max_phase ≥ 4) at collection time. Unapproved research-grade tool compounds were excluded from the pool, not merely down-ranked.

> ℹ **Top-K evaluation: 5 of 5 target(s) successfully evaluated.**

## 1. Hypothesis summary
DEXAMETHASONE is proposed as a repurposing candidate against **Multiple myeloma** via the target **NR3C1**. It shows a ChEMBL median pChEMBL affinity of 8.39 at assay confidence 9/9, an Open Targets target-disease association of 0.900, and a Tanimoto similarity of 0.422 to FLUTICASONE PROPIONATE. Target network context (BioGRID, physical/genetic — not mechanism): SFN, TGFB1I1, ECD, RAD9A, NCOA6, RANBP9, JUN, STAT3. The resulting composite score is 0.8243.

_Chemist rationale:_ Dexamethasone has a median pChEMBL affinity of 8.39 against NR3C1, measured at assay confidence score 9 out of 9, and is an approved drug with a Tanimoto similarity of 0.422 to the nearest approved drug fluticasone propionate. NR3C1 has eight recorded BioGRID physical/genetic interactors: SFN, TGFB1I1, ECD, RAD9A, NCOA6, RANBP9, JUN, and STAT3.

### Stage 1 prioritization scores
- **tractability_score:** 0.6632 (ChEMBL bioactivity + AlphaFold pLDDT − prior-trial-failure penalty)
- **unmet_need_score:** 0.0555 (treatment availability + prevalence)

These are computed by the same formulas used to rank the full rare-disease / NTD universe; a manually chosen target is scored identically, never faked or skipped.

## 2. Evidence table
| Evidence | Value |
| --- | --- |
| ChEMBL median pChEMBL affinity | 8.39 |
| Assay confidence score (0-9) | 9 |
| Open Targets association score | 0.900 |
| Tanimoto to nearest approved drug | 0.422 (FLUTICASONE PROPIONATE) |
| Approved / known drug | yes |
| Mutation-specific approved indication (disclosure) | No specific mutation named in approved indication |
| Lipinski/Veber (MW, logP, HBD, HBA, TPSA, rotB) | 392.5, 1.90, 3, 5, 94.8, 2 |
| Lipinski violations / Veber pass | 0 / yes |
| AFDB apo structure mean pLDDT (free protein, no ligand) | 59.6 |
| Boltz structure confidence (0-1) | 0.606 |
| Boltz binding-pose confidence (0-1) | 0.930 |
| Boltz predicted affinity (relative optimization score, 0-1, NOT a Kd) | 0.571 |
| Boltz predicted structure (CIF) | [Download CIF](/api/structures/6a2dfaf15c681f67a6d65356332cd562.cif) |
| Boltz ADME — lipophilicity (logD) | 1.267 |
| Boltz ADME — permeability | 0.671 |
| Boltz ADME — solubility | high-confidence |
| openFDA adverse-event signal (FAERS) | OFF LABEL USE (23274), FATIGUE (18259), DIARRHOEA (18185), NAUSEA (15164), PNEUMONIA (14959) |
| Prior trials for this exact drug+disease | 100 |
| Target discovery method | pharmacological_precedent |

## 3. Full source citations
- **PMIDs (6):** 16528529, 19133980, 20501894, 25474406, 31819496, 34481515
- **ChEMBL activity IDs (4):** 1733236, 1741691, 1799337, 501558
- **NCT numbers (100):** NCT00083551, NCT00093028, NCT00111748, NCT00124813, NCT00215943, NCT00306813, NCT00314743, NCT00431990, NCT00438841, NCT00440726, NCT00482261, NCT00571168, NCT00573391, NCT01042704, NCT01053949, NCT01155583, NCT01168804, NCT01180569, NCT01249690, NCT01250808, NCT01255514, NCT01484626, NCT01555281, NCT01562405, NCT01568294, NCT01572480, NCT01665794, NCT01689987, NCT01731886, NCT01745588, NCT01863550, NCT01916252, NCT02020941, NCT02075996, NCT02082405, NCT02100657, NCT02128230, NCT02204241, NCT02290431, NCT02315716, NCT02516423, NCT02685826, NCT02697383, NCT02863991, NCT02880228, NCT02981199, NCT03104270, NCT03151811, NCT03353545, NCT03492138, NCT03530683, NCT03605056, NCT03792620, NCT03809780, NCT03859427, NCT03942224, NCT04045795, NCT04162210, NCT04176718, NCT04191616, NCT04270409, NCT04302324, NCT04407442, NCT04414475, NCT04519476, NCT04656951, NCT04762745, NCT04782687, NCT04790474, NCT04802356, NCT04877275, NCT04933539, NCT04934475, NCT04956302, NCT04989140, NCT05027594, NCT05236621, NCT05280275, NCT05308654, NCT05392946, NCT05497102, NCT05514990, NCT05527340, NCT05552222, NCT05641324, NCT05704049, NCT05835726, NCT05909826, NCT06022939, NCT06106945, NCT06140966, NCT06158412, NCT06187441, NCT06232707, NCT06348108, NCT06561854, NCT06762769, NCT06956170, NCT07452198, NCT07532473

## 4. Composite score breakdown
| Term | Weight | Component value | Contribution |
| --- | ---: | ---: | ---: |
| Normalized pChEMBL affinity | 0.30 | 0.770 | 0.2310 |
| Assay confidence (score / 9) | 0.20 | 1.000 | 0.2000 |
| Normalized Open Targets association | 0.20 | 0.900 | 0.1800 |
| Normalized Tanimoto similarity | 0.15 | 0.422 | 0.0633 |
| No prior failed trial (1/0) | 0.15 | 1 | 0.1500 |
| **Composite (weighted sum − penalty − cap)** | | | **0.8243** |

Weighted sum before penalty = 0.8243; penalty = 0.0000; reported composite_score = 0.8243.

## 5. Limitations
- **Binding is not efficacy.** A high binding-pose confidence (0.930) or predicted affinity (0.571) only suggests the molecule may occupy the target; it does NOT establish agonism vs. antagonism, functional modulation, or therapeutic benefit.
- **ADME values are model predictions, not measurements.** The Boltz lipophilicity/permeability/solubility numbers are computed estimates and must be confirmed experimentally before any decision.
- **Structure confidence is bounded.** This hypothesis relies on a Boltz structure_confidence of 0.606 (complex pLDDT 0.509) and an AFDB apo mean pLDDT of 59.6; the AFDB model contains NO ligand, so the protein-ligand pose is entirely a Boltz prediction.
- **Assay-type and species caveats.** The affinity is a median pChEMBL over Homo sapiens IC50/Ki assays at confidence ≥ 8; assay heterogeneity and the bounded approved-drug reference set for Tanimoto still apply.
- **Absence of evidence is not evidence of absence.** A zero prior-trial count or no adverse-event signal may reflect that the pair has simply never been studied, not that it is safe or untried.
- **This is a repurposing *hypothesis*, not a finding.** It is a prioritised starting point that requires wet-lab and, ultimately, clinical validation.

### Target druggability context

- **Approved drugs with known mechanism against this target (ChEMBL):** 65 — ALCLOMETASONE DIPROPIONATE, AMCINONIDE, BECLOMETHASONE DIPROPIONATE, BETAMETHASONE, BETAMETHASONE ACETATE
- **Historical difficulty literature:** insufficient signal found (fewer than 2 qualifying abstracts in targeted PubMed searches for undruggability / resistance / difficulty).

_Druggability context is informational only. It does not affect tractability\_score, unmet\_need\_score, composite\_score, or STRONG\_MATCH._
