# V2 Engineering Acceptance — Five Archived v1 Genuine Misses

_Label: **engineering_acceptance** (NOT benchmark v2). Generated: 2026-08-11T15:01:22._

Disease-input pipeline only (`select_for_disease` -> biologist -> chemist -> pooled union -> reviewer); the confirmed drug is held out and used only for post-run matching. Every candidate target row is run up to a cap of **10** (engineering harness, not a production ranking).

## Summary (outcome levels reported separately)

- Generated: **3/5**
- Mechanistically valid: **3/5** (qualified evidence + direction compatibility only)
- Top-10: 0/5  |  STRONG_MATCH: 1/5

## Per-fixture

| # | Drug | Disease | Generated | Mech-valid | Rank | Top10 | Strong | By target | Match | Providers |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Phenobarbital | Lennox-Gastaut syndrome | — | — | — | — | — | — | — | — |
| 2 | Lamotrigine | Lennox-Gastaut syndrome | — | — | — | — | — | — | — | — |
| 3 | Mercaptopurine | Acute Promyelocytic Leukemia | ✓ | ✓ | 47 | — | — | PNP | inchikey_block | drugcentral, europepmc |
| 4 | Vincristine | Rhabdomyosarcoma | ✓ | ✓ | 16 | — | — | TUBB | inchikey_block | drugcentral, europepmc, gtopdb |
| 5 | Promazine | Acute intermittent porphyria | ✓ | ✓ | 53 | — | ✓ | DRD3 | inchikey_block | drugcentral, europepmc, gtopdb |

## Holdout audit & source health

### Phenobarbital / Lennox-Gastaut syndrome
- holdout active: True · drugs: ['Phenobarbital']
- holdout unresolved: —
- validity: None
- source lineages: 0
- source health: {}

### Lamotrigine / Lennox-Gastaut syndrome
- holdout active: True · drugs: ['Lamotrigine']
- holdout unresolved: —
- validity: None
- source lineages: 0
- source health: {}

### Mercaptopurine / Acute Promyelocytic Leukemia
- holdout active: True · drugs: ['Mercaptopurine']
- holdout unresolved: —
- validity: 4 qualified record(s); efficacy_confidence=0.8063; direction compatible
- source lineages: 5
- source health: {'gtopdb': True, 'drugcentral': True, 'bindingdb': False, 'chembl': True, 'europepmc': True}

### Vincristine / Rhabdomyosarcoma
- holdout active: True · drugs: ['Vincristine']
- holdout unresolved: —
- validity: 4 qualified record(s); efficacy_confidence=0.8894; direction compatible; explicit therapeutic action
- source lineages: 6
- source health: {'gtopdb': True, 'drugcentral': True, 'bindingdb': False, 'chembl': True, 'europepmc': True}

### Promazine / Acute intermittent porphyria
- holdout active: True · drugs: ['Promazine']
- holdout unresolved: —
- validity: 10 qualified record(s); efficacy_confidence=0.8894; direction compatible; explicit therapeutic action
- source lineages: 12
- source health: {'gtopdb': True, 'drugcentral': True, 'bindingdb': False, 'chembl': True, 'europepmc': True}
