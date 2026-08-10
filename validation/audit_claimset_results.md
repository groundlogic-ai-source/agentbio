# Frozen audit claim-set v1 — scored results

- Label: `audit_claimset_v1` · scored 2026-08-10T20:57:15.778454+00:00
- Claim set sha256: `32efd7d965f62e2c…`
- Freeze code commit: `f41d7661`
- **Verdict: FAIL**

## Headline metrics (pre-registered thresholds)

| Metric | Value | 95% bound | Threshold | Met |
|--------|-------|-----------|-----------|-----|
| defect_recall | 0.533 (32/60) | CP lower 0.420 | ≥ 0.8, lower ≥ 0.65 | NO |
| control_false_flag | 0.175 (7/40) | CP upper 0.304 | ≤ 0.15, upper ≤ 0.3 | NO |
| novel_recall (no threshold) | 0.967 (29/30) | CP lower 0.851 | — | — |

Fixed-denominator views (abstentions as not-caught): defect 0.533, control false-flag 0.175.

## Per-class breakdown

| Class | caught/flagged | miss/clean | abstain | excluded | total |
|-------|-----|------|---------|----------|-------|
| E1_safety_withdrawal | 2 | 0 | 0 | 0 | 2 |
| E2_boxed_warning_not_withdrawal | 0 | 19 | 0 | 0 | 19 |
| E3_direction_incompatible | 1 | 0 | 0 | 0 | 1 |
| E4_unresolved_name_honesty | 0 | 8 | 0 | 0 | 8 |
| N1_combination_product_splitting | 8 | 0 | 0 | 0 | 8 |
| N2_biologic_modality_mis_scope | 13 | 0 | 0 | 0 | 13 |
| N4_dose_route_implausibility | 8 | 1 | 0 | 0 | 9 |
| none | 7 | 33 | 0 | 0 | 40 |

## Abstentions and exclusions


## Disclosure-accuracy annotation (non-scored)

Caught defects whose disclosure contradicts the external artifact: 0

## Limitations

- N3 (species/preclinical-only) has ZERO claims: all externally verifiable candidates failed construction verification and the shortfall was reallocated per protocol §2. The N3 detector is untested by this study (synthetic unit coverage only).
- Persisted candidate pools predate the black-box/withdrawal classifier fix; E1/E2 disclosure TEXT may contradict external artifacts. This is measured by the non-scored disclosure annotation and never changes scored metrics.
- The citation cutoff (2026-08-10) is a mechanical artifact-date rule, not a judgment of evidence currency.
- Pool-context coverage is limited to three persisted cases; novel-lane claims are pool-free by design.
