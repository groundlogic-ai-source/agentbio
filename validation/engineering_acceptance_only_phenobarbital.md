# V2 Engineering Acceptance — Five Archived v1 Genuine Misses

_Label: **engineering_acceptance** (NOT benchmark v2). Generated: 2026-08-03T18:12:18._

Disease-input pipeline only (`select_for_disease` -> biologist -> chemist -> pooled union -> reviewer); the confirmed drug is held out and used only for post-run matching. Every candidate target row is run up to a cap of **10** (engineering harness, not a production ranking).

## Summary (outcome levels reported separately)

- Generated: **1/1**
- Mechanistically valid: **1/1** (qualified evidence + direction compatibility only)
- Top-10: 0/1  |  STRONG_MATCH: 0/1

## Per-fixture

| # | Drug | Disease | Generated | Mech-valid | Rank | Top10 | Strong | By target | Match | Providers |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Phenobarbital | Lennox-Gastaut syndrome | ✓ | ✓ | 215 | — | — | GABRA1 | inchikey_block | drugcentral, europepmc |

## Holdout audit & source health

### Phenobarbital / Lennox-Gastaut syndrome
- holdout active: True · drugs: ['Phenobarbital']
- holdout unresolved: —
- validity: 15 qualified record(s); efficacy_confidence=0.8063; direction compatible
- source lineages: 16
- source health: {'gtopdb': True, 'drugcentral': True, 'chembl': True, 'europepmc': True}
