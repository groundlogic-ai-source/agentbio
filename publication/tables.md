# Generated tables (publication/make_figures.py)

## Benchmark v2 headline

| Subset | Executed | Out of scope | Error | In-scope | Rediscovered | Top-10 | STRONG_MATCH |
|---|---|---|---|---|---|---|---|
| Primary | 32 | 10 | 0 | 22 | 6 (27.3%) | 6/6 | 2/6 |
| Development | 15 | 2 | 1 | 12 | 2 (16.7%) | 1/2 | 0/2 |

## Audit claim-set (frozen, one scored run)

| Metric | Result | PASS threshold | Met? |
|---|---|---|---|
| Defect recall | 32/60 = 0.533 (95% CI 0.400–0.663), CP lower 0.420 | ≥ 0.80, lower ≥ 0.65 | NO |
| Control false-flag | 7/40 = 0.175 (95% CI 0.073–0.328), CP upper 0.304 | ≤ 0.15, upper ≤ 0.30 | NO |
| Novel-class recall | 29/30 = 0.967 (95% CI 0.828–0.999) | none (registered) | — |

## Source ablation (pre-freeze control, 13 development cases × 4 conditions)

| Condition | Generated + mechanistically valid |
|---|---|
| chembl_only | 5/13 |
| chembl_gtopdb | 8/13 |
| chembl_drugcentral | 10/13 |
| all_three | 10/13 |
