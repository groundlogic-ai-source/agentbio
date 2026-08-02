# AgentBio Production Evidence-Source Portfolio

**Date:** 2026-08-02  
**Status:** architecture and feasibility decision; no frozen pipeline code changed  
**Purpose:** define the data architecture for broad repurposing accuracy, rather than optimizing
only for the five archived v1 misses.

## Executive decision

The three-source recovery set in `source_coverage_matrix.md` is a floor for five regression
fixtures, not a ceiling for AgentBio. Production candidate generation should use a **union of
orthogonal, high-quality evidence lanes**, while ranking should use source-calibrated evidence,
explicit contradictions, and upstream-lineage de-duplication.

Adding more databases can improve recall, but database count is not evidence count. ChEMBL,
BindingDB, DrugCentral, PubChem, and aggregators can contain the same assay or paper. AgentBio
must therefore count the underlying experiment, label, trial, or publication once, even when
several providers expose it.

## Coverage goals

A production portfolio must cover at least these distinct blind spots:

1. Direct quantitative binding and functional pharmacology.
2. Curated drug–target and drug–mechanism knowledge.
3. Phenotypic assays where no clean protein target is known.
4. Regulatory-label mechanisms, indications, contraindications, and modality.
5. Entity-linked primary literature for mechanisms absent from structured databases.
6. Disease biology: genetics, phenotypes, pathways, tissues, and direction of effect.
7. Clinical precedent, failures, and active trials.
8. Perturbational signatures for target-agnostic or pathway-level reversal.
9. Safety, pharmacogenomics, and dosing constraints.
10. Non-human pathogen targets for neglected tropical and infectious diseases.
11. Non-small-molecule modalities: biologics, enzymes, peptides, and oligonucleotides.

## Source portfolio

### A. Core candidate-generation and scoring lanes

| Lane | Sources | Unique value | Production policy |
|---|---|---|---|
| Quantitative pharmacology | **ChEMBL + BindingDB** | IC50/Ki/Kd/EC50 and related measurements; BindingDB adds primary-literature and patent coverage that can be absent or differently represented in ChEMBL | Use versioned bulk snapshots. Preserve assay, species, target form, units, relation operator, publication/patent, and provider provenance. De-duplicate by underlying experiment/publication; never treat the same imported ChEMBL row in BindingDB as independent confirmation. |
| Expert-curated interactions | **GtoPdb/IUPHAR** | High-precision ligand–target interaction and action data; rescued three structurally different v1 misses | Core only after commercial access terms are satisfied. Use the downloadable version, ligand/target mappings, interaction type, action, species, and primary references. |
| Approved-drug mechanisms and identity | **DrugCentral** | Curated approved-drug identity, approvals, labels, mechanisms, interactions, and literature links; useful for older drugs and modalities that are poorly represented by ChEMBL assays | Prefer the versioned dump/official API over brittle undocumented endpoints. Track each interaction's upstream source so DrugCentral does not double-count ChEMBL, labels, or literature. A currentness check is required because some public dump links are old. |
| Regulatory mechanisms | **openFDA + DailyMed SPL** | Official label mechanism, pharmacologic class, active moiety, indication, contraindication, and warning text; essential for mechanism-class and non-protein drugs | Treat openFDA as searchable access and DailyMed SPL/set ID as the canonical label record/fallback. Extract assertions deterministically with quoted supporting text. A label assertion is strong human-use evidence but not proof of efficacy in a new disease. |
| Entity-linked literature | **Europe PMC + PubTator3**, with PubMed identifiers retained | Primary literature for drug–target, drug–pathway, and drug–mechanism relationships missing from structured sources | Bounded queries must contain resolved drug entity plus resolved mechanism object. Require an identified paper, entity spans, relationship/direction screening, species, and evidence sentence. Counts alone never score. Reviews generate hypotheses; primary experiments provide supporting assertions. |
| Disease biology | **Open Targets + Reactome + Orphanet**, with UniProt/HGNC/EFO/MONDO mappings | Disease–target association, causal genetics, pathway neighbors, disease identity, rarity, and unmet need | Keep disease relevance separate from pharmacology. Imported genetics sources inside Open Targets share one lineage group rather than counting as multiple independent votes. Broad pathway neighbors are discounted and direction-checked. |
| Clinical evidence | **ClinicalTrials.gov** | Prior testing, negative/terminated trials, active studies, and translational precedent | Trial status and results can corroborate or contradict a candidate, but registration alone is not efficacy evidence. Unavailable queries fail closed. |

### B. High-value expansion lanes

These sources cover edge cases outside the five misses and should be implemented after the common
evidence ledger exists. They are not optional in the long-term product, but they require stricter
calibration than the core curated lanes.

| Lane | Sources | Edge cases covered | Guardrail |
|---|---|---|---|
| Functional and phenotypic assays | **PubChem BioAssay** | Cell-based activity, pathway phenotypes, pathogen assays, and compounds without a clean affinity record | Admit only assays with resolvable assay type, endpoint, active/inactive call, concentration, organism/cell line, and depositor. Primary-screen hits alone generate candidates at low confidence; confirmation assays are required for scoring credit. |
| Perturbational signatures | **LINCS L1000 public GEO releases** | Target-agnostic pathway reversal, transcriptional rescue, and drugs whose relevant effect is systems-level rather than single-target binding | Require a disease signature with compatible tissue/cell context, direction pre-registration, replicate quality, and held-out calibration. Signature reversal is orthogonal supporting evidence, never a standalone strong match. Use archived GEO releases, not a retiring live portal. |
| Pharmacogenomics | **ClinPGx/PharmGKB public downloads** | Genotype-dependent response, dosing, metabolism, and severe toxicity edge cases | Safety/dose personalization and contradiction lane only until separately validated for efficacy prediction. Comply with the data-usage agreement and preserve guideline/annotation level. |
| Human safety | **openFDA FAERS + label warnings** | Serious adverse-event and contraindication signals | Safety can cap or warn; spontaneous report counts never increase efficacy score and are never interpreted as incidence or causation. |
| Genetics breadth | **GWAS Catalog**, only where Open Targets lacks a usable mapped assertion | Sparse or newly released disease–locus evidence | Avoid duplicate credit for GWAS data already imported by Open Targets. Require gene-mapping provenance and direction; locus proximity alone is weak. |
| Rare-disease phenotype/ontology | **MONDO/HPO/Monarch-compatible mappings** | Syndrome manifestations, phenotype-based target context, and disease aliases missed by one ontology | Use for disease resolution and biological context, not direct drug efficacy. Record the exact ontology release and mapping path. |

### C. Required specialized lanes

#### Pathogen and neglected-tropical-disease lane

The current human-only ChEMBL target filter is appropriate for human rare-disease targets but is
structurally wrong for drugs acting on parasites, bacteria, or viruses. NTD cases require a
species-aware lane using ChEMBL and PubChem assays for the causative organism, with organism,
strain, life stage, host-cell context, and selectivity against human homologs carried explicitly.
Human-target and pathogen-target evidence must never be pooled into one target score.

This lane can use specialized organism resources only after the same source-admission checks
below. It should not be faked by relaxing the human filter globally.

#### Biologic and non-small-molecule lane

The present SMILES/pChEMBL/RDKit contract cannot fairly represent enzymes, antibodies, peptides,
or oligonucleotides. These modalities require:

- canonical active-moiety and product identity rather than an InChIKey-only key;
- sequence/product identifiers where available;
- label, DrugCentral, Open Targets, clinical, and literature evidence;
- modality-appropriate developability and administration fields;
- `not_applicable` rather than zero for pChEMBL, Tanimoto, and Lipinski features.

A missing small-molecule descriptor must not penalize a valid biologic. Modality-specific models
must remain separate until there is evidence that their scores are calibrated onto a common
scale.

### D. Research-only or excluded from core scoring

| Source/type | Policy |
|---|---|
| Generic web search or free-form LLM browsing | Never a scoring source. It may locate a primary record, but only the verified primary record enters the ledger. |
| Google Scholar search | Excluded: no stable public API, weak reproducibility, and unstable entity resolution. |
| Aggregators without row-level upstream lineage | Discovery-only. They cannot provide independent corroboration. |
| DGIdb or similar integration databases | Useful as gap detectors, not independent score credit unless the exact upstream assertion is resolved. |
| DrugBank | Do not ingest unless licensing for AgentBio's intended use is explicitly obtained and documented. |
| Broad Drug Repurposing Hub / retiring portals | Research input only until a durable, versioned successor dataset is identified and frozen. |
| Docking/structure prediction alone | Hypothesis support only. Predicted binding never substitutes for measured pharmacology or human evidence. |

## Common evidence ledger — prerequisite to adding pipelines

Every source must map into one assertion model instead of adding source-specific columns to the
current ChEMBL-shaped candidate dictionary.

### Canonical entities

- `DrugEntity`: canonical active moiety, salts/products, names, modality, InChIKey/PubChem/
  ChEMBL/DrugCentral/UNII/RxNorm-style identifiers when applicable.
- `DiseaseEntity`: Orphanet, EFO/MONDO and phenotype mappings with exact/broader-match status.
- `MechanismObject`: tagged union of `protein_target`, `protein_family`, `pathway`,
  `mechanism_class`, `phenotype`, or `pathogen_process`.

### Assertion fields

Each row records:

- subject, predicate, object, direction, and effect;
- quantitative value, unit, relation operator, assay type, and confidence where applicable;
- organism/species, tissue, cell line, pathogen strain/life stage, and modality;
- provider source **and upstream source**, source record ID, PMID/patent/label/trial ID;
- evidence sentence or raw-record locator;
- source release/version and retrieval date;
- independent-evidence group;
- quality tier, contradiction status, and holdout-redaction status.

## Fusion rules

1. **Union for generation, not intersection.** Any qualified lane can nominate a candidate.
2. **Source-calibrated ranking.** A curated action, quantitative affinity, label mechanism,
   phenotypic hit, and literature assertion are not interchangeable numbers.
3. **No database-count bonus.** Corroboration is counted by independent underlying experiments
   or authorities, not by the number of websites exposing the same record.
4. **Separate disease relevance from drug mechanism.** Strong binding to an irrelevant target
   cannot compensate for weak disease biology.
5. **Direction is mandatory.** Agonism versus antagonism, activation versus inhibition, and
   disease gain- versus loss-of-function must be reconciled before strong-match status.
6. **Contradictions survive aggregation.** Incompatible mechanisms, failed trials, label
   contraindications, and species/context mismatches remain visible and can cap a score.
7. **Missing is not zero.** Features inapplicable to a modality or evidence type are marked
   unavailable; they are not silently converted into evidence against the candidate.
8. **Safety never boosts efficacy.** Safety evidence can establish repurposing eligibility,
   disclose risk, or cap a candidate, but cannot prove therapeutic benefit.
9. **Holdout applies to every lane.** Drug names, salts, active moieties, identifiers, labels,
   papers, and trials must all be redacted during retrospective evaluation.

## Source-admission gate

No new pipeline may affect production scoring until all of the following are documented:

1. **Distinct blind spot:** the lane adds a mechanism, modality, species, or evidence class not
   adequately covered by existing lanes.
2. **Legal use:** license, attribution, share-alike, access-fee, and redistribution obligations
   are recorded. GtoPdb commercial fees are a concrete go-live dependency.
3. **Reproducibility:** a versioned bulk snapshot is preferred; otherwise raw API responses,
   release metadata, and retrieval time are archived.
4. **Stable identity:** drug, target/mechanism, disease, species, and source IDs resolve without
   relying on a raw name string.
5. **Row-level provenance:** the primary experiment, paper, label, patent, or trial is traceable.
6. **Failure honesty:** unavailable, degraded, genuine-empty, filtered-empty, and parse-failed
   states are distinct. No transient or degraded empty payload is cached as biological absence.
7. **Calibration:** thresholds and source weights are fixed on a development corpus grouped by
   drug and separated from final benchmark cases.
8. **Incremental value:** broad-corpus ablation shows useful recall or error reduction without
   unacceptable precision loss. The five v1 misses can verify regression coverage but cannot set
   thresholds or source weights.
9. **Contradiction tests:** wrong species, wrong target family, inactive controls, incompatible
   action direction, label ambiguity, and duplicate upstream records have explicit tests.
10. **Operational health:** source snapshot checksums, row counts, schema checks, and freshness
    are validated before a run; an unhealthy lane degrades visibly rather than silently.

## Implementation order

1. **Canonical entities and evidence ledger.** This prevents every new wrapper from hard-coding
   another ChEMBL-shaped exception.
2. **Identity and lineage resolution.** Active-moiety/salt/product mapping and upstream-source
   de-duplication must exist before multiple databases are fused.
3. **Core lanes:** BindingDB, GtoPdb, DrugCentral, regulatory labels, and Europe PMC/PubTator3,
   alongside the existing ChEMBL/Open Targets/Reactome/ClinicalTrials sources.
4. **Source-aware scoring and contradiction gates.** Replace the assumption that every candidate
   has pChEMBL with calibrated evidence-type and modality-specific scoring.
5. **Expansion lanes:** PubChem BioAssay, LINCS, pharmacogenomics, phenotype mappings, and
   species-specific pathogen assays.
6. **Broad ablation and regression suite.** Measure each lane alone and in combination across
   mechanism, modality, evidence-sparsity, and pathogen strata; retain the five misses as fixed
   regression cases.

## What this commits AgentBio to

AgentBio will pursue broader evidence coverage, including sources that do not help the five known
misses, when they cover a defensible edge case and pass the admission gate. It will not inflate
confidence by indiscriminately stacking databases, and it will not force biologics, pathogen
targets, pathway mechanisms, and small-molecule affinity records through one inappropriate score.