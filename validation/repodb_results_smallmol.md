# repoDB Retrospective — Small-Molecule Cases

_Generated: 2026-08-11 01:02:38_

_Filter: enriched_dataset.csv, status=Approved, chembl_molecule_type=Small molecule, disease in Orphanet rare / WHO NTD universe_

## Summary

- In-universe cases: 13/13
- Hits: 1  |  Misses: 12  |  Errors: 0  |  Out-of-scope: 0

## Per-case table

| # | Disease | Drug | Target | Rank | Score | Top10 | Strong | Status | Miss Reason |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Leprosy | Dapsone | RIPK2 | — | — | — | — | **miss** | 'Dapsone' absent among 32 ChEMBL compound(s) for top target RIPK2 (O43353) |
| 2 | Essential thrombocythemia | Anagrelide | PDE3B | — | — | — | — | **miss** | 'Anagrelide' absent among 28 ChEMBL compound(s) for top target PDE3B (Q13370) |
| 3 | Phenylketonuria | Sapropterin | PAH | — | — | — | — | **miss** | 'Sapropterin' absent among 25 ChEMBL compound(s) for top target PAH (P00439) |
| 4 | Gaucher Disease | Miglustat | GBA1 | — | — | — | — | **miss** | 'Miglustat' absent among 28 ChEMBL compound(s) for top target GBA1 (P04062) |
| 5 | African Trypanosomiasis | Pentamidine | FGF1 | — | — | — | — | **miss** | 'Pentamidine' absent among 52 ChEMBL compound(s) for top target FGF1 (P05230) |
| 6 | Anaplastic astrocytoma | Temozolomide | FKBP1A | — | — | — | — | **miss** | 'Temozolomide' absent among 29 ChEMBL compound(s) for top target FKBP1A (P62942) |
| 7 | Chronic thromboembolic pulmonary hypertension | Riociguat | PDE5A | — | — | — | — | **miss** | 'Riociguat' absent among 32 ChEMBL compound(s) for top target PDE5A (O76074) |
| 8 | Waldenstrom Macroglobulinemia | Ibrutinib | BTK | 2 | 0.6185 | ✓ | — | **hit** | — |
| 9 | Idiopathic Hypereosinophilic Syndrome | Imatinib | IL5 | — | — | — | — | **miss** | 'Imatinib' absent among 154 ChEMBL compound(s) for top target IL5 (P05113) |
| 10 | Chronic eosinophilic leukemia | Imatinib | JAK1 | — | — | — | — | **miss** | 'Imatinib' absent among 35 ChEMBL compound(s) for top target JAK1 (P23458) |
| 11 | Myasthenia Gravis | Pyridostigmine | ACHE | — | — | — | — | **miss** | 'Pyridostigmine' absent among 35 ChEMBL compound(s) for top target ACHE (P22303) |
| 12 | Lennox-Gastaut syndrome | Lorazepam | CA2 | — | — | — | — | **miss** | 'Lorazepam' absent among 38 ChEMBL compound(s) for top target CA2 (P00918) |
| 13 | Tuberous sclerosis complex | Everolimus | FKBP1A | — | — | — | — | **miss** | 'Everolimus' absent among 29 ChEMBL compound(s) for top target FKBP1A (P62942) |

## Miss-reason breakdown (computed)

- Recovered (hit): **1/13**
- Right target, drug absent from activity pool (mechanism-endpoint gap): **4** — recoverable by the candidate-pool fix
- Right target but pool truncation: **0**
- Wrong target selected: **8** (true target was in the considered list for 5 → recoverable by target-selection fix)
- Biologic / non-small-molecule (structurally outside the pool): **0**
- No ChEMBL mechanism record for drug: **0**

**Projected ceiling under both fixes: 10/13** (hits + pool-fix recoverable + target-fix recoverable).

| Drug | Disease | Class | Detail |
|---|---|---|---|
| Dapsone | Leprosy | wrong_target | Drug's mechanism target(s) ['folP1'] absent from the entire considered-target list — discovery problem, not ranking. |
| Anagrelide | Essential thrombocythemia | right_target_pool_gap | Mechanism endpoint links drug to PDE3A/PDE3B (Phosphodiesterase 3 inhibitor) but no qualifying Homo sapiens IC50/Ki assay exists — activity pool cannot see it. |
| Sapropterin | Phenylketonuria | right_target_pool_gap | Mechanism endpoint links drug to PAH (Phenylalanine-4-hydroxylase activator) but no qualifying Homo sapiens IC50/Ki assay exists — activity pool cannot see it. |
| Miglustat | Gaucher Disease | wrong_target | Drug's mechanism target(s) ['UGCG'] were NOT tried; true target sits at selection rank 2 (tried top-1). |
| Pentamidine | African Trypanosomiasis | wrong_target | Drug's mechanism target(s) ['DNA', 'Kinetoplast DNA'] absent from the entire considered-target list — discovery problem, not ranking. |
| Temozolomide | Anaplastic astrocytoma | wrong_target | Drug's mechanism target(s) ['DNA'] absent from the entire considered-target list — discovery problem, not ranking. |
| Riociguat | Chronic thromboembolic pulmonary hypertension | wrong_target | Drug's mechanism target(s) ['GUCY1A1', 'GUCY1A2', 'GUCY1B1', 'GUCY1B2'] were NOT tried; true target sits at selection rank 6 (tried top-1). |
| Ibrutinib | Waldenstrom Macroglobulinemia | hit | rank 2, composite 0.6185 |
| Imatinib | Idiopathic Hypereosinophilic Syndrome | wrong_target | Drug's mechanism target(s) ['ABL1', 'BCR', 'KIT', 'PDGFRB'] were NOT tried; true target sits at selection rank 3 (tried top-1). |
| Imatinib | Chronic eosinophilic leukemia | wrong_target | Drug's mechanism target(s) ['ABL1', 'BCR', 'KIT', 'PDGFRB'] were NOT tried; true target sits at selection rank 6 (tried top-1). |
| Pyridostigmine | Myasthenia Gravis | right_target_pool_gap | Mechanism endpoint links drug to ACHE (Acetylcholinesterase inhibitor) but no qualifying Homo sapiens IC50/Ki assay exists — activity pool cannot see it. |
| Lorazepam | Lennox-Gastaut syndrome | wrong_target | Drug's mechanism target(s) ['GABRA1', 'GABRA2', 'GABRA3', 'GABRA4', 'GABRA5', 'GABRA6', 'GABRB1', 'GABRB2', 'GABRB3', 'GABRD', 'GABRE', 'GABRG1', 'GABRG2', 'GABRG3', 'GABRP', 'GABRQ'] were NOT tried; true target sits at selection rank 6 (tried top-1). |
| Everolimus | Tuberous sclerosis complex | right_target_pool_gap | Mechanism endpoint links drug to FKBP1A (FK506-binding protein 1A inhibitor) but no qualifying Homo sapiens IC50/Ki assay exists — activity pool cannot see it. |
