# Rediscovery-rate summary — repoDB retrospective

_Generated: 2026-08-03 14:30:44 — computed by validation/miss_classifier.py_

## repoDB first-10 (biologic-leaning, top-3 targets)

## Miss-reason breakdown (computed)

- Recovered (hit): **0/9**
- Right target, drug absent from activity pool (mechanism-endpoint gap): **0** — recoverable by the candidate-pool fix
- Right target but pool truncation: **0**
- Wrong target selected: **0** (true target was in the considered list for 0 → recoverable by target-selection fix)
- Biologic / non-small-molecule (structurally outside the pool): **2**
- No ChEMBL mechanism record for drug: **0**

**Projected ceiling under both fixes: 0/9** (hits + pool-fix recoverable + target-fix recoverable).

| Drug | Disease | Class | Detail |
|---|---|---|---|
| Dornase alfa | Cystic Fibrosis | biologic_not_addressable | molecule_type=Enzyme; small-molecule IC50/Ki pool cannot contain it. |
| Anakinra | Chronic Infantile Neurological, Cutaneous, and Articular Syndrome | — |  |
| Desmopressin | Hemophilia A | biologic_not_addressable | molecule_type=Protein; small-molecule IC50/Ki pool cannot contain it. |
| Desmopressin | von Willebrand Disease | — |  |
| Coagulation factor VIIa Recombinant Human | Hemophilia B | — |  |
| Somatropin recombinant | Prader-Willi Syndrome | — |  |
| Somatropin recombinant | Turner Syndrome | — |  |
| Imiglucerase | Gaucher Disease | — |  |
| Laronidase | Mucopolysaccharidosis I | — |  |

## repoDB small-molecule set (top-1 target)

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

## Combined

## Miss-reason breakdown (computed)

- Recovered (hit): **1/22**
- Right target, drug absent from activity pool (mechanism-endpoint gap): **4** — recoverable by the candidate-pool fix
- Right target but pool truncation: **0**
- Wrong target selected: **8** (true target was in the considered list for 5 → recoverable by target-selection fix)
- Biologic / non-small-molecule (structurally outside the pool): **2**
- No ChEMBL mechanism record for drug: **0**

**Projected ceiling under both fixes: 10/22** (hits + pool-fix recoverable + target-fix recoverable).

| Drug | Disease | Class | Detail |
|---|---|---|---|
| Dornase alfa | Cystic Fibrosis | biologic_not_addressable | molecule_type=Enzyme; small-molecule IC50/Ki pool cannot contain it. |
| Anakinra | Chronic Infantile Neurological, Cutaneous, and Articular Syndrome | — |  |
| Desmopressin | Hemophilia A | biologic_not_addressable | molecule_type=Protein; small-molecule IC50/Ki pool cannot contain it. |
| Desmopressin | von Willebrand Disease | — |  |
| Coagulation factor VIIa Recombinant Human | Hemophilia B | — |  |
| Somatropin recombinant | Prader-Willi Syndrome | — |  |
| Somatropin recombinant | Turner Syndrome | — |  |
| Imiglucerase | Gaucher Disease | — |  |
| Laronidase | Mucopolysaccharidosis I | — |  |
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