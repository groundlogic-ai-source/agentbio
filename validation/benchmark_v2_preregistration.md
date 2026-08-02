# Benchmark v2 — Pre-Registration (committed BEFORE any v2 code or run)

Date: 2026-08-01. This document is committed to git before the upgrade package is
implemented and before any v2 case executes. It exists so that v2's design cannot be
accused of being tuned to v2's own outcomes.

## Motivation (from v1-partial, see benchmark_v1_partial_report.md)

v1 was terminated at 13/50 because its genuine misses showed two structural defects
with perfect consistency, plus a confirmed leakage gap:

- D1: candidate pools too strict (human IC50/Ki, confidence ≥ 8 only) — confirmed drugs
  absent from pools even when the correct target was selected.
- D2: Stage 1 target ranking uses Open Targets genetic association only — misses drugs
  acting on pathway/metabolic targets rather than genetic drivers.
- D3: coverage errors (EFO mismatch; OT-zero-genetic-target infections) counted as
  errors instead of being classified.
- D4: PubMed literature search not sealed against the held-out drug (inflation channel).

## Planned v2 changes (complete list — anything beyond this requires amending this doc)

1. **Tiered evidence pools** (addresses D1). Tier 1 unchanged: human IC50/Ki, conf ≥ 8.
   Tier 2: human EC50/Kd/Potency, conf ≥ 8. Tier 3: any human functional assay,
   conf ≥ 6. Lower tiers used ONLY when higher tiers yield an empty/sparse pool;
   each compound carries its tier; a fixed deterministic composite penalty applies per
   tier (tier 2: −0.05, tier 3: −0.10) and the tier is disclosed in the dossier.
2. **Pathway-aware target augmentation** (addresses D2). Reactome pathway neighbors of
   top genetically-associated targets are added to the considered set with a provenance
   flag and an association-score discount; the original OT-genetic ranking is unchanged.
   Cases whose confirmed drug is mechanism-agnostic cytotoxic (e.g. tubulin agents) are
   expected to remain misses and will be reported as a scope limitation, not fixed.
3. **Error classification fixes** (addresses D3). OT-zero-genetic-target diseases are
   classified `out_of_scope (no genetic evidence)` instead of `error`; the EFO
   resolution mismatch for Trichinellosis-type names is fixed or the case reclassified.
   Counts of reclassified cases are disclosed in the v2 report.
4. **PubMed holdout seal** (addresses D4). The held-out drug's names/synonyms/salt
   forms are filtered from Biologist PubMed query construction and results while
   holdout is active; failures recorded loudly like `holdout_unresolved`.

## Protocol commitments

- New freeze tag `benchmark-freeze-v2` after upgrades land; v2 runner reuses the same
  health/freeze/holdout gates and the same 50-case list (`benchmark-cases-v1`).
- **One v2 run. No re-rolls.** If v2 produces a poor number, it is published as-is.
- v1-partial and v2 results are published side-by-side with this document. v2's
  improvement (if any) is attributed to the four changes above — no silent extras.
- Chance-rate baseline + exact Poisson-binomial p-values recomputed mechanically,
  same method as v1.
- The threshold-adjacent sensitivity audit (LLM cap-gate bias) runs on v2 results.
