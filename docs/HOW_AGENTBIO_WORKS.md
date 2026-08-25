# How AgentBio Works

An engineering reference for the whole system: what runs, in what order, from
which data sources, with which formulas, and where AI is (and is not) allowed
to act. Everything below names the file that implements it.

The one-paragraph mental model:

> AgentBio is a **deterministic evidence pipeline with AI-assisted
> interpretation**, not an AI that invents repurposing ideas. Public biomedical
> databases supply the evidence; plain Python computes every score, rank, cap,
> and gate; language models are confined to four narrow jobs (literature
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
  - *Safety cap* — withdrawn/black-box signals from structured sources (layer
    1) plus an independent web-search check on the top 3 (layer 2).
    Disagreements between layers are preserved as a visible audit object.

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

### Stage 3b — Writer (`agents/writer.py`)

Compiles one Markdown dossier per selected candidate into `output/reports/`,
with exactly five sections: hypothesis summary, evidence table, full citations
(deduplicated PMIDs / ChEMBL activity IDs / NCT numbers), the **complete
composite breakdown** (every term, weight, contribution, penalty, cap,
coverage note), and limitations. The writer invents nothing: it restates
numbers already computed, and it re-derives the breakdown from the candidate's
own `score_components` so the arithmetic is auditable against
`reviewed_candidates.json`.

### Stage 3c — Human review (`main_graph.py`, `human_review_node`)

The graph interrupts. A person approves, rejects, or annotates. The decision
is persisted with the job. Every dossier is labeled a machine-generated
hypothesis for expert review — the system does not call itself clinically
validated anywhere.

---

## 4. Data sources: exactly what each contributes and when it runs

| Source | Adapter | What it contributes | When it runs |
| --- | --- | --- | --- |
| Orphadata / Orphanet | `orphadata.py` | The disease universe (~11.4k rare diseases + WHO NTDs), official names, ORPHA codes, cross-references, prevalence, group-of-disorders flags | Stage 1, always |
| Open Targets | `open_targets.py` | EFO resolution, target–disease association scores, approved-treatment status, parent/descendant ontology walks | Stage 1, always |
| ChEMBL | `chembl.py` | Bioactivity counts (tractability), mechanism-of-action precedent targets, the candidate compound pool, approved drugs per target, safety flags, action types, molecule type/orality | Stages 1–2, always (small-molecule lanes) |
| AlphaFold DB | `afdb.py` | Mean pLDDT (tractability term); apo structure pre-check | Stage 1 + Stage 3, whenever a UniProt ID exists |
| ClinicalTrials.gov | `clinicaltrials.py` | Prior/negative repurposing trials (Stage 1 penalty, Reviewer term, Writer citations) | Stages 1, 2c, 3b — always |
| PubMed (E-utilities) | `pubmed.py` | Abstracts for target–disease literature and druggability history | Biologist, always |
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
| 5 | Research module hypothesis proposer ("Sol") | LLM, `data_prep/` | Proposes analogical hypotheses about repurposing success in general | Every hypothesis becomes a testable predicate run against held-out repoDB outcomes under cumulative Benjamini–Hochberg FDR; findings are disclosure-only base rates, never score inputs |
| 6 | Stage-1 CLI narration | Sonnet | Plain-English summary of the already-written top-30 table | Post-hoc; references only numbers already on disk; not used by the API path |

What the AI **never** does: calculate any score, rank, or similarity; decide
caps or STRONG_MATCH; resolve disease identity; invent or select citations;
override a gate; or mark anything clinically validated. All AI clients honor
spend guardrails (`AGENTBIO_MAX_LLM_RATIONALES`, prefetch worker caps), and
provider-level hard spend limits are documented as the required backstop in
`api/guardrails.py`.

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
