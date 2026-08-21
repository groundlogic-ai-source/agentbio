# v2 Benchmark Program — Historical Progress Snapshot (2026-08-05)

> **Superseded on 2026-08-09.** Benchmark v2 subsequently completed as the
> single pre-registered run: 32 screened primary rows plus 15 development rows.
> The frozen result is `validation/benchmark_results_v2.json`. The primary
> endpoint was 6 rediscoveries among 22 in-scope screened cases (27.3%).
> This document is retained only as a dated operational snapshot and must not
> be used as current status or as authorization to rerun the benchmark.

## Where things stand

- The single-shot **v2 benchmark has NOT started**. No case list, no results, no freeze tag.
- The **source-ablation control is running**: 5 of 52 source-arm runs complete.
- The run is currently **health-gated**: DrugCentral's API returned transient HTTP 500s, so the
  workflow paused and retries every 5 minutes rather than run a source arm against a degraded
  provider. The checkpoint is preserved; it resumes from arm 6 automatically.

## How the control works

- **13 small-molecule development cases** where the correct drug is known (e.g. Dapsone/Leprosy,
  Anagrelide/Essential thrombocythemia). These are development cases, disjoint from the 50
  benchmark cases — no leakage.
- For each case, the true drug's identity is **sealed (holdout)**: its name, synonyms, salts, and
  disease-side evidence are redacted before the pipeline runs.
- Target selection runs **once per case** and is **frozen** (up to 10 targets), so all source
  conditions see identical inputs.
- Each case then runs under **4 source configurations**:
  1. ChEMBL only
  2. ChEMBL + Guide to Pharmacology
  3. ChEMBL + DrugCentral
  4. All three sources
- Every arm records per-target **source health** (`ok` / `empty` / `disabled`). An intentionally
  removed source must report `disabled`; an enabled source that errors invalidates the arm.
- A per-target **15-minute hard timeout** turns stalls into explicit errors, never synthetic
  misses. Preflight re-validates the full 52-row artifact before anything downstream proceeds.

**Purpose:** the v1 failure mode was candidate-*generation* — the true drug never entered the
pool. v2's central claim is that union sourcing fixes that. The control measures whether each
added source actually recovers drugs ChEMBL alone misses (the paper's mechanism evidence), and it
rehearses the full pipeline before the one-shot frozen benchmark.

## Results so far

| Case | Arm | Outcome | Candidates |
|---|---|---|---|
| Dapsone | ChEMBL only | miss | 40 |
| Dapsone | + GtoPdb | miss | 47 |
| Dapsone | + DrugCentral | miss | 50 |
| Dapsone | all three | miss | 56 |
| Anagrelide | ChEMBL only | miss | 146 |

## Prediction: expected, not alarming — yet

The weak start matches the prior miss forensics almost exactly:

- **Dapsone** was already classified `wrong_target`: its mechanism target (folP1, a *bacterial*
  enzyme) is absent from the human target universe. No source union can generate a drug for a
  target that was never considered. Its 4/4 miss **confirms a known limitation** (pathogen-target
  scope) rather than revealing a new one.
- **Anagrelide** was classified `right_target_pool_gap`: mechanism-endpoint sources link it to
  PDE3A/PDE3B, but no qualifying human IC50/Ki assay exists in ChEMBL. This is *precisely* the
  case the union architecture was built to rescue — and its three source-expanded arms have not
  run yet.

So the first five arms tested the two hardest, already-diagnosed cases. The earlier projection
(10/13 recoverable under both fixes) was never predicated on these two converting via source
expansion alone.

**What to watch:** the recovery *delta* between `chembl_only` and `all_three` across the remaining
11 cases. If, after ~5–6 fully-completed cases, source expansion has rescued none of the
pool-gap-classified cases, that is a genuine problem for the paper's central claim. If it rescues
even 2–3, the mechanism evidence holds. Statistical power at 13 cases is modest; treat any single
arm as anecdote and the case-level delta table as the result.

## The planned v2 run (after the control validates)

1. Strict validation of the 52-row control artifact (any degraded row → discard, bounded retries).
2. **Amendment-1 screen** of candidate benchmark cases (OT re-resolution, umbrella guard, gated
   target list, non-empty union pool; indeterminate on lookup failure — never read as absence).
3. Preflight creates `benchmark-freeze-v2` at a clean HEAD — only now.
4. The **single v2 run**: 50 held-out cases, holdout redaction, ChEMBL health gates mid-run,
   resume-only on halt (completed cases never re-executed).
5. Outputs: `benchmark_results_v2.json/.md`, reported against the pre-registered thresholds —
   with v1 confined to a short motivation paragraph, not a results section.

Realistic calendar: control likely 3–8 more hours of healthy-source time (longer while
DrugCentral is degraded); screen + freeze minutes; benchmark on the order of the control's
single-arm time × 50. Overnight to ~a day end-to-end.
