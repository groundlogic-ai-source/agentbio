# How AgentBio Works

An engineering reference for the whole system: what runs, in what order, from
which data sources, with which formulas, and where AI is (and is not) allowed
to act. Everything below names the file that implements it.

The one-paragraph mental model:

> AgentBio is a **deterministic evidence pipeline with AI-assisted
> interpretation**, not an AI that invents repurposing ideas. Public biomedical
> databases supply the evidence; plain Python computes every score, rank, cap,
> and gate; language models are confined to narrow jobs (literature
> relevance screening, cited summarization, fact-restatement prose, and a
> constrained mechanism-direction verdict); a human sign-off node is a real,
> pausing stage in the graph — not a UI decoration.

---

## 1. Runtime topology

Three processes make up the running product:

| Process | Command | Port | Serves |
| --- | --- | --- | --- |
| Web frontend | `pnpm --filter @workspace/web-frontend run dev` (Vite) | 21854 | The React UI at `/` |
| API server | `uvicorn api.main:app --port 8000` | 8000 | Everything under `/api/` and `/internal/` |
| LangGraph pipeline | not a server — runs as a background thread inside the API process | — | The six-stage case pipeline |

Code layout:

```
agents/            the five pipeline stages (pure Python + constrained LLM calls)
data_sources/      one adapter per external biomedical source (+ 2 pinned local snapshots)
cache/             SQLite response cache (cache.db) with per-source TTLs
api/               FastAPI backend: jobs, audit, triage, research registry, reports
main_graph.py      LangGraph orchestration of the stages, with durable checkpoints
artifacts/web-frontend/   React + Vite UI
validation/        frozen benchmark, audit harnesses, and their tests
docs/              this document
```

---

## 2. Request lifecycle: one case, click to report

1. **Browser → `POST /api/runs`** with `{disease_name}` (or empty for
   auto-explore). `api/main.py`.
2. **Cost guardrails fire first** (`api/guardrails.py`): per-IP sliding-window
   limit (`RATE_LIMIT_PER_HOUR`, default 3/hr) and a global daily cap
   (`DAILY_RUN_CAP`, default 50/day, counted from PostgreSQL so it survives
   restarts). Rejections are 429/503 with `Retry-After`.
3. **A job row is created** in PostgreSQL (`api/jobs_db.py`) with
   `status=queued`, `current_stage=NULL` — NULL on purpose, so the UI never
   shows stage 1 as done while it is still running.
4. **A background thread runs the LangGraph graph** (`main_graph.py`), and the
   HTTP request returns immediately with a `job_id`. The UI polls
   `GET /api/runs/{job_id}` every 4 s.
5. **After every graph node completes**, the thread writes real progress into
   the job row (`current_stage`, accumulated cost, canonical disease name,
   report path).
6. The graph **pauses at `human_review`** via LangGraph's `interrupt()`. The
   job flips to `awaiting_review`. That node performs zero API calls before the
   interrupt, so resuming can never re-spend money.
7. **`POST /api/runs/{job_id}/resume`** with `approve`/`reject`/notes resumes
   the exact checkpointed thread (`checkpoints.db` via LangGraph's
   `SqliteSaver`) and the job completes.

Two run modes, decided in the `target_selection` node:

- **Manual mode** (a disease name was given): resolve that exact disease and
  score its targets. If the name cannot be resolved safely, the job fails with
  an explicit error. The system **never silently substitutes a different
  disease**.
- **Blank mode** (no name): the highest-ranked (disease, target) pair not yet
  explored is claimed **atomically** in PostgreSQL, so two simultaneous blank
  runs cannot pick the same pair. Repeated blank runs walk down the ranked
  universe.

### Flagship hypothesis is a separate screen

Named cases can first use `POST /api/flagship/preflight`. This is a
non-metered Stage 1 screen: it resolves the disease and reviews direct target
specificity, current-treatment overlap, hypothesis scope, a testable
differentiator, and whether a falsifiable next experiment can be stated. It
creates no job and never starts candidate collection or structure prediction.

The preflight returns one of `FLAGSHIP_READY`, `CONDITIONAL_REVIEW`,
`NOT_FLAGSHIP_READY`, or `INSUFFICIENT_EVIDENCE`. Unknown evidence remains
unknown. A user may stop after the screen or explicitly continue as an ordinary
research hypothesis; the screen never auto-starts a billed run.

After candidate review, the same versioned policy runs again using the
persisted dossier evidence contract. The candidate-level hypothesis verdict is
rendered separately from `composite_score`, `STRONG_MATCH`, and
`paid_validation_eligible`. A high prioritization score is not an efficacy
probability and cannot by itself establish hypothesis readiness. The gate can
reject generic or pathway-only targets, missing candidate identity, and
unscoped or non-differentiated hypotheses. It does **not** require disease-model
efficacy, clinical efficacy, exposure, safety, or comparative-advantage evidence:
those remain explicit UNKNOWN validation states for the external work that the
flagship hypothesis is meant to initiate.

---

## 3. The six stages

The graph (`main_graph.py`, `build_graph()`):

```
target_selection → biologist → chemist → reviewer
                 → structure_validation → writer → human_review
```

### Stage 1 — Target selection (`agents/target_selection.py`)

**No AI is used for any number in this stage.** One optional LLM call at the
very end of the CLI sweep narrates the already-written table; the API path
does not use it.

Inputs: a disease (manual) or the ranked sweep output (blank).

The target universe for a disease is the union of **four explicitly labeled
discovery lanes**:

| Lane | Source | Label attached to the target | Disease-blind? |
| --- | --- | --- | --- |
| A. Genetic association | Open Targets target–disease scores, gate `association_score ≥ 0.1` | `genetic_association` | yes |
| B. Pharmacological precedent | Approved drugs linked to the disease in Open Targets → their mechanism targets in ChEMBL | `pharmacological_precedent` | uses approval data |
| B-ext. Parent-umbrella precedent | Same as B, but via a parent EFO when the subtype has no drug links — only if the parent has ≤ 100 descendant diseases | `pharmacological_precedent_via_parent_umbrella` | uses approval data |
| C. Literature mechanism class | Europe PMC disease/process literature → mechanism classes (e.g. channel families, nucleotide metabolism) | `literature_mechanism_class` | yes — never queries a drug name |
| D. Pathway neighbors | Reactome co-pathway proteins of lane A/C targets (never of lane B targets, so the lane can't rediscover the known drug through its own mechanism); fixed association 0.05, half the lane-A gate | `pathway_neighbor` | yes — protein co-participation only |

Every row is scored with two **separate** numbers that are never blended into
one opaque value at collection time:

```
tractability_score  = ( 0.40 × log-scaled ChEMBL bioactivity count (cap 500)
                      + 0.35 × (AlphaFold mean pLDDT / 100)
                      − 0.25 × prior-negative-trial indicator )
                      × Open Targets association score

unmet_need_score    = 0.7 × treatment_component + 0.3 × log-scaled prevalence
                      treatment_component = 0.0 approved treatment exists
                                          = 1.0 none exists
                                          = 0.5 unknown (never treated as evidence)
```

Ranking key: `tractability_score + unmet_need_score`. A mechanistic-convergence
cap demotes rows whose support collapses onto one mechanism (rank only —
scores unchanged).

**Top-K pursuit.** One run pursues up to `TOP_K_TARGETS` targets for the same
disease (default 5), or all targets within `TOP_K_FRACTION` of the top score
capped at `TOP_K_MAX` (default 10) when fraction mode is set. With K > 1, the
biologist and chemist run **in parallel per target** and their candidate pools
are merged before the reviewer. All K pairs are recorded as explored.

Disease-name resolution is deliberately paranoid (this code exists because of
real near-misses): exact Orphanet name → ICD-10/OMIM/MeSH cross-reference →
unique substring (obsolete entries excluded) → Open Targets EFO search →
Orphanet-code reconciliation; then a **hard stop** if Open Targets' canonical
name for the resolved EFO shares zero meaningful tokens with the Orphanet
name, and a prominent Limitations warning in the partial-overlap band.
Orphanet "Group of disorders" umbrella terms and administrative entries
("OBSOLETE:", "NON RARE IN EUROPE:") are rejected as unscoreable.

### Stage 2a — Biologist (`agents/biologist.py`)

For each pursued target:

- **BioGRID** physical/genetic interactors — always labeled *network context,
  not mechanism*.
- **PubMed** target↔disease literature, screened by a constrained LLM gate:
  the model sees one retrieved abstract and answers YES/NO whether it
  specifically discusses the relationship (temperature 0, Sonnet). The model
  never searches and never cites anything that wasn't retrieved.
- **Druggability context**: ChEMBL approved-drug count for the target (no LLM)
  plus, if ≥ 2 abstracts pass the YES/NO screen, ONE Haiku call writes a 2–3
  sentence historical-difficulty summary citing only the supplied PMIDs.
  **This block is informational: it is architecturally unable to affect any
  score** (no downstream consumer reads it for math).
- **Reactome pathway neighbors** forwarded to the chemist, tagged
  `pathway_neighbor`, with `broad_metabolic` co-pathway-only neighbors flagged.

### Stage 2b — Chemist (`agents/chemist.py`)

Builds the candidate compound pool per target:

1. **Candidate collection.** ChEMBL bioactivity (IC50/Ki, human, assay
   confidence ≥ 8 — joined from the assay endpoint, because the activity
   endpoint silently ignores that filter), plus the machine-v2 multisource
   fan-out (`data_sources/multisource_candidates.py`): **GtoPdb**, the pinned
   **DrugCentral 2023 snapshot**, and **BindingDB**, each normalized into the
   shared evidence ledger. Live API jobs run `repurposing_only=True`: the pool
   is restricted to approved/known drugs, never research tool compounds.
2. **Identity resolution.** PubChem name → InChIKey → SMILES/properties. Raw
   name matching is never trusted; salt/hydrate variants of one active moiety
   collapse via the 14-character InChIKey connectivity block and an RDKit
   salt-stripper.
3. **Bisociation (a real computed number).** RDKit Morgan fingerprints
   (radius 2, 2048 bits) and Tanimoto similarity of each candidate against the
   approved/known drugs in the working reference set. Not an LLM judgment.
4. **Rationale prose (AI, budgeted).** At most `AGENTBIO_MAX_LLM_RATIONALES`
   (default 25) candidates per pool get one constrained Sonnet call that
   restates the supplied numbers in exactly two sentences, forbidden from
   adding any fact, adjective of praise, or efficacy/safety speculation.
   Everyone else gets the deterministic template. This call is
   disclosure-only: nothing downstream parses it.
5. **Lazy pathway-neighbor expansion**: if the primary target has fewer than
   `PATHWAY_NEIGHBOR_MIN_APPROVED` (default 3) approved drugs, the chemist also
   queries the biologist's Reactome neighbors for compounds.

Every candidate carries an **evidence ledger**
(`data_sources/evidence_ledger.py`): normalized source records with a
deterministic lineage key, so the same underlying assay/PMID/label/trial seen
through two providers counts once, never twice.

### Stage 2c — Reviewer (`agents/reviewer.py`)

The scoring stage. For each candidate:

- RDKit descriptors (MW, logP, HBD/HBA, TPSA, rotatable bonds, Lipinski/Veber).
- openFDA adverse events; ClinicalTrials.gov prior trials for the exact
  drug+disease pair; ChEMBL safety flags, action type, molecule type/orality.
- PubChem XLogP (caution flag at ≥ 5 — disclosure only) and a non-oral
  biologic flag (disclosure only). Neither moves the score.

**Composite formula** (fixed reference ranges so scores compare across runs):

```
composite = 0.50 × efficacy_evidence          # ledger-based, or legacy
                                                 0.6 × norm(pChEMBL 3–10) + 0.4 × (assay confidence / 9)
          + 0.20 × Open Targets association   # direct [0,1]
          + 0.15 × Tanimoto similarity        # direct [0,1]
          + 0.15 × no_failed_trial            # 1 = looked, none found; 0 = found
          + 0.05 × qualified directional bonus
          − 0.25  if Lipinski violations > 1
```

Two honesty rules are load-bearing here:

- **Unobserved ≠ measured zero.** If a term was never measured (lookup failed,
  structure unresolvable), it is dropped from *both* sides of the weighted sum
  and the remaining terms are renormalized over the covered weight — the
  report shows the coverage fraction. A *measured* zero still counts against
  the candidate.
- **Hard caps beat the formula.** `composite = min(composite, 0.40)` when any
  of these fire, and each is disclosed as an explicit row in the report:
  - *Unapproved-compound cap* — non-approved compounds can't reach
    STRONG_MATCH.
  - *Mechanism-direction cap* — the LLM direction checker
    (`data_sources/mechanism_direction.py`, top-3 candidates, verdicts
    COMPATIBLE / DIRECTIONALLY_INCOMPATIBLE / INSUFFICIENT_INFO) caps only on
    an incompatible verdict. This gate exists because of a real archetype:
    miglitol vs. GSD1c — right pathway keywords, wrong cellular mechanism.
  - *Safety cap* — structured-source signals (layer 1) plus an independent
    web-search check on the top 3 (layer 2). Layer 2 uses the versioned
    `safety-v3` evidence contract: only an exact, in-context quote from a
    recognized regulator can confirm `WITHDRAWN_FOR_SAFETY` or
    `SAFETY_DISCONTINUED`, and the evidence must identify the active ingredient,
    jurisdiction, and affected formulation. Brand/manufacturer discontinuation,
    not-marketed or ingredient-unavailable notices, ordinary warnings, and
    formal boxed warnings remain distinct disclosure states and cannot confirm
    safety withdrawal. Contrary evidence (for example, available generics or
    an explicit statement that no formal withdrawal occurred) yields
    `CONFLICT`, never a confirmed result. Glyburide and glibenclamide are
    normalized as the same active-ingredient identity. Legacy Layer 2 fields
    remain compatibility projections of this richer record.

**Post-benchmark literature-limitation gate.** Before a candidate can proceed
to paid structure validation, the top three candidates receive a separate
retrieval-first PubMed screen (`data_sources/literature_limitation.py`). This
asks whether applicable guidelines, systematic reviews, clinical reports, or
mechanism-specific studies explicitly limit the proposed drug/class for the
exact disease and intended use. It is deliberately separate from target-level
direction: a target action can look directionally plausible while the class is
known not to correct the relevant gating, trafficking, compartment, or clinical
problem.

The model sees one bounded batch of at most eight retrieved abstracts per
candidate and may only return labels and exact quotations keyed to supplied
PMIDs. Python verifies that each quotation is verbatim, the PMID was retrieved,
and the cited title/abstract independently contains the exact disease,
specific drug or sufficiently specific class/mechanism, and meaningful
intended-use terms; target presence or one generic use term is not enough, and
model-supplied match booleans are never sufficient. A block
requires either one PubMed-typed guideline/systematic-review/consensus source or at least two
independent explicit PubMed records. One low-authority record is caution only;
supporting and limiting records produce a visible conflict; source failure is
unknown and closes the paid gate without asserting ineffectiveness. A confirmed
limitation preserves the evidence score, withdraws any coarse directional
bonus, and separately marks the candidate not externally prioritizable. Frozen
holdout studies bypass this post-benchmark gate so their semantics are never
changed retroactively.

**Post-benchmark EFO cross-reference admission (added 2026-09-23).** Stage 1
hard-stops when the Orphanet disease name and Open Targets' canonical name for
the resolved EFO/MONDO node share no meaningful tokens, on the reasoning that a
zero-overlap resolution has probably landed on a different disease entirely.
That heuristic cannot distinguish a wrong resolution from the same disease
named differently in two ontologies, which is routine in rare disease
nomenclature — Orphanet often carries a descriptive name where EFO/MONDO
carries an eponym or classification name. ORPHA:88660 is the worked example:
Orphanet's "Hypertension due to gain-of-function mutations in the
mineralocorticoid receptor" versus MONDO_0011517's "pseudohyperaldosteronism
type 2" — zero shared tokens, yet Orphanet lists the second as a synonym of the
first and the MONDO node cross-references ORPHA:88660 directly.

Before hard-stopping, Stage 1 now asks whether the resolved node's own Orphanet
cross-reference matches the Orphanet code that was resolved. An explicit
ontology cross-reference establishes identity more reliably than name
similarity, so a match admits the pair. The check is one-directional: it can
only admit pairs the name heuristic would have rejected, never reject a pair it
accepted, and it touches no score. A missing, unreadable, or non-matching
cross-reference leaves the name heuristic in force, so genuine wrong-disease
resolutions still stop.

This is disclosed as a post-benchmark modification. The frozen v1 benchmark
contains two rows — Trichinellosis/Prednisone and Trichinellosis/Triamcinolone
— recorded as `status="error"` precisely because this hard stop fired on the
same zero-shared-token synonym problem ("Trichinellosis" versus "trichinosis").
Admitting those pairs would make the frozen artifacts non-reproducible from
current code, so frozen and holdout studies bypass the cross-reference check and
keep the original name-only semantics, exactly as the literature-limitation gate
above does. No frozen benchmark result changes.

**Post-benchmark scoping of the persistence coverage gate (added 2026-09-24).**
Required-source coverage is assessed per candidate and is deliberately scoped by
target role: a failure on a direct disease target fails every candidate, while a
failure on an exploratory pathway-neighbor target is meant to fail only the
candidates discovered on that neighbor. Stage 3 marks neighbors
`coverage_required=False` for exactly this reason, and the reviewer honours it —
an incompletely covered candidate carries `candidate_source_coverage_incomplete`
among its `exclusion_reasons` and is barred from being promoted.

The gate that freezes a report as actionable did not honour that scoping. It
required *every pooled* candidate to be completely covered, which made an
exploratory neighbor's failure fatal to the whole run. A single transient HTTP
429 from GtoPdb on NR3C1 — a pathway neighbor contributing 6 compounds to a
210-candidate pool, on a case whose promoted candidate sat on the direct target
NR3C2 with complete coverage — discarded a finished run after full LLM and
structure-validation spend.

The gate now requires complete coverage only of candidates the report actually
advances (`paid_validation_eligible`, `headline_eligible`, or
`externally_prioritizable`). Candidate dossier-contract versioning remains
pool-wide; only coverage was rescoped. The invariant that matters is unchanged:
nothing a report promotes may rest on incomplete evidence. A report that
promotes no candidate — "no candidate can be authorized" — remains a legitimate
and citable outcome.

No frozen benchmark result changes, and no holdout bypass is needed here: this
gate lives in the API's run-persistence path (`api/main.py`), which no frozen
validation harness executes. It governs whether a finished report may be frozen,
never how any candidate is scored. Regression cover:
`validation/test_promoted_candidate_coverage_gate.py`.

**Post-benchmark leader-convergence direction pass (added 2026-09-24).** The
mechanism-direction check is bounded: it screens the top
`MAX_MECHANISM_DIRECTION_CANDIDATES` chemically distinct candidates, then, after
caps force a re-rank, up to that many newly promoted ones. Both passes spend
their budget on the candidates they cap, and every cap re-sorts the pool. A pool
whose entire head is directionally incompatible therefore exhausts the budget
capping it and leaves the lead to a candidate no pass ever reached.

The NR3C2 S810L run is the worked example. The check correctly capped six
steroid agonists in sequence — progesterone, dexamethasone, prednisolone,
spironolactone, and both desoxycorticosterone esters — exhausting the 2×3 call
budget. The list re-sorted a final time and promoted a seventh compound,
drospirenone, which carries no mechanism-direction record at all. The hole opens
precisely *because* the gate is working, and it opens onto the one candidate
whose screening matters most: the one the report will promote.

A third pass now re-checks the promotable leader until it has been checked,
bounded by `MAX_DIRECTION_LEADER_PASSES`. Each iteration checks exactly one
candidate and an incompatible verdict caps it out of `strong_match`, so the
list of uncapped leaders strictly shortens and the loop converges. The
three passes share one `_direction_check_candidate` helper so they cannot drift
apart in how a drug's action on the evaluated target is labeled.

This is disclosed as a post-benchmark modification, and frozen/holdout studies
bypass the pass entirely (`_holdout.is_active()`), exactly as the
literature-limitation and EFO cross-reference gates above do, so frozen
benchmark semantics are unchanged and no frozen result moves. The bounded
passes themselves are untouched. Regression cover:
`validation/test_direction_leader_convergence.py`.

Note that this closes a *coverage* hole, not a reasoning one. The check remains
literature-anchored, so its power is greatest on drug-disease pairs someone has
already written about and weakest on genuinely novel ones — which is the
opposite of where a discovery system most needs it. A candidate whose class
behaviour inverts for a mutation-specific reason that no source states
explicitly will still return `INSUFFICIENT_INFO` and, by design, pass uncapped.

**Post-benchmark target-attribution ranking (added 2026-09-26).** A drug is
pooled once per target it was retrieved for, across the primary target and its
pathway neighbors. Those rows are not interchangeable: one may carry a measured
Open Targets association and a qualified assay, another only a precedent-stamped
constant with no assay row at all. They were previously ranked independently, so
the thinner row could win.

Two consecutive runs of the same NR3C2-style case make the effect concrete. The
first attributed capivasertib to **AKT2** — the gene that causes the disease —
via `genetic_association`, association 0.787, pChEMBL 8.10 at assay confidence
9, and scored 0.8183. The second attributed the same drug to **AKT1**, a pathway
neighbor, with no qualified ChEMBL row, a `tractability_score` of 0.0 and the
association excluded as a stamped constant — and scored **0.8256**. The dossier
named the right drug against the wrong gene, cited AKT1 literature to justify
it, and scored higher for carrying less evidence.

Ranking now resolves attribution before ordering: where the same compound
appears at several target tiers (`causal_anchor` > `clinical_precedent` >
`exploratory_expansion`/`unattributed`), the weaker-tier rows are demoted below
the strongest one. Compound identity is the InChIKey connectivity layer, so salt
and stereo variants group together. This is rank-only — no score changes, the
same discipline as the causal-anchor demotion — and frozen/holdout studies
bypass it via `_holdout.is_active()`.

**Post-benchmark Tanimoto reference scoping (added 2026-09-26).** Tanimoto
similarity is defined, in both `reference_set_note` and the reader's guide, as
similarity to approved drugs acting on *the candidate's own target*. The
implementation built one flat reference set from every approved compound in the
pooled multi-target set, so one target's approved drugs served as references for
another target's candidates. In the AKT2 case the nearest "approved drug for
this target" was reported as **CLOZAPINE**, which has zero AKT2 activity records
in ChEMBL and entered the pool on a neighbor. The value feeds a weighted scoring
term, so the mismatch moved scores: it contributed 0.000 in the first run and
0.191 in the second.

The reference set is now keyed by target symbol and a candidate is only ever
compared within its own target. `approved_reference_set_size_by_target` records
the per-target reference counts so a thin reference set is visible rather than
silently producing a low similarity. Regression cover for both corrections:
`validation/test_target_attribution_and_reference_scope.py`.

**Post-benchmark Tanimoto coverage semantics (added 2026-09-26).** Scoping the
reference set exposed a second defect it had been masking. The chemist emitted
`tanimoto_score = 0.0` both when a candidate was genuinely dissimilar to the
approved drugs at its target and when the target had no approved drug to compare
against at all. The reviewer has always kept those apart — `None` drops from
both sides of the weighted sum, a measured `0.0` is adverse structural evidence
and stays in the denominator — but it never received a `None` to act on.

The cost lands on exactly the targets a repurposing pipeline cares about. AKT2
has one approved drug with a known mechanism, capivasertib, which is the
candidate itself; there is no same-target analog in existence to compare it to.
That unscorable term was consuming a full 0.15 of weight, so the candidate was
penalised for being the only approved drug at its target. The chemist now emits
`None` when no reference drug was found, and the term is excluded rather than
scored as a measured zero.

**Post-benchmark direction-check failure caching (added 2026-09-26).** A real
verdict is cached for 30 days; failures were cached for one day. That let a
transient outage silently degrade later runs: during an Anthropic credit
exhaustion the check for an AKT2 candidate failed, `INSUFFICIENT_INFO` was
memoized, and the next run that day read the cached failure without calling the
model — losing the qualified directional bonus and producing a dossier 0.05
lower for a reason unconnected to the biology. Failures are now not cached at
any TTL: "not determined" must be retried, never remembered. Successful verdicts
still cache for 30 days. Regression cover:
`validation/test_direction_failure_not_cached.py`.

`STRONG_MATCH` = composite ≥ 0.70 **and** no cap. The pipeline keeps both
`pre_cap_score` and the capped `composite_score`, so "weak candidate" is
distinguishable from "strong candidate blocked by a gate".

### Stage 3a — Structure validation (`main_graph.py`, `structure_validation_node`)

Only for selected candidates (strong matches, capped at
`STAGE3_MAX_CANDIDATES`, default 3 — and hard-capped at **1** when K > 1, a
cost guardrail):

- **AlphaFold DB** apo-structure confidence for the candidate's *own* UniProt
  accession (pathway-neighbor candidates fold their own protein, never the
  primary target's).
- **Boltz API** (paid): protein–ligand complex prediction from the UniProt
  sequence + ligand SMILES → structure/binding-pose confidence, predicted
  affinity, CIF file (cached locally, served at `/api/structures/{file}`);
  plus ADME predictions (lipophilicity, permeability, solubility). Cost is
  summed into the job row.

These structure and ADME outputs do **not** establish therapeutic
applicability. AgentBio does not score whether a candidate reaches the
relevant tissue, cell, or subcellular compartment at an effective, tolerable
human exposure. It also does not infer compatibility from a missing result.
Route, dose, human pharmacokinetics, disease stage or subtype, and the
therapeutic window remain explicit expert-review questions. This is a general
limitation rather than a blood-brain-barrier-specific rule, and it introduces
no new score, cap, or gate.

### Stage 3b — Writer (`agents/writer.py`)

Compiles one Markdown dossier per selected candidate into `output/reports/`,
with five sections — hypothesis summary, evidence table, full citations
(deduplicated PMIDs / ChEMBL activity IDs / NCT numbers), the **complete
composite breakdown** (every term, weight, contribution, penalty, cap,
coverage note), and limitations — plus a static sixth section, **"How to read
this dossier"**: a versioned reader's guide that explains the format and
vocabulary only. It is deliberately claim-free (no candidate-specific
content), so it cannot introduce an unverifiable statement into an otherwise
claim-audited document. The writer invents nothing: it restates
numbers already computed, and it re-derives the breakdown from the candidate's
own `score_components` so the arithmetic is auditable against
`reviewed_candidates.json`. Every generated dossier also carries a prominent
therapeutic-applicability disclosure near the top and repeats the boundary in
the reader's guide: **unknown must not be interpreted as compatible**.

Flagship dossiers also carry the versioned
`flagship-dossier-evidence-v1` handoff. It is a deterministic view over
already collected evidence — it makes **no additional provider or LLM calls**.
It records an evidence-stage verdict and scientific-readiness state, disease /
mechanism context, ledger-native assay rows, target-approved and reviewed-pool
comparators, individual ClinicalTrials.gov rows, and a safety/applicability
matrix. Missing data is rendered as explicit `UNKNOWN`, `NOT ASSESSED`, or
`NOT YET AVAILABLE`; it is never silently converted to a favorable state.
The score section stamps the formula and safety-schema versions and replays the
persisted component arithmetic, including coverage renormalization and caps.

### Stage 3c — Human review (`main_graph.py`, `human_review_node`)

The graph interrupts. A person approves, rejects, or annotates. The decision
is persisted with the job. Every dossier is labeled a machine-generated
hypothesis for expert review — the system does not call itself clinically
validated anywhere.

Note the ordering: this checkpoint sits **after** structure validation. Boltz
spend has already happened by the time a human is asked — the checkpoint gates
*completion* of the run (whether the dossier is accepted into the record), not
the expensive computation. The `human_review` node itself makes zero API
calls before the interrupt, so resuming can never re-spend money.

---

## 4. Data sources: exactly what each contributes and when it runs

| Source | Adapter | What it contributes | When it runs |
| --- | --- | --- | --- |
| Orphadata / Orphanet | `orphadata.py` | The disease universe (~11.4k rare diseases + WHO NTDs), official names, ORPHA codes, cross-references, prevalence, group-of-disorders flags | Stage 1, always |
| Open Targets | `open_targets.py` | EFO resolution, target–disease association scores, approved-treatment status, parent/descendant ontology walks | Stage 1, always |
| ChEMBL | `chembl.py` | Bioactivity counts (tractability), mechanism-of-action precedent targets, the candidate compound pool, approved drugs per target, safety flags, action types, molecule type/orality | Stages 1–2, always (small-molecule lanes) |
| AlphaFold DB | `afdb.py` | Mean pLDDT (tractability term); apo structure pre-check | Stage 1 + Stage 3, whenever a UniProt ID exists |
| ClinicalTrials.gov | `clinicaltrials.py` | Prior/negative repurposing trials (Stage 1 penalty, Reviewer term, Writer citations) | Stages 1, 2c, 3b — always |
| PubMed (E-utilities) | `pubmed.py` + `literature_limitation.py` | Abstracts for target–disease literature, druggability history, and the pre-structure exact-use limitation gate | Biologist always; Reviewer bounded top-3 gate |
| Europe PMC | `europepmc_mechanisms.py` | Path C literature mechanism-class targets | Stage 1, always |
| BioGRID | `biogrid.py` | Physical/genetic interactors (network context) | Biologist, always (needs `BIOGRID_API_KEY`; degrades gracefully) |
| Reactome | `reactome.py` | Pathway-neighbor proteins (Path D) and chemist expansion neighbors | Conditional: universe expansion + when a target's approved-drug pool is thin |
| PubChem | `pubchem.py` + pinned `pubchem_snapshot.sqlite` | Name → InChIKey → SMILES/properties identity chain; XLogP; known-drug status | Chemist + Reviewer, per candidate |
| openFDA | `openfda.py` | Adverse-event counts, label indications, label mechanism | Reviewer always; chemist mechanism-only lane conditionally |
| UniProt | `uniprot.py` | Protein sequence for complex prediction | Stage 3, selected candidates only |
| Boltz | `boltz_api.py` | Paid complex structure/binding/affinity + ADME predictions | Stage 3, selected candidates only (hard caps) |
| GtoPdb | `gtopdb.py` | Machine-v2 candidate lane (curated ligand–target interactions) | Chemist multisource fan-out; per-lane disable-able |
| DrugCentral | `drugcentral_v2.py` / `drugcentral_local.py` + pinned `drugcentral_2023_snapshot.sqlite` | Machine-v2 candidate lane (approvals, activities, indications) from a SHA-256-pinned local snapshot — fail-closed, `DRUGCENTRAL_FORCE_LIVE=1` escape hatch | Chemist multisource fan-out |
| BindingDB | `bindingdb.py` | Machine-v2 candidate lane (nM affinities; moiety identity via fragment-parent canonical SMILES — this environment's RDKit has no InChI support) | Chemist multisource fan-out |
| PubTator | `pubtator_assertions.py` | Literature assertion extraction for the audit lanes | Audit path, not the case pipeline |
| Web search | `safety_check.py` | Safety layer 2: independent withdrawal/black-box check | Reviewer, top-3 candidates only |
| Anthropic / OpenAI | `llm_failover.py` + per-agent clients | The constrained AI calls listed in §5 | Per call site, with failover |

Every adapter is **cache-first** (`cache/cache.py`): a SHA-256 key over
(function name + arguments), per-source TTLs, SQLite in WAL mode with a
bounded-lock hybrid writer so a stuck SQLite handle can never freeze the
network lanes. Two cache rules are hard-won and enforced: **transient failures
are never cached**, and a **degraded 200-with-empty-payload is a failure**,
not a confirmed negative — empty pools get purged by content, not trusted.

---

## 5. Exactly where AI acts — and its leash

| # | Call site | Model | Job | Hard constraints |
| --- | --- | --- | --- | --- |
| 1 | Biologist literature screen | Sonnet, temp 0 | YES/NO: does this retrieved abstract specifically discuss the target–disease relationship? | One abstract in, verdict out; the model cannot cite anything not retrieved |
| 2 | Biologist druggability summary | Haiku, temp 0 | 2–3 sentences of historical-difficulty context | Only if ≥ 2 abstracts passed gate 1; may only use supplied abstracts + one ChEMBL fact; **cannot affect any score** |
| 3 | Chemist rationale | Sonnet, temp 0 | Restate a candidate's measured numbers in exactly two sentences | Budget-capped (default 25/pool); fact-list prompt banning praise/speculation; disclosure-only — nothing parses it |
| 4 | Mechanism-direction check | LLM via `mechanism_direction.py` | COMPATIBLE / DIRECTIONALLY_INCOMPATIBLE / INSUFFICIENT_INFO | Top-3 candidates only; only INCOMPATIBLE acts (cap at 0.40); verdict + reason disclosed in the report |
| 5 | Literature-limitation extraction | LLM via `literature_limitation.py` | Classify a bounded batch of retrieved PubMed abstracts and copy exact limiting/supportive passages | One call per top-3 candidate, at most 8 abstracts each; Python verifies quote + PMID + exact applicability and applies the multi-source/source-authority threshold; failures never become negative findings |
| 6 | Stage-1 CLI narration | Sonnet | Plain-English summary of the already-written top-30 table | Post-hoc; references only numbers already on disk; not used by the API path |

What the AI **never** does: calculate any score, rank, or similarity; decide
caps, literature blocks, or STRONG_MATCH; resolve disease identity; invent or select citations;
override a gate; or mark anything clinically validated. All AI clients honor
spend guardrails (`AGENTBIO_MAX_LLM_RATIONALES`, prefetch worker caps), and
provider-level hard spend limits are documented as the required backstop in
`api/guardrails.py`.

### LLM routing tiers and telemetry

`data_sources/llm_failover.py` keeps the existing capable defaults unchanged:
standard/critical text calls use Sonnet 4.6 and GPT-5.4. The module also
defines an **opt-in** `cheap` tier (`AGENTBIO_ANTHROPIC_CHEAP_TEXT_MODEL` and
`AGENTBIO_OPENAI_CHEAP_TEXT_MODEL`) for future low-risk classification or
narration; no current critical or default call is routed there.

Each shared failover attempt emits a structured `llm_call` telemetry event with
provider, model, operation label, attempt, latency, success/error class, and
SDK token usage when supplied. It never emits prompt text, response text,
credentials, or error payload text. Token counts are not USD: an actual cost
calculation requires the provider's applicable input/output (and any tool)
rates plus returned usage for that call.

Before every shared call, a process-wide scheduler admits each provider at no
more than 2 concurrent calls and one new call every 0.25 seconds by default.
Self-hosters can tune `AGENTBIO_LLM_MAX_CONCURRENT_PER_PROVIDER` and
`AGENTBIO_LLM_MIN_INTERVAL_SECONDS`; each provider receives its own independent
limit rather than sharing one global request slot.

---

## 6. Persistence: four stores, four jobs

| Store | Technology | Holds | Durability rule |
| --- | --- | --- | --- |
| Job store | Replit PostgreSQL (`api/jobs_db.py`) | Jobs, status, stage, cost, decisions, `explored_targets` | Durable across deploys; schema owned by dev DB + Publish diff (no startup DDL); seeded once from `api/seed_jobs.json` on an empty DB |
| Graph checkpoints | SQLite `checkpoints.db` (LangGraph `SqliteSaver`) | Full graph state per thread | Enables pause/resume without re-spend |
| Response cache | SQLite `cache/cache.db` | External API responses with TTLs | Best-effort; a lost write only costs a refetch; failures never cached |
| Materialized artifacts | `output/*.json`, `output/reports/*.md` | Stage outputs and dossiers | Target-specific artifacts are invalidated whenever the selected target changes, so a new target can never inherit the previous one's biology |

Handoff integrity is enforced at runtime by `agents/schemas.py`: after the
chemist and reviewer stages, required fields are checked and loudly logged
(hard-fail with `STRICT_VALIDATION=true`). This exists because field-dropout
bugs were found three times the hard way.

---

## 7. Configuration knobs (self-hosting)

The hosted public instance keeps conservative caps because every run spends
real money. Self-hosters can widen them via environment variables — **all
overrides are logged loudly at startup, and weight overrides are stamped into
the run's output and disclosed in every dossier** (see the banner in the
score breakdown), because a run with non-default weights is not comparable to
the frozen benchmark.

| Variable | Default | Effect |
| --- | --- | --- |
| `TOP_K_TARGETS` | 5 | Targets pursued in parallel per disease |
| `TOP_K_FRACTION` / `TOP_K_MAX` | 0 / 10 | Score-relative target inclusion instead of a fixed K |
| `STAGE3_MAX_CANDIDATES` | 3 | Paid structure predictions per run (1 when K > 1) |
| `STAGE3_BOLTZ_SAMPLES` | 1 | Boltz samples per prediction |
| `STAGE3_STRONG_ONLY` | 0 | If 1, never write a below-threshold demonstration report |
| `STAGE3_FORCE_RECOMPUTE` | 0 | If 1, ignore cached stage artifacts (CLI path) |
| `PATHWAY_NEIGHBOR_MIN_APPROVED` | 3 | Approved-pool size below which pathway-neighbor expansion triggers (0 = never) |
| `AGENTBIO_MAX_LLM_RATIONALES` | 25 | LLM rationale budget per pool (0 = all templated, −1 = unbounded) |
| `AGENTBIO_LLM_MAX_CONCURRENT_PER_PROVIDER` | 2 | Shared LLM scheduler concurrent-call limit per provider |
| `AGENTBIO_LLM_MIN_INTERVAL_SECONDS` | 0.25 | Shared LLM scheduler minimum interval between starts per provider |
| `AGENTBIO_ANTHROPIC_CHEAP_TEXT_MODEL` / `AGENTBIO_OPENAI_CHEAP_TEXT_MODEL` | Haiku 4.5 / GPT-5 mini | Opt-in low-risk routing tier only; current default/critical calls are unchanged |
| `AGENTBIO_PREFETCH_WORKERS` | 8 | Reviewer prefetch concurrency per source |
| `AGENTBIO_DISABLE_V2_LANES` | — | If 1, restore machine-v1 pool semantics (ChEMBL-only) |
| `AGENTBIO_TRACTABILITY_WEIGHTS` | `{"chembl_log_count":0.40,"afdb_plddt":0.35,"trial_penalty":0.25}` | JSON object overriding Stage-1 tractability weights |
| `AGENTBIO_COMPOSITE_WEIGHTS` | `{"efficacy_evidence":0.50,"ot_association":0.20,"tanimoto":0.15,"no_failed_trial":0.15}` | JSON object overriding Reviewer composite weights |
| `RATE_LIMIT_PER_HOUR` | 3 | Per-IP new-case limit (hosted cost guardrail) |
| `DAILY_RUN_CAP` | 50 | Global new-case cap per UTC day (hosted cost guardrail) |
| `STRICT_VALIDATION` | — | If true, handoff schema problems hard-fail the run |
| `DRUGCENTRAL_FORCE_LIVE` | — | If 1, bypass the pinned DrugCentral snapshot (not recommended) |

Required secrets for a full run: Anthropic (via Replit AI Integrations or
`AI_INTEGRATIONS_ANTHROPIC_*`), `BIOGRID_API_KEY`, `BOLTZ_API_KEY`,
`OPENFDA_API_KEY`, plus `DATABASE_URL` for the API.

---

## 8. Validation posture (why the numbers can be cited)

The pipeline's claims rest on frozen, provenance-checked artifacts in
`validation/`, not on the live system:

- A **pre-registered, holdout-redacted retrospective benchmark** (case list
  frozen under a git tag *before* the run; results SHA-256-pinned;
  `python3 validation/verify_v2_provenance.py` re-checks tag/blob identity,
  pre-run dates, row identity, and funnel arithmetic — 8 checks).
- **Audit claim-set studies**: v1 FAILED honestly and stays published
  unedited; v2 passed and is the result of record.
- Frozen studies are never rerun or regenerated; post-freeze hardening ships
  as amendments with the results hash untouched.

A self-hosted run with overridden weights or lanes is a *different instrument*
— that is why the disclosure banner exists rather than a silent knob.
