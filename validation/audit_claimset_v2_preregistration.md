# Audit claim-set v2 — pre-registration

**Study label:** `audit_claimset_v2` (the sealed harness runs ONLY under this
label; this artifact must never be reported as benchmark v2, engineering
acceptance, or discovery accuracy).
**Registered:** 2026-08-10, before any v2 claim construction.
**Supersedes:** nothing. v1 (`audit_claimset_v1`, frozen and scored
2026-08-10) is immutable; its FAIL result is never replaced, re-scored, or
re-labeled. v2 is a new study on a new claim set against the fixed pipeline.
Outreach and reports carry BOTH results with their dates.

## 1. Why v2 exists

v1 failed its pre-registered bars: defect recall 32/60 = 0.533 (bar ≥ 0.80)
and control false-flag rate 7/40 = 0.175 (bar ≤ 0.15). Failure analysis
(validation/audit_claimset_results.md + construction log) attributed the
deficit to three root causes, each addressed by a committed fix BEFORE this
registration (main @ 44a298a):

1. **E2 (19/19 missed) — stale persisted pools.** The three persisted
   candidate pools predated the black-box/withdrawal classifier fix and
   wrongly badged marketed boxed-warning drugs as WITHDRAWN (composite
   pinned at the 0.40 safety cap).
   *Fix:* the withdrawal/black-box reconciliation was extracted to a single
   shared function (`agents.reviewer._reconcile_safety`), all three pools
   were refreshed against live sources through that same code (21 candidates
   corrected; originals preserved under
   `output/candidates/_backup_pre_safety_refresh/`), and every refreshed
   pool now carries a `safety_schema_version = "safety-v2"` stamp. The audit
   layer explicitly discloses "safety status UNVERIFIED" on any pool missing
   the stamp instead of silently trusting badges.
2. **N3 precision defect (5 of 7 control false-flags).** The N3
   preclinical-only detector flagged marketed, label-approved drugs whenever
   the literature lane held only preclinical assertions.
   *Fix:* approved-label guard in `api/audit_context.py` (context contract
   `audit-context-v2`): when cutoff-eligible marketed FDA-label evidence
   exists, N3 reports UNRESOLVED (a disclosure), never FLAGGED. Genuinely
   label-less preclinical-only drugs still flag.
3. **E4 (8/8 invalid claims) — falsified construction assumption.** v1
   assumed brand names do not resolve in ChEMBL; the ChEMBL synonym tables
   resolve all 8 accepted brands, so v1's "must read UNRESOLVED" ground
   truth was wrong for every E4 claim.
   *Fix (construction-side):* v2 verifies brand-name NON-resolvability at
   construction time against the raw ChEMBL API (molecule pref_name and
   molecule_synonym search). A brand that resolves is excluded and logged;
   ground truth is never asserted against observable resolution behavior.

**Deliberately NOT fixed in v2 scope:** the v1 control arm's 2 N1
label-parse false-flags (single-ingredient labels mis-parsed as
combinations). v2 registers this as a known-remaining defect with an
expected residual control false-flag contribution of up to 2/40.

## 2. Design (unchanged unless stated)

Identical to v1 (validation/audit_claimset_preregistration.md) in: claim
shape (input fields only reach the pipeline; truth is scoring-side),
citation cutoff (**2026-08-10**, mechanical artifact-date rule per v1
Amendment 1), health gates (ChEMBL, openFDA, Europe PMC, PubTator3, pool
jobs resolvable), cache policy (production caches as-is, 7–30 day TTLs,
degraded states never cached), blinding, one-scored-run discipline, and the
single one-fix-one-rerun allowance (fresh for v2; archive-only, marker +
manifest cross-check, results-hash lock).

### Composition (registered rule, replaces v1's fixed 30/30/40)

Total claims **100** = **60 defect** + **40 control**, unchanged.

- **existing_fix (E) group = what the fixed pools honestly provide.**
  The E1/E2/E4 classes are pool-bounded (the audit must FIND the drug in a
  persisted pool) and the safety refresh legitimately shrank the
  wrongly-flagged universe. Quotas: E1 ≤ 4, E3 ≤ 4, E4 ≤ 8, E2 = remainder
  of the pool boxed-warning universe after exclusions. **If total E yield
  is < 6, construction aborts** and the composition rule must be amended
  here (dated) before any retry. Expected yield ≈ 6–16, including a
  possible E4 yield of 0 — a
  near-zero E4 yield is itself a reported finding (brand-name resolution is
  near-universal), not a construction failure.
- **novel (N) group = 60 − E yield.** Initial quotas N1 8 / N2 7 / N3 7 /
  N4 8; shortfall reallocates in the fixed order **N1 → N4 → N2** (N2's
  universe is the 185 unused approved biologics in the enriched dataset).
  If the deficit cannot be filled, construction aborts.
- **control = 40** (32 pool-free + 8 pool-context), same cleanliness gates
  as v1.
- **Instance disjointness:** every drug named in any v1 claim is excluded
  from every v2 class. v2 re-tests the fixed pipeline, never v1's items.

### Class-specific v2 construction gates (additions only)

- **E4:** raw ChEMBL API resolution check (above). The builder never imports
  the pipeline's resolution code; the check queries the same public ChEMBL
  endpoints the pipeline reads.
- **N3:** v1 gates (absent from repoDB/DrugCentral, zero Europe PMC
  clinical-trial hits, cutoff-eligible primary paper) **plus no
  cutoff-eligible FDA label products** — label absence is part of the
  defect's definition under the v2 detector, verified against raw openFDA
  responses, not pipeline code.

## 3. Metrics and PASS thresholds (identical to v1)

- `defect_recall = caught / 60` — PASS requires **≥ 0.80**, exact
  Clopper–Pearson lower 95% bound **≥ 0.65**.
- `control_false_flag_rate = flagged / 40` — PASS requires **≤ 0.15**,
  upper 95% bound **≤ 0.30**.
- `novel_recall` — reported with CI, no threshold (first adequate
  measurement; v1's novel arm had N3 = 0 claims).
- Max group abstention fraction 0.10. Invalid citations are excluded and
  reported as construction defects; lane failures abstain.
- Overall verdict PASS requires both thresholded metrics.

## 4. Freeze discipline

After construction, `validation/audit_claimset_v2_freeze_manifest.json`
binds the v2 claim-set SHA-256, the code commit (which includes the fixes
above and the v2 harness wrapper), harness config, cache policy, and health
requirements (including: all three pool files stamped `safety-v2`). Any
`.py` change under `api/`, `agents/`, `data_sources/`, `cache/`, or
`validation/` between the freeze commit and the scored run refuses the run.

## 5. Phase 2

The inter-rater arm remains registered under the v1 pre-registration and is
unchanged by this document; if it ever runs it will use the then-current
pipeline, and v2 claims are eligible material for it.

## Amendments

## Amendment 1 (2026-08-10, before claim construction): reporting posture

1. **The pre-fix arithmetic is a diagnostic, not a prediction.** v1's
   failure analysis ("19 of 28 misses and 5 of 7 control false-flags are
   attributable to the two fixed defects") was computed by looking backward
   at v1's specific claims. It justified WHICH bugs to fix first; it must
   never be reported as a predicted v2 outcome. v2 is instance-disjoint
   from v1 precisely so the fixes can still fail on new claims (a narrower
   than diagnosed root cause, or a third, unobserved defect). The result is
   genuinely uncertain at registration time; before the score is revealed,
   the results report must restate this paragraph.
2. **The pool refresh fixed stale DATA, not a too-strict STANDARD.** v1's
   E2 misses came from outdated persisted snapshots wrongly badging
   marketed drugs as withdrawn — the safety logic itself was unchanged and
   the conservative direction (cap retained whenever a withdrawal flag is
   unrefuted) was preserved end-to-end. Every v2-facing writeup (results
   report, technical appendix, outreach letters) must frame this as "our
   safety data was stale", never as "we made the detector less aggressive".
