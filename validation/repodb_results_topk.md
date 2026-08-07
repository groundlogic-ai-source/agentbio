# repoDB Retrospective — 10 In-Universe Cases (Top-3 Targets)

_Generated: 2026-08-07 20:54:53_

_Each disease runs the Biologist→Chemist→Reviewer pipeline against the top 3 OT-associated targets. A case is HIT if the approved drug is found in ANY of the 3 pools._

## Summary

- In-universe cases: 9/10
- Hits: 0  |  Misses: 2  |  Errors: 7  |  Out-of-scope: 1

## Per-case table

| # | Disease | Drug | Hit Target (rank) | Reviewed Rank | Score | Top10 | Strong | Status |
|---|---|---|---|---|---|---|---|---|
| 1 | Cystic Fibrosis | Dornase alfa | VCP | — | — | — | — | **miss** |
| 2 | Chronic Infantile Neurological, Cutaneous, and Articular Syndrome | Anakinra | — | — | — | — | — | **error** |
| 3 | Hemophilia A | Desmopressin | PLG | — | — | — | — | **miss** |
| 4 | von Willebrand Disease | Desmopressin | — | — | — | — | — | **error** |
| 5 | Hemophilia B | Coagulation factor VIIa Recombinant Human | — | — | — | — | — | **error** |
| 6 | Prader-Willi Syndrome | Somatropin recombinant | — | — | — | — | — | **error** |
| 7 | Turner Syndrome | Somatropin recombinant | — | — | — | — | — | **error** |
| 8 | Gaucher Disease | Imiglucerase | — | — | — | — | — | **error** |
| 9 | Mucopolysaccharidosis I | Laronidase | — | — | — | — | — | **error** |

## Per-target breakdown

### Dornase alfa / Cystic Fibrosis

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | VCP | P55072 | 0.465 | 28 | 28 | — | — | — |
| 2 | CFTR | P13569 | 0.918 | 102 | 102 | — | — | — |
| 3 | RPS27A | P62979 | 0.559 | 32 | 32 | — | — | — |

### Anakinra / Chronic Infantile Neurological, Cutaneous, and Articular Syndrome

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Desmopressin / Hemophilia A

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|
| 1 | PLG | P00747 | 0.900 | 30 | 30 | — | — | — |
| 2 | F9 | P00740 | 0.763 | 189 | 189 | — | — | — |
| 3 | F7 | P08709 | 0.552 | 155 | 155 | — | — | — |

### Desmopressin / von Willebrand Disease

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Coagulation factor VIIa Recombinant Human / Hemophilia B

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Somatropin recombinant / Prader-Willi Syndrome

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Somatropin recombinant / Turner Syndrome

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Imiglucerase / Gaucher Disease

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

### Laronidase / Mucopolysaccharidosis I

| OT rank | Target | UniProt | OT score | # Chem | # Rev | Found | Rev rank | Score |
|---|---|---|---|---|---|---|---|---|

## Comparison: top-1 vs top-3 targets

| # | Disease | Drug | Top-1 status | Top-3 status | Recovered by target rank |
|---|---|---|---|---|---|
| 1 | Cystic Fibrosis | Dornase alfa | **miss** | **miss** | — |
| 2 | Chronic Infantile Neurological, Cutaneous, and Articular Syndrome | Anakinra | **miss** | **error** | — |
| 3 | Hemophilia A | Desmopressin | **miss** | **miss** | — |
| 4 | von Willebrand Disease | Desmopressin | **miss** | **error** | — |
| 5 | Hemophilia B | Coagulation factor VIIa Recombinant Human | **miss** | **error** | — |
| 6 | Prader-Willi Syndrome | Somatropin recombinant | **miss** | **error** | — |
| 7 | Turner Syndrome | Somatropin recombinant | **miss** | **error** | — |
| 8 | Gaucher Disease | Imiglucerase | **miss** | **error** | — |
| 9 | Mucopolysaccharidosis I | Laronidase | **miss** | **error** | — |

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
