# Study B — Triage Discrimination: Pool Rebuild Results (descriptive)

**Contract:** `triage-discrimination-studyb-v2`  
**Descriptive only:** True  
**Freeze:** commit `f94a9853e258`, rule fingerprint `c600f834faf8…`  
**results_sha256:** `d1840426992e6529bce6ced5720167927c9365088c320ddb4a66c3142ffa7a77`  
**Diseases:** 12/12 complete, none incomplete  

## Caveats (from the artifact)

- rank/composite/mechanism dimensions derive from a pool built with disease-linked OpenTargets and trial data; only the disease-independent dimensions are provably blind.
- Pool sizes for pools 7–12 finalized only on prod; the prod disk wiped before full checkpoint pull-back. Pool sizes below are from the dev checkpoint where available.

## Per-disease summary

| Disease | Pool size | Confirmed found | Confirmed absent | Best confirmed rank |
|---|---|---|---|---|
| Acute Promyelocytic Leukemia | 5850 | 1 | 3 | 13 |
| Aspergillosis | 4869 | 0 | 1 | — |
| Brucellosis | 5613 | 0 | 1 | — |
| Dermatomyositis | 5444 | 0 | 1 | — |
| Gaucher Disease | 3071 | 0 | 1 | — |
| Kaposi Sarcoma | 15452 | 0 | 1 | — |
| Lennox-Gastaut syndrome | n/a | 0 | 4 | — |
| Listeriosis | n/a | 0 | 1 | — |
| Malaria | n/a | 0 | 1 | — |
| Rhabdomyosarcoma | n/a | 1 | 0 | 15 |
| Zollinger-Ellison syndrome | n/a | 2 | 2 | 3 |
| liposarcoma | n/a | 0 | 2 | — |

## Confirmed repurposings found in-pool (4/22)

- **Omeprazole** (Zollinger-Ellison syndrome) — rank 3
- **Lansoprazole** (Zollinger-Ellison syndrome) — rank 7
- **Mercaptopurine** (Acute Promyelocytic Leukemia) — rank 13
- **Vincristine** (Rhabdomyosarcoma) — rank 15

## Confirmed repurposings absent from pool (18/22)

- Tretinoin (Acute Promyelocytic Leukemia)
- Daunorubicin (Acute Promyelocytic Leukemia)
- Idarubicin (Acute Promyelocytic Leukemia)
- Isavuconazonium (Aspergillosis)
- Streptomycin (Brucellosis)
- Benzoic Acid (Dermatomyositis)
- Voglibose (Gaucher Disease)
- Vinblastine (Kaposi Sarcoma)
- Phenobarbital (Lennox-Gastaut syndrome)
- Lamotrigine (Lennox-Gastaut syndrome)
- Clonazepam (Lennox-Gastaut syndrome)
- Primidone (Lennox-Gastaut syndrome)
- Erythromycin (Listeriosis)
- Pyrimethamine (Malaria)
- Esomeprazole (Zollinger-Ellison syndrome)
- Roxatidine acetate (Zollinger-Ellison syndrome)
- Trabectedin (liposarcoma)
- Eribulin (liposarcoma)

## Read of the result (descriptive, no inference)

- When a confirmed repurposing is in the rebuilt pool, it ranks well: all 4 found rank in the top 15 (best 3, worst 15). Scoring is not the bottleneck.
- The dominant miss mode is pool absence (18/22): the confirmed drug never entered the candidate pool, so no score could ever surface it. Profiles for absent rows show disposition=SUPPORTED with the ABSENT_FROM_POOL flag — the evidence lanes support the drug; pool construction never proposed it.
- Context top-candidate rows (the pipeline's own top picks): 118 rows; dispositions 73 QUALIFIED / 35 SUPPORTED / 10 DISQUALIFIED.

