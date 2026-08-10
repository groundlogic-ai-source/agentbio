# V2 Engineering Acceptance — Five Archived v1 Genuine Misses

_Label: **engineering_acceptance** (NOT benchmark v2). Generated: 2026-08-10T16:16:05._

Disease-input pipeline only (`select_for_disease` -> biologist -> chemist -> pooled union -> reviewer); the confirmed drug is held out and used only for post-run matching. Every candidate target row is run up to a cap of **10** (engineering harness, not a production ranking).

## Summary (outcome levels reported separately)

- Generated: **5/5**
- Mechanistically valid: **5/5** (qualified evidence + direction compatibility only)
- Top-10: 0/5  |  STRONG_MATCH: 1/5

## Per-fixture

| # | Drug | Disease | Generated | Mech-valid | Rank | Top10 | Strong | By target | Match | Providers |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Phenobarbital | Lennox-Gastaut syndrome | ✓ | ✓ | 215 | — | — | GABRA1 | inchikey_block | drugcentral, europepmc |
| 2 | Lamotrigine | Lennox-Gastaut syndrome | ✓ | ✓ | 159 | — | — | SCN1A | inchikey_block | chembl, drugcentral, europepmc |
| 3 | Mercaptopurine | Acute Promyelocytic Leukemia | ✓ | ✓ | 47 | — | — | PNP | inchikey_block | drugcentral, europepmc |
| 4 | Vincristine | Rhabdomyosarcoma | ✓ | ✓ | 16 | — | — | TUBB | inchikey_block | drugcentral, europepmc, gtopdb |
| 5 | Promazine | Acute intermittent porphyria | ✓ | ✓ | 56 | — | ✓ | DRD3 | inchikey_block | drugcentral, europepmc, gtopdb |

## Holdout audit & source health

### Phenobarbital / Lennox-Gastaut syndrome
- holdout active: True · drugs: ['Phenobarbital']
- holdout unresolved: —
- validity: 15 qualified record(s); efficacy_confidence=0.8063; direction compatible
- source lineages: 16
- source health: {'gtopdb': True, 'drugcentral': True, 'bindingdb': False, 'chembl': True, 'europepmc': True}

### Lamotrigine / Lennox-Gastaut syndrome
- holdout active: True · drugs: ['Lamotrigine']
- holdout unresolved: —
- validity: 32 qualified record(s); efficacy_confidence=0.8156; direction compatible; explicit therapeutic action
- source lineages: 38
- source health: {'gtopdb': True, 'drugcentral': True, 'bindingdb': False, 'chembl': True, 'europepmc': True}

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
