# Frozen audit claim-set v2 — scored results

- Label: `audit_claimset_v2` · scored 2026-08-11T01:58:11.935914+00:00
- Claim set sha256: `5013a57aca080e82…`
- Freeze code commit: `0cce8376`
- **Verdict: PASS**

## Headline metrics (pre-registered thresholds)

| Metric | Value | 95% bound | Threshold | Met |
|--------|-------|-----------|-----------|-----|
| defect_recall | 1.000 (60/60) | CP lower 0.951 | ≥ 0.8, lower ≥ 0.65 | yes |
| control_false_flag | 0.050 (2/40) | CP upper 0.149 | ≤ 0.15, upper ≤ 0.3 | yes |
| novel_recall (no threshold) | 1.000 (59/59) | CP lower 0.950 | — | — |

Fixed-denominator views (abstentions as not-caught): defect 1.000, control false-flag 0.050.

## Per-class breakdown

| Class | caught/flagged | miss/clean | abstain | excluded | total |
|-------|-----|------|---------|----------|-------|
| E2_boxed_warning_not_withdrawal | 1 | 0 | 0 | 0 | 1 |
| N1_combination_product_splitting | 8 | 0 | 0 | 0 | 8 |
| N2_biologic_modality_mis_scope | 43 | 0 | 0 | 0 | 43 |
| N4_dose_route_implausibility | 8 | 0 | 0 | 0 | 8 |
| none | 2 | 38 | 0 | 0 | 40 |

## Abstentions and exclusions


## Disclosure-accuracy annotation (non-scored)

Caught defects whose disclosure contradicts the external artifact: 0

## Limitations

- E4 (unresolvable-name honesty) may have zero or few claims: v1's construction assumption was falsified (ChEMBL synonyms resolve major brand names), so v2 accepts only brands verified NON-resolving in raw ChEMBL at construction. A near-empty E4 class is reported as untested, not padded.
- The N1 label-parse precision defect observed in v1's control arm (2 of 7 false flags) was deliberately NOT fixed before v2; a residual control false-flag contribution of up to 2/40 is expected and does not by itself fail the <=0.15 bar.
- Composition is construction-determined: existing_fix is the honest pool-bounded yield of the three refreshed safety-v2 pools; novel fills the remainder of the 60-claim defect total per the registered N1 -> N4 -> N2 reallocation order.
- The citation cutoff (2026-08-10) is a mechanical artifact-date rule, not a judgment of evidence currency.
- Pool-context coverage is limited to the two persisted cases referenced by job_id_hint in this claim set (refreshed to safety-v2); novel-lane claims are pool-free by design.
- N3 (species/preclinical-only) yielded ZERO claims under v2's tightened gates (v1 gates PLUS no cutoff-eligible FDA label and no human-trial signal): all five candidates failed raw ground truth at construction. The N3 defect class is untested in v2 — reported as such, not padded. The novel group's 59 claims are N1=8, N2=43 (reallocation), N4=8.
- Freeze #1 was destroyed by an environment restart before any scoring (Amendment 3); this claim set is the registered rebuild under identical rules. Engineering fixes (EvidenceRecord coercion, LLM provider round-robin + 429 backoff, per-claim checkpoint/resume) were applied BEFORE this freeze and are part of the frozen system under test. Both allowances remain unconsumed.
