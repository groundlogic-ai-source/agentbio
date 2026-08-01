# Benchmark case-selection criteria (pre-registered)

**Date:** 2026-07-31 · **Status:** DRAFT for approval · **Discipline:** these criteria are fixed on commit, *before* the case list exists. The list is then derived **mechanically** from this document and committed to git with the selection script. No case is added, removed, or re-ordered after the single frozen run starts.

## 1. Purpose

The expanded benchmark must be immune to the failure mode of the old 13-case suite: cases accumulating by convenience and results read post hoc. Selection must be reproducible from this document alone — anyone re-running the committed selection script at the freeze commit must reproduce the list byte-identically.

## 2. Universe & scope

- **Source:** repoDB confirmed repurposing pairs (drug approved for indication B, distinct from its original indication A), cross-checked per the existing dataset-prep rules (`repodb-dataset-prep` lessons: QC-quarantine dirty drug_id/name rows before drug-keyed features; group-aware dedup by drug).
- **Drug scope — small molecules only, for the primary benchmark.** AgentBio's mechanism pool is ChEMBL IC50/Ki activity data; a biologic cannot be in that pool *by construction*. Including biologics in the primary set would measure pool coverage, not discovery ability. Biologics are reported separately (the existing N-suite) with exactly this disclosure. This is a scope decision, not a result we hide.
- **Disease scope:** rare diseases (Orphanet-defined universe), matching AgentBio's remit and outreach positioning.

## 3. Inclusion criteria (mechanical)

- **I1.** repoDB status "approved" for the benchmark indication; indication distinct from the drug's original approval indication.
- **I2.** Drug resolves to exactly one ChEMBL *Small molecule* entry and one PubChem CID. *Amendment (2026-07-31, pre-selection): pairs whose molecule_type is EMPTY in the enriched CSV (enrichment-era ChEMBL failures) are excluded as unresolved with the count disclosed in the attrition table. Live re-resolution at selection time was rejected — ChEMBL was down during the first selection attempt and the filters ground to a halt on timeouts; selection must be reproducible regardless of transient API health. No case is permanently lost: the benchmark run itself resolves molecules live, so these pairs simply aren't selectable in this draw.*
- **I3.** Disease resolves to an EFO term via the v3 specificity pipeline and appears in the Orphanet rare-disease universe.
- **I4.** The disease has at least one target in the discovery universe (Open Targets association or pharmacological precedent). A case the system cannot even start measures data coverage, not selection — such cases are counted and disclosed separately as *coverage failures*, never silently dropped and never counted as discovery failures.

## 4. Exclusion criteria

- **E1.** Biologics (see §2).
- **E2.** Combination products / drug mixtures.
- **E3.** Development-suite contamination: **every drug appearing in any development suite** (`ground_truth.json`, `run_repodb_cases.py`, `run_repodb_cases_smallmol.py`) is excluded from the primary endpoint set — the wrong-target diagnosis inspected those cases, so they are contaminated for tuning. *Amendment (2026-07-31, pre-selection): upgraded from pair-level to drug-level exclusion, because disease-name normalization differences between the suite files and repoDB could let a near-duplicate pair slip through; the cost (~15 of ~705 drugs) is negligible.* The development cases are still run and reported as a clearly-labeled *development subset* (the F2 predictions P1–P3 are scored there).
- **E4.** One case per drug: if a drug qualifies for multiple rare indications, keep exactly one (the indication with the lowest Orphanet prevalence), preventing one mechanism from being double-counted.

## 5. Sampling procedure

1. Enumerate all pairs passing I1–I4 and E1–E4. Publish an **attrition table** (count after each filter) with the case list — the filters themselves must be auditable for bias.
2. Stratify by **prevalence band of the benchmark indication** (ultra-rare <1/M, rare 1–10/M, less-rare >10/M, prevalence-unknown); no stratum may exceed 40% of the final list. *Amendment (2026-07-31, pre-selection): the original draft specified Orphanet therapeutic-area strata, but the Orphadata pipeline exposes no per-disease therapeutic-area field (only DisorderGroup/cross-references). Prevalence bands are the finest stratification available mechanically, are fully deterministic, and guard against a sample dominated by one rarity regime. Amended before any case was selected.*
3. Sample to **N = 50** (acceptable band 40–60) with a fixed RNG seed recorded in the selection script (`SEED = 20260731`).
4. Commit: selection script + seed + final list, before any run.

## 6. Endpoints & statistics (fixed a priori)

- **Primary endpoint — rediscovery:** the true drug appears anywhere in the final candidate list of the frozen run, executed under the existing retrospective holdout redaction.
- **Secondary endpoints:** (a) the drug's true target appears among selected/considered targets; (b) rank of the true drug in the candidate list.
- **Statistics:** binomial test of the observed rediscovery rate against a mechanically-defined chance rate (mean candidate-list size ÷ eligible-drug pool size); 95% confidence intervals; enrichment factor vs chance. Success threshold registered now: **the lower 95% CI bound exceeds the chance rate**.
- **Publication:** the full table — every case, hits and misses — with failures classified by the existing miss taxonomy. No cherry-picking.
- **Blinding disclosure:** the holdout seals disease-side leakage (precedent lists, indication fallback, has_approved) but **not** the bioactivity pool, and there is **no temporal holdout** (DrugCentral has no per-indication approval dates). Both are stated plainly in the published report as limitations.

## 7. Registered predictions

- Development subset: F2 predictions P1–P3 (see `f2_precedent_calibration_justification.md` §5).
- Primary set: rediscovery rate significantly above chance per §6. No magnitude promise — the number is what the single run produces.

## 8. Sequence position

F2 justification (approved) → F2 implementation + tests → **code freeze (commit/tag)** → selection script written to THIS document → case list committed → **single run** → full table + enrichment statistics published. Any deviation aborts the run rather than patching mid-flight.
