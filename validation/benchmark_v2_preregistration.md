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

## Amendment 1 (2026-08-01, committed before any v2 code) — case list, framing, dry-run, post-cutoff stratum

5. **Screened case list, target n=35–40** (replaces "same 50-case list"). Inclusion
   criteria are disease/target properties checkable OFFLINE without running the
   pipeline: (a) disease resolves to a specific, non-umbrella EFO/MONDO term;
   (b) ≥1 Open Targets genetically-associated target exists; (c) ≥1 considered target
   has a non-empty ChEMBL pool under the v2 tier definitions. Cases are NEVER selected
   or excluded based on v1 outcomes — the criteria are property-based and would have
   excluded v1's error cases regardless of what v1 did. The screen's pass rate on the
   original 50 (and on the wider repoDB universe) is computed and disclosed.
6. **Headline framing commitment.** The v2 headline number answers "how good is the
   ranking core on funnel-feasible diseases," NOT "how likely is a random new disease
   submission to yield a candidate." Both the screened hit rate AND the funnel-feasibility
   (screen pass) rate are reported in the same sentence as the headline — not as a
   footnote. A screened list systematically underrepresents sparse-data, genuinely
   neglected diseases; that scope limitation is stated plainly.
7. **Pool-forced diagnostic redefined as a v2-funnel dry-run.** The sensitivity
   experiment on v1's archived misses is executed THROUGH the actual registered v2
   code paths (tiered pools with the −0.05/−0.10 penalties, pathway-neighbor
   augmentation, unchanged deterministic scoring and caps) — not through hand-built
   "best possible" injections. It therefore tests whether the registered v2 design
   converts v1's misses into hits before the real v2 run is spent. It runs offline on
   archived cases, is published as a design-validation sensitivity analysis, and is
   never conflated with rediscovery results.
8. **Post-cutoff stratum (attempt, disclosed either way).** We will attempt to find
   3–5 confirmed repurposing pairs whose confirmation postdates frontier-model training
   cutoffs, as a separately-labeled sub-analysis ("on cases the model cannot have
   memorized, it surfaced N of M"). repoDB/DrugCentral chronology is known to be sparse
   and lagged; if too few verifiable cases exist, the attempt and its failure are
   disclosed rather than silently dropped.

## Amendment 2 (2026-08-02, before v2 implementation) — broad evidence architecture, anti-overfit rules

The five archived genuine misses are regression fixtures, not the design population. The
three-source set that recovers them is explicitly a **minimum recovery set for those five**, not
the production source ceiling. The production portfolio and source-admission gate are frozen in
`production_evidence_source_portfolio.md`.

9. **Common evidence ledger and wider mechanism object.** Before adding source wrappers, candidate
   evidence is represented as provenance-bearing assertions. The mechanism object is a tagged
   union of protein target, protein family, pathway, mechanism class, phenotype, or pathogen
   process. This is necessary for antimetabolites, phenotypic drugs, family-target pharmacology,
   and pathogen-directed drugs which cannot be represented honestly as one drug→human-protein
   edge.
10. **Multi-source union candidate generation.** Qualified evidence from ChEMBL, BindingDB,
    GtoPdb, DrugCentral, regulatory labels, and entity-anchored Europe PMC/PubTator3 may nominate a
    candidate. PubChem BioAssay, LINCS, pharmacogenomics, phenotype, and pathogen-specific lanes
    are admitted as they pass the same pre-declared source gate. Candidate generation is a union;
    one missing database cannot erase a candidate supported by another qualified lane.
11. **Upstream-lineage de-duplication.** Database appearances are not independent evidence.
    Assertions are grouped by the underlying assay, paper, label, patent, or trial. A ChEMBL record
    re-exported by BindingDB or DrugCentral receives one evidence contribution, not three.
12. **Source- and modality-aware scoring.** The current pChEMBL-shaped composite cannot assign an
    honest score to a label mechanism, phenotypic assay, pathway mechanism, biologic, or
    oligonucleotide. Evidence types are calibrated separately; inapplicable features are marked
    `not_applicable`, never zero. Safety evidence cannot raise efficacy confidence. Human-target,
    pathogen-target, and non-small-molecule candidates use explicit separate contracts until
    cross-contract calibration is demonstrated.
13. **No tuning on the five misses.** The five may test whether known structural gaps remain, but
    may not set source weights, quality thresholds, query templates, or admission decisions.
    Calibration uses a broader development corpus grouped by drug, with final benchmark cases and
    their drug identities held out. Every lane must show incremental value by ablation across
    mechanism, modality, species, and evidence-sparsity strata; precision loss is disclosed.
14. **Every lane inherits the retrospective seal and health gate.** Holdout redaction covers
    names, synonyms, salts, active moieties, source identifiers, labels, papers, and trials across
    all sources. Source states distinguish healthy-empty, filtered-empty, unavailable, degraded,
    and parse-failed. No degraded or transient empty payload is cached as biological absence.
15. **Specialized pathogen and biologic lanes.** Human-only target filtering is retained for human
    rare-disease targets but is not applied to NTD pathogen mechanisms. Species, strain/life stage,
    host context, and human-homolog selectivity are explicit. Biologics, enzymes, peptides, and
    oligonucleotides are not penalized for absent SMILES, pChEMBL, Tanimoto, or Lipinski features.

These changes expand the architecture beyond the original four-item package. They are registered
before implementation and do not authorize silent additions after benchmark execution begins.

### Pre-v2 engineering acceptance test

Before creating `benchmark-freeze-v2`, the upgraded runtime will be exercised on the five
archived v1 genuine misses under the protocol in `v2_upgrade_readiness_audit.md`. This is a
disclosed development/regression test and may be rerun while implementing general fixes; it is
never reported as benchmark v2.

The acceptance target is 5/5 **generated and mechanistically valid** through normal disease-input
and multi-source code paths with the confirmed drug fully held out. Rank, Top-10, and STRONG_MATCH
are reported separately and are not tuned to 5/5. Any change prompted by a fixture failure must be
expressed as a general rule, receive positive and negative non-fixture controls, and pass broader
drug-grouped ablation before the fixture is rerun.

## Amendment 3 (2026-08-05, committed before any v2 case executes) — operationalization + supersessions

Registered after the five-miss engineering acceptance passed 5/5 generated / 5/5 mechanistically
valid (label `engineering_acceptance`, 2026-08-05) and BEFORE the screened list exists, the freeze
tag exists, or any v2 case runs.

16. **Item 1 (tiered ChEMBL pools) is SUPERSEDED, not silently dropped.** The Amendment-2 union
    architecture (items 9–12) widened candidate generation beyond ChEMBL IC50/Ki conf ≥ 8 — the
    acceptance fixtures generated through drugcentral/europepmc/gtopdb lanes with the ChEMBL
    approved pool at zero. Tiered ChEMBL pools with the −0.05/−0.10 penalties were never
    implemented; D1 is addressed by the union lanes instead. v2's improvement attribution must
    name this substitution plainly.
17. **Screen operationalization** (frozen in `validation/screen_v2_cases.py`,
    `SCREEN_VERSION = "v2-screen-1"`, before the screen ever ran). (a) = OT re-resolution of the
    indication name (unresolved → exclude; drift vs the stored v1 EFO → exclude) + Jaccard ≥ 0.6
    OT canonical-name match + descendant count ≤ 50 (umbrella guard). (b) = non-empty OT
    associated-target list after the pipeline's Stage-1 gate (association_score ≥ 0.1).
    (c) = non-empty PRODUCTION union pool (`collect_target_candidates`,
    `repurposing_only=True`, default source set) for at least one of the first ≤ 10 gated
    targets. A lookup failure marks a case INDETERMINATE and the screen exits without writing a
    list — unavailable data is never read as biological absence (item 14). Because the OT helpers
    return None/[] on transport failure, absence verdicts (unresolved EFO, zero targets, empty
    pool) are accepted ONLY when the source probes healthy at that moment and every contacted
    union provider reports status ok / empty / disabled; anything else is INDETERMINATE (exit 3).
18. **Item 3 (error reclassification) is subsumed by the screen** for its two observed classes
    (EFO-mismatch, OT-zero-genetic infections); any residual in-run errors are still classified,
    never counted as genuine misses.
19. **Results separation.** v2 writes `validation/benchmark_results_v2.json`/`.md`; the v1
    leftover rows in `benchmark_results.json` are never merged — the runner refuses cross-freeze
    resume with exit 2.
20. **Preflight automation.** `validation/run_v2_preflight.py` chains, idempotently: source
    health probes → the source-ablation control (must complete BEFORE the freeze tag; the
    harness's post-tag refusal is preserved) → the Amendment-1 screen → creation of
    `benchmark-freeze-v2` at a clean HEAD. The benchmark-run workflow invokes preflight before
    every (re)start, so the one v2 run cannot begin on degraded sources or an untagged tree.

## Amendment 4 (2026-08-07, committed before the freeze tag exists) — GtoPdb structure-204 tolerance + blessed fingerprint transition

Registered while the source-ablation control was mid-run (38/52 arms persisted), BEFORE the freeze
tag exists or any v2 case executes.

21. **GtoPdb ligand-structure HTTP 204 is a data absence, not a source failure.** The
    /ligands/{id}/structure endpoint returns 204 No Content for approved biologics with no
    deposited small-molecule structure (observed: olaratumab 9172, tositumomab 6781,
    efgartigimod alfa 9777 — none of them a benchmark confirmed drug). Previously a single such
    204 raised source-unavailable for the whole target, deterministically poisoning any arm whose
    frozen snapshot contained MS4A1, PDGFRA, or FCGRT (8 of 52 arms could never validate under
    the strict final validation). `data_sources/gtopdb.py` now tolerates 204 on the structure
    endpoint ONLY (candidate kept, structure fields None); every other endpoint keeps the strict
    unexpected-status behavior.
22. **Blessed fingerprint transition (one-time).** Because the fix is in fingerprinted pipeline
    code, the stale-resume guard would otherwise force a full 52-arm re-run. Before registration,
    the persisted 38-row checkpoint was re-verified row-by-row: ZERO completed rows carry a
    degraded/error source stamp, so the fix is behavior-identical for every completed row; only
    re-run arms (the quarantined/stripped ones plus the not-yet-run Everolimus case) observe the
    new behavior. `run_v2_preflight.py` blesses exactly one fingerprint transition
    (e65a5374477e…), row- AND snapshot-verified again at runtime (a failed, out-of-universe,
    or invalid snapshot is never transitioned); unrecognized drift still discards.
    Rationale: preserving 38 verified rows (~5 h of healthy compute) without mixing semantics —
    a full re-run would produce equivalent rows for all completed arms.
23. **Stall watchdog (operational, not analytical).** The control was found wedged >1 h in
    row finalization (the post-target reviewer/matching phase), which the per-target 15-minute
    process bound does not cover. Preflight now kills any child module after 30 min of output
    silence and exits 3; the supervisor retries and the harness resumes from its last per-arm
    flush. This changes no data semantics.

## Amendment 5 (2026-08-07, after control+screen completion, before any v2 case executes) — deployment freeze attestation

Registered after the control completed (52/52 arms, 33 hits) and the screen wrote the case list,
BEFORE the freeze is sealed or any v2 case executes.

24. **Freeze in the published deployment.** The production deployment ships without a git
    repository, so `git tag benchmark-freeze-v2` cannot be created or verified there. In a
    deployment, the freeze is sealed by `validation/benchmark_freeze_v2_attestation.json`,
    which pins: the pipeline source fingerprint (the same fingerprint the control resume-guard
    uses — any byte change to fingerprinted pipeline code is drift), the SHA-256 of the
    completed control artifact, and the SHA-256 of the screened case list. A published
    deployment is immutable per publish, so redeploying different code necessarily fails the
    attestation check. In a git checkout (dev), the tag path is unchanged and remains
    authoritative. Results record `freeze_mode` ("git-tag" | "deployment-attestation").
25. **Post-freeze re-run refusal without git.** The ablation harness's git-tag hard-refusal
    cannot fire in a deployment; instead preflight refuses (exit 2) to run or resume the
    control or the screen whenever a freeze marker (tag or attestation) exists and the
    corresponding artifact is missing/invalid.
26. **Benchmark integrity parity.** `run_benchmark._check_freeze_integrity` verifies the
    attestation (fingerprint + both artifact hashes) when git is unavailable, with identical
    refusal semantics (exit 2) to the tag path.
