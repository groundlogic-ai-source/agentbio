# AgentBio reviewer pilot — compact retrospective rediscovery

_Source results generated: 2026-08-03T21:02:42_
_Report built: 2026-08-03 21:07:34_

## What this pilot tests

This is a small, transparent reviewer pilot of the current discovery pipeline. It asks whether a confirmed drug appears in the final ranked candidate list when the disease-side discovery run is executed without using that drug's known indication as an input.

It is **not** a prospective or historical benchmark. The data sources are present-day databases, and the bioactivity pool is intentionally not redacted: seeing a held-out drug in a correctly selected target's pool is the retrospective rediscovery event being measured. The result should be described as **rediscovery in present-day data under disease-side holdout**, not as proof that the system would have discovered the repurposing historically.

## Headline result

| Metric | Result |
|---|---:|
| Cases in headline set | 9 |
| Recovered anywhere in final candidate list | 1/9 (11.1%) |
| Recovered in Top-10 | 1/9 (11.1%) |
| Correct mechanism target selected first | 4/9 (44.4%) |
| Correct mechanism target considered anywhere | 8/9 (88.9%) |
| Right-target but strict activity-pool gap | 3/9 (33.3%) |
| Target-selection misses | 5/9 (55.6%) |
| Pipeline errors | 0/9 |

The one recovered case is Ibrutinib / Waldenstrom macroglobulinemia (BTK, rank 2). The main limitation visible in this pilot is not one single ranking score: three cases had the right target but no qualifying strict ChEMBL activity record, while four had the correct target somewhere in the considered set but lost because only the top target was pursued.

## Headline cases

| Drug / disease | Selected target | Target outcome | Final result | Interpretation |
|---|---|---|---|---|
| **Dapsone** / Leprosy | RIPK2 | true target not considered | wrong target | Leprosy's confirmed mechanism is bacterial; the human rare-disease target universe cannot represent it. |
| **Anagrelide** / Essential thrombocythemia | PDE3B | top target matched | right target pool gap | PDE3B was selected, but the strict ChEMBL activity pool has no qualifying assay for the drug. |
| **Sapropterin** / Phenylketonuria | PAH | top target matched | right target pool gap | PAH was selected, but a cofactor/chaperone is not visible in the strict IC50/Ki pool. |
| **Miglustat** / Gaucher Disease | GBA1 | true target in considered list (rank 2) | wrong target | UGCG was present in the considered targets but ranked second; only the top target was pursued. |
| **Riociguat** / Chronic thromboembolic pulmonary hypertension | PDE5A | true target in considered list (rank 6) | wrong target | Soluble guanylate cyclase targets were present at selection rank six; PDE5A was pursued first. |
| **Ibrutinib** / Waldenstrom Macroglobulinemia | BTK | top target matched | recovered at rank 2 | BTK was selected and the drug was recovered at rank two. |
| **Imatinib** / Chronic eosinophilic leukemia | JAK1 | true target in considered list (rank 6) | wrong target | For chronic eosinophilic leukemia, the relevant target was present at rank six; only the top target was pursued. |
| **Pyridostigmine** / Myasthenia Gravis | ACHE | top target matched | right target pool gap | ACHE was selected, but the strict ChEMBL activity pool has no qualifying assay for the drug. |
| **Lorazepam** / Lennox-Gastaut syndrome | CA2 | true target in considered list (rank 6) | wrong target | GABA-A targets were present at selection rank six; the top carbonic-anhydrase precedent target was pursued first. |

## Stratified readout

| Stratum | Cases | Recovered | What it shows |
|---|---:|---:|---|
| human-target scope boundary | 1 | 0 | The current human-target contract does not cover this mechanism. |
| right target / pool coverage | 3 | 0 | Target selection can be right while the strict evidence pool is blind. |
| target-ranking stress | 4 | 0 | Top-1 pursuit loses cases whose true target was already considered. |
| positive control | 1 | 1 | A positive control is recovered near the top. |

## Stress controls excluded from the headline

These cases remain useful for showing where the current system's contract ends, but combining them with the core small-molecule human-target cases would make the headline less interpretable.

| Drug / disease | Selected target | Result | Why excluded |
|---|---|---|---|
| **Pentamidine** / African Trypanosomiasis | FGF1 (pharmacological_precedent_via_parent_umbrella) | wrong target | African trypanosomiasis is a pathogen-directed mechanism outside the human-target headline contract. |
| **Temozolomide** / Anaplastic astrocytoma | FKBP1A (pharmacological_precedent_via_parent_umbrella) | wrong target | DNA alkylation is not honestly represented by the current single human-protein target contract. |
| **Everolimus** / Tuberous sclerosis complex | FKBP1A (pharmacological_precedent) | right target pool gap | The selected FKBP1A target comes from a pharmacological-precedent lane that already knows the rapalog class; this is not an independent discovery case. |

## Protocol and limitations

- The source run contains 13 disease-drug cases; this report uses 12 unique-drug cases (9 headline cases and 3 stress controls), leaving out the duplicate Imatinib / Idiopathic Hypereosinophilic Syndrome pair. The subset is fixed in this script for reviewer readability; it was not preregistered before the underlying run.
- The confirmed drug was used only for post-run matching and miss classification; it was held out from disease-side approved-drug and indication signals.
- Drug matching uses the runner's InChIKey/ChEMBL-ID-first logic with a name fallback, and is performed after ranking.
- The case list is a development/reviewer pilot, not the frozen v2 benchmark population. No chance-rate significance claim is made.
- Everolimus is shown only as a circularity control because its selected precedent lane already knows the rapalog class.
- The pilot therefore supports a narrow claim: the machine can recover at least one known repurposing pair near the top in the current data, while exposing target-coverage and evidence-coverage limits. It does not support a claim of broad autonomous discovery accuracy.

## Reproduction

The underlying completed run is `validation/repodb_results_smallmol.json`. This report is generated with:

```bash
python -m validation.build_reviewer_pilot
```
