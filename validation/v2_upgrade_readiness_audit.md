# Benchmark v2 Upgrade — Readiness Audit and Five-Miss Acceptance Protocol

**Audit date:** 2026-08-02  
**Decision:** **NOT READY FOR V2.** The upgrade architecture is pre-registered and source
feasibility has been researched, but the multi-source runtime has not been implemented.

## What exists

- The five v1 genuine misses have source-coverage diagnoses.
- The expanded production evidence portfolio and source-admission rules are documented.
- Benchmark Amendment 2 pre-registers union candidate generation, a common evidence ledger,
  upstream-lineage de-duplication, source/modality-aware scoring, broad calibration, holdout
  sealing, and specialized pathogen/biologic contracts.
- The five archived misses and their original held-out results are preserved.

## What does not exist yet

There are no executable runtime implementations for:

- the common evidence assertion ledger or canonical active-moiety identity layer;
- BindingDB ingestion;
- GtoPdb/IUPHAR ingestion;
- DrugCentral ingestion;
- Europe PMC/PubTator3 entity-linked mechanism ingestion;
- PubChem BioAssay evidence ingestion;
- LINCS L1000 signature evidence;
- source-lineage de-duplication across providers;
- source- and modality-aware fusion/scoring;
- complete cross-source holdout redaction;
- the specialized pathogen and non-small-molecule contracts.

The current Chemist/Reviewer path is still fundamentally ChEMBL/pChEMBL shaped. The only
benchmark freeze tag is `benchmark-freeze-v1`, and the v1 termination sentinel correctly blocks
another v1 run. Therefore the current machine cannot test the registered upgrade.

## Live web/source verification

Web search and official-document fetching were working on 2026-08-02. The portfolio was checked
against official source pages:

- **GtoPdb:** versioned downloads and REST services exist; database/content licenses are ODbL /
  CC BY-SA 4.0. Its official sustainability page says commercial organizations that use GtoPdb
  must pay access fees. Commercial clearance is a go-live dependency.
  - https://www.guidetopharmacology.org/download.jsp
  - https://www.guidetopharmacology.org/gtopdbSustainability.jsp
- **BindingDB:** monthly downloads and quarterly long-term archives exist, including assay
  metadata and checksums. An archived release is appropriate for a frozen benchmark.
  - https://www.bindingdb.org/rwd/bind/chemsearch/marvin/Download.jsp
- **DrugCentral:** official API/download pages and CC BY-SA 4.0 terms exist. The publicly linked
  full database dump is dated 2023, so freshness and API-versus-dump equivalence must be checked
  before it affects scoring.
  - https://drugcentral.org/download
  - https://drugcentral.org/OpenAPI
  - https://drugcentral.org/privacy
- **PubChem:** PUG REST and downloadable data exist, but PubChem explicitly notes that contributor
  licensing can prevent bulk download of some datasets. BioAssay records must preserve depositor
  and contributor-level reuse/provenance constraints.
  - https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest
  - https://pubchem.ncbi.nlm.nih.gov/docs/downloads
  - https://pubchem.ncbi.nlm.nih.gov/docs/bioassays
- **openFDA/DailyMed:** openFDA provides public FDA data with generally unrestricted reuse and
  attribution/disclaimer requirements; DailyMed provides REST services and full plus
  daily/weekly/monthly SPL releases. DailyMed SPL is the reproducible label snapshot/fallback.
  - https://open.fda.gov/terms/
  - https://open.fda.gov/license/
  - https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
  - https://dailymed.nlm.nih.gov/dailymed/spl-resources.cfm
- **Europe PMC/PubTator3:** developer APIs, bulk resources, normalized entities, and relationship
  queries exist. Bibliographic/entity metadata can drive the bounded search lane, but full-text
  reuse must respect each article's license; being indexed by Europe PMC does not make every full
  text freely reusable.
  - https://europepmc.org/developers
  - https://europepmc.org/downloads
  - https://www.ncbi.nlm.nih.gov/research/pubtator3/api
- **ClinicalTrials.gov:** API v2 exists and remains appropriate for trial status/results evidence.
  Registration alone is not efficacy evidence and source attribution/disclaimers remain required.
  - https://clinicaltrials.gov/data-api/api
  - https://clinicaltrials.gov/about-site/terms-conditions
- **LINCS L1000:** durable GEO releases exist (including GSE92742) with processed signature
  levels. The files are very large and the release is historical, so this is an expansion lane,
  not a blocker for the core five-miss acceptance test.
  - https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE92742
- **Reactome, ClinPGx, GWAS Catalog, MONDO/HPO:** official downloads/releases exist. ClinPGx
  content is CC BY-SA 4.0; GWAS releases are versioned/dated; MONDO is CC BY 4.0. Each still needs
  exact release, attribution, and upstream-lineage tracking.

This verification supports the architecture but does not substitute for implementation,
source-specific tests, legal compliance, or calibration.

## Five-miss engineering acceptance test

The five cases are valuable **development regression fixtures**:

1. Phenobarbital / Lennox-Gastaut syndrome
2. Lamotrigine / Lennox-Gastaut syndrome
3. Mercaptopurine / Acute Promyelocytic Leukemia
4. Vincristine / Rhabdomyosarcoma
5. Promazine / Acute intermittent porphyria

We should run them repeatedly while building, but the result must be labeled
`engineering_acceptance`, never `benchmark_v2`.

### Required execution conditions

- Run through the real disease-input pipeline; do not force the known target, mechanism, source,
  drug, or rank.
- Activate the full holdout using the drug's names, synonyms, salt/product forms, active-moiety
  identifiers, source IDs, labels, literature, and trials.
- Do not inject hand-built evidence rows or use a case-specific query template.
- Use the same identity matcher and ranking outputs intended for v2.
- Archive source releases/checksums and lane health for each run.
- Report every pipeline modification made after inspecting a failure.

### Outcome levels

For each case report all levels separately:

1. **Generated:** the held-out active moiety appears in the union candidate set through a
   qualified source assertion.
2. **Mechanistically valid:** the evidence object, species/context, and action direction are
   compatible with the disease hypothesis; a bare name co-mention does not qualify.
3. **Ranked:** final rank and total candidate count.
4. **Top-10:** whether the candidate appears in the first 10.
5. **Strong match:** whether it crosses the pre-registered strong-match policy without a safety,
   mechanism-direction, approval, or evidence-quality cap.

The old harness called any `found` candidate a hit and reported Top-10 and STRONG_MATCH
separately. The new acceptance report must preserve that distinction. The engineering target is
**5/5 generated and mechanistically valid**. Top-10 and STRONG_MATCH are stricter calibration
outcomes; they must not be forced to 5/5 by case-specific weight or threshold changes.

### How to learn without concealing overfitting

After each failure:

1. classify the structural failure before changing code;
2. implement only a general rule that would apply to an explicit evidence/mechanism stratum;
3. add at least one positive and one negative non-fixture case for that rule;
4. run the broader development corpus and source ablation;
5. disclose precision losses and regressions;
6. rerun all five fixtures from a clean snapshot.

Passing the five is necessary evidence that the diagnosed v1 gaps were actually repaired. It is
not sufficient evidence of general accuracy. V2 may begin only after the five-case engineering
test, broader held-out development/negative controls, holdout audit, source-health checks, and a
new `benchmark-freeze-v2` tag all pass.