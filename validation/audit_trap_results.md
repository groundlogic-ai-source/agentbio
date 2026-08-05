# Audit Trap Benchmark — Results

- Generated: 2026-08-05T02:38:48.600430+00:00
- Label: `audit_trap_benchmark` (offline: True)
- **Verdict: PASS**

## Metrics

- Trap recall: 12/12 = 1.00 (threshold ≥ 0.9)
- Control false-flag rate: 1/4 = 0.25 (threshold ≤ 0.25)
- Precision: 0.92

## Traps (must be caught)

| ID | Class | Outcome |
|----|-------|---------|
| T1 | safety_cap_disclosure | CAUGHT |
| T2 | blackbox_not_withdrawal | CAUGHT |
| T3 | direction_incompatible | CAUGHT |
| T4 | label_artifact_screen | CAUGHT |
| T5 | confirmation_discipline | CAUGHT |
| T6 | unresolvable_name_honesty | CAUGHT |
| T7 | salt_form_dedup | CAUGHT |
| T8 | degraded_source_honesty | CAUGHT |
| T9 | unobserved_not_zero | CAUGHT |
| T10 | xlogp_unresolved_disclosure | CAUGHT |
| T11 | degraded_200_empty_pool_not_cached | CAUGHT |
| T12 | holdout_name_no_api_leak | CAUGHT |

## Controls (must NOT be flagged)

| ID | Class | Outcome |
|----|-------|---------|
| C1 | clean_candidate_no_flags | **FALSE-FLAGGED** |
| C2 | verified_hypothesis_not_flagged | CLEAN |
| C3 | measured_zero_still_counts | CLEAN |
| C4 | resolved_absent_not_unresolved | CLEAN |

> Engineering acceptance instrument measuring audit-layer detection of known failure classes against stubbed inputs. NOT discovery accuracy; must never be reported as benchmark v2. External organizational validation still requires a frozen claim set with independent ground truth and an inter-rater study.
