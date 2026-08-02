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
