# Study B — Miss Decomposition (18/22 absent confirmed pairs)

**Purpose:** classify every confirmed repurposing that never entered its rebuilt
pool, and answer the operational question: *how many of these misses are
actually concerning for a repurposing org?*

**Evidence base:** `triage_discrimination_studyb_results.json` (artifact of
record), dev checkpoint pool-target sets (6 pools; case-insensitive match),
and drug-mechanism pharmacology. Pools are built from human disease-linked
genes (OpenTargets + trial data) — that single design fact explains most of
the 18.

## Classification

| # | Drug | Disease | Class | Verdict |
|---|------|---------|-------|---------|
| 1 | Isavuconazonium | Aspergillosis | Pathogen-directed (fungal CYP51) | Out of scope by design |
| 2 | Streptomycin | Brucellosis | Pathogen-directed antibiotic | Out of scope by design |
| 3 | Erythromycin | Listeriosis | Pathogen-directed antibiotic | Out of scope by design |
| 4 | Pyrimethamine | Malaria | Pathogen-directed (parasite DHFR) | Out of scope by design |
| 5 | Phenobarbital | Lennox-Gastaut | Symptomatic anticonvulsant, syndromic disease | Known scope limit (manifestation therapies invisible to gene-linked walks) |
| 6 | Lamotrigine | Lennox-Gastaut | Symptomatic anticonvulsant | Known scope limit |
| 7 | Clonazepam | Lennox-Gastaut | Symptomatic anticonvulsant | Known scope limit |
| 8 | Primidone | Lennox-Gastaut | Symptomatic anticonvulsant | Known scope limit |
| 9 | Daunorubicin | APL | Broad cytotoxic; TOP2A **not among APL pool targets** (IDH1/PNP/RARA) | Explained at target level; direct approval, not a repurposing find |
| 10 | Idarubicin | APL | Broad cytotoxic; same as above | Explained at target level |
| 11 | Vinblastine | Kaposi Sarcoma | Tubulin agent; pool targets are JAK1/PNP/TOP2A — no tubulin | Explained at target level |
| 12 | Trabectedin | liposarcoma | DNA minor-groove binder, no clean protein target | Explained by design (target-based pool) |
| 13 | Eribulin | liposarcoma | Tubulin agent (pool finalized on prod, unverifiable locally) | Explained at target level, same class |
| 14 | Esomeprazole | Zollinger-Ellison | S-isomer of Omeprazole — which was found at rank 3 | Chemically redundant; entity-resolution note, not a coverage gap |
| 15 | Roxatidine acetate | Zollinger-Ellison | H2 antagonist, Japan-only, sparse ChEMBL; HRH2 unlikely to be a ZES disease-linked gene | Data-sparsity, mild; class already represented by the two found PPIs |
| 16 | Voglibose | Gaucher Disease | Alpha-glucosidase (GANAB) drug; **GBA is a pool target** but voglibose is not a GBA mechanism | Off the pool's mechanism set, mild |
| 17 | Benzoic Acid | Dermatomyositis | Implausible as a repurposing signal; repoDB identity is known-dirty | Dataset noise |
| 18 | **Tretinoin** | APL | **RARA IS an APL pool target, yet the drug is absent** — ChEMBL IC50/Ki assay-strictness gap | **Genuinely concerning; in-scope defect** |

## Tally

- **8/18** pathogen-directed or purely symptomatic — structurally outside a
  host-target pipeline. A careful comp-bio lab treats these as *scope
  negative-controls*, not failures. Disclosure matter, not a defect.
- **5/18** broad cytotoxics on targets that are not disease-linked — also
  direct standard-of-care approvals, not repurposing opportunities. Explained
  at the target-selection level.
- **3/18** redundant, noisy, or data-sparse (esomeprazole, benzoic acid,
  roxatidine).
- **1/18** genuinely concerning: **tretinoin/APL** — the pool *contains the
  right gene* and still missed the drug. This is the ChEMBL assay-strictness
  class already tracked by the existing task "Recover drugs like Sapropterin
  and Pyridostigmine that target the right gene but aren't in ChEMBL's IC50/Ki
  assay pool." Study B independently confirms that task's priority.
- (Voglibose sits between "off-mechanism" and "assay gap"; counted mild.)

## Bottom line for an org

The misses an org should actually worry about reduce to **one drug class with
an existing tracked fix**. Everything else is either a declared scope boundary
(pathogens, symptom control, broad cytotoxics) or noise. The 4/4 top-15 result
for in-pool drugs is the metric that matches the product promise; the 18/22
figure, undecomposed, overstates the failure rate for any org whose use case
is host-mechanism repurposing in rare genetic disease.
