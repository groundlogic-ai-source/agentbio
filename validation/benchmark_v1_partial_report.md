# Benchmark v1 — Partial Results Report (terminated at 14/50 by protocol decision)

**Freeze tag:** `benchmark-freeze-v1` · **Case list:** `benchmark-cases-v1` (50 cases) ·
**Terminated:** 2026-08-01, after 14 cases, by explicit protocol decision (structural
defects diagnosed with 5/5 consistency in the genuine-miss reasons — not a results-based re-roll).

## Tally at termination

| Outcome | n | Detail |
|---|---|---|
| Hit | 1 | Tretinoin / Acute Promyelocytic Leukemia — rank 2, matched by InChIKey/ChEMBL ID |
| Genuine miss | 5 | All five share one signature (see Diagnosis) |
| Error | 5 | EFO resolution mismatch ×2 (Trichinellosis), OT zero genetic targets ×2 (infections), 1 other (see archive) |
| Admin exclusion | 3 | Umbrella disease terms etc. — correct behavior |

## Diagnosis (the reason for termination)

1. **Pool strictness (dominant).** All 5 genuine misses: the confirmed drug is absent
   from the candidate pool because the Chemist admits only *H. sapiens* IC50/Ki assays at
   confidence ≥ 8. Approved-drug evidence frequently lives in EC50/Kd/potency or
   mechanism-endpoint assays. (Consistent with the known Sapropterin/Pyridostigmine gap.)
2. **Genetic-association mismatch.** Stage 1 ranks targets by Open Targets *genetic*
   association; many confirmed repurposes act on pathway/metabolic biology instead
   (phenobarbital→GABA-A, mercaptopurine→purine metabolism, vincristine→tubulin; the
   pipeline selected DNM1/CA2/CA4, RARA/IDH1/IDH2, TP53/NF1/DICER1 respectively).
   Some cases (cytotoxic chemo) may be legitimately out of scope for a
   mechanism-driven pipeline; v2 will classify rather than chase these.
3. **Coverage errors.** Infectious diseases are structurally sparse in OT genetic
   evidence; two errored cases should have been classified admin-exclusions.

## Disclosed limitations of this partial result

- **PubMed was NOT sealed against the held-out drug** in v1 (confirmed by code
  inspection): the Biologist's literature search could surface papers describing the
  known repurpose. Literature cannot influence Stage 1 target selection (biologist runs
  downstream), but the channel could inflate narrative-stage judgments. Sealed in v2.
- LLM parametric knowledge is unsealable (disclosed in the run protocol).
- Effective scorable n at termination: 14 − 3 admin = 11 (5 errors shrink it further).

## Provenance

Raw per-case data: `validation/benchmark_results_v1_partial.json` (immutable archive).
Continuation of this file (`benchmark_results.json`) belongs to a future v2 run under a
new freeze tag; the two are never merged.
