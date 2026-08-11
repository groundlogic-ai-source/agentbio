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

## Amendment 2 (2026-08-11, after the first construction attempt aborted
## fail-closed at the E floor, before any scored run): composition floor

The first construction attempt (log: /tmp/build_v2.log) yielded **E = 1**
(E1 = 0, E2 = 1 [VANDETANIB], E3 = 0, E4 = 0), below the registered floor
of 6, and aborted as designed. Causes, verified at construction against raw
sources: the safety refresh legitimately emptied E1 (the three remaining
refreshed-pool withdrawal-flagged drugs are all v1-named and therefore
excluded by instance disjointness); the E3 candidate (fluticasone
propionate) failed its raw ground-truth check (ChEMBL action_type AGONIST
is not incompatibility-class for a glucocorticoid-activation indication);
E4 yielded zero — the pre-registered finding that brand-name resolution is
near-universal.

1. **The E floor is amended from 6 to 1.** Rationale: the floor exists to
   guarantee the fixed-defect classes are actually exercised; with E = 1
   the study still tests the stale-pool fix (E2) end-to-end, and the
   near-zero E1/E3/E4 yields are themselves reported findings about the
   post-fix universe, not a defect in the claim set.
2. **Composition:** E group = honest pool-bounded yield (observed: 1);
   **novel group = 60 − E yield (59)** with initial quotas unchanged and
   shortfall reallocation order unchanged (N1 → N4 → N2); control = 40.
   The 60/40 defect/control split, all PASS thresholds, the abstention
   cap, and the one-scored-run discipline are unchanged.
3. **Reporting:** the v2 results report and any derived writeup must state
   the E-group yield and its per-class causes (this amendment) before the
   score, and must not describe the composition change as relaxed
   methodology — the abort-and-amend path itself is the registered
   mechanism working as intended.

## Amendment 3 (2026-08-11, before any scored result exists): freeze #1
## destroyed by environment failure; pre-freeze engineering fixes

The first frozen claim set (100 claims: E=1, N=59, C=40; claim-set SHA
a9a7f45f…, freeze commit 0b4d776d, manifest
validation/audit_claimset_v2_freeze_manifest.json) was **destroyed by a
Replit environment restart mid-run** that reverted the workspace: the
claim-set file, freeze manifest, freeze commit, partial raw outputs, and
run log were all lost and are unrecoverable (git object recovery
attempted; the commit is not in the object store). The executing run was
stopped at 75/100 claims executed.

1. **No scoring occurred.** No results file was ever written; no claim was
   ever classified as caught/missed/clean/false-flag; truth labels were
   never joined to outputs. The one-scored-run allowance and the
   one-fix-one-rerun allowance are therefore both **unconsumed**. The
   only observation made from the lost run was operational, not
   outcome-level: four claims crashed with harness exceptions
   (AttributeError on enum-typed fields; one unhashable-dict) and both
   AI-integration providers were returning sustained HTTP 429s.
2. **Engineering fixes applied BEFORE the new freeze** (i.e., they are
   part of the frozen system under test, not a post-hoc patch):
   (a) `data_sources/evidence_ledger.py` — `EvidenceRecord.__post_init__`
       coerces enum/string fields at construction so malformed adapter
       output can never crash `.value` access or set membership (the
       observed crash class; affected claims would otherwise have been
       scored as abstentions);
   (b) `data_sources/llm_failover.py` (new) — text-only LLM calls
       round-robin across both AI-integration providers with
       cross-provider failover and exponential backoff on 429/5xx;
       provider-bound web-search tool calls retry with backoff on the
       same provider (tool semantics unchanged). Call sites rewired:
       pubmed relevance gate, clinicaltrials stop-reason classifier,
       mechanism-direction Step-2 classifier, safety Layer-2 classifier;
   (c) `validation/run_audit_claimset.py` — per-claim atomic checkpointing
       (was every-5-claims) with commit-bound resume: a partial archive is
       reused only if written by the same code commit, completed claims
       are kept, and crashed/missing claims are re-run. One archive still
       equals one code version.
3. **Construction restart.** The claim set is rebuilt from scratch under
   the identical registered rules (Amendment 2 composition: E honest
   yield, N to 59, C=40). Because construction involves live sources and
   LLM classification, the new set is not expected to be byte-identical
   to the lost one; this is disclosed, and the new freeze manifest binds
   the new claim-set SHA and the new code commit.
4. **Reporting:** the v2 results report must disclose this amendment
   (environment failure, unconsumed allowances, pre-freeze fix list)
   alongside the score. The lost run's crash patterns informed the fixes
   but no outcome information carried over.
