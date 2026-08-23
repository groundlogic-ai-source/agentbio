# Drug Repurposing Pipeline — Stages 1, 2, 3, 4 & 5 (AgentBio)

A Python pipeline that systematically identifies drug-repurposing candidates for rare diseases and WHO Neglected Tropical Diseases (NTDs) by integrating data from public biomedical APIs.

- **Stage 1 (Target Selection)** ranks the top 30 (disease, target) pairs by tractability and unmet need.
- **Stage 2 (Candidate Review)** takes the selected Stage 1 target and runs three agents — Biologist → Chemist → Reviewer — to produce a scored, fully-provenanced list of candidate compounds in `output/reviewed_candidates.json`.
- **Stage 3 (Structure Validation & Reporting)** orchestrates all stages as one checkpointed [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline, predicts protein–ligand structures/affinity and ADME for the top candidates via the [Boltz API](https://api.boltz.bio), compiles a Markdown report per candidate, and **pauses for human review** before finishing.

Auditability ethos (both stages): every LLM call is constrained to numbers already computed by code — the model never invents facts or scores. All similarity (Tanimoto) and composite scores are real computed numbers, not model guesses.

---

## Project Structure

```
.
├── data_sources/           # API wrapper modules (one file per source): Orphanet,
│                           #   Open Targets, ChEMBL, AlphaFold DB, ClinicalTrials.gov,
│                           #   PubChem, BioGRID, PubMed, openFDA, UniProt, Boltz,
│                           #   GtoPdb, BindingDB, Reactome, PubTator, Europe PMC,
│                           #   plus a sha256-pinned local DrugCentral 2023 snapshot
├── cache/
│   └── cache.py            # SQLite-backed key-value cache with TTL
├── agents/
│   ├── target_selection.py # [S1] Core scoring agent (no LLM for scoring)
│   ├── biologist.py        # [S2] Target biology: interactions + literature
│   ├── chemist.py          # [S2] Candidate compounds + Tanimoto bisociation
│   ├── reviewer.py         # [S2] Descriptors + safety + composite score
│   └── writer.py           # [S3] Markdown repurposing report per candidate
├── main_graph.py           # [S3] LangGraph orchestration (all stages + human review)
├── resume_review.py        # [S3] CLI to resume a paused run after human review
├── api/                    # [S4] FastAPI service (runs, audit, candidates, benchmarks)
├── artifacts/web-frontend/ # [S5] React + Vite single-page app (pnpm workspace)
├── validation/             # Frozen benchmark/audit artifacts of record + harnesses
├── publication/            # Preprint manuscript drafts + derived metrics
├── output/                 # Generated output files (created at runtime)
└── requirements.txt
```

---

## Installation

```bash
pip install -r requirements.txt --break-system-packages
```

> **RDKit note:** If `pip install rdkit` fails, try:
> ```bash
> pip install rdkit-pypi --break-system-packages
> ```
> RDKit is not called in Stage 1 scoring; Stage 2's Chemist and Reviewer use it for Morgan-fingerprint Tanimoto similarity and Lipinski/Veber descriptors.

### Environment variables

The LLM steps use Replit AI Integrations (Anthropic). These are set automatically:

| Variable | Purpose |
|---|---|
| `AI_INTEGRATIONS_ANTHROPIC_BASE_URL` | Replit proxy URL for Anthropic |
| `AI_INTEGRATIONS_ANTHROPIC_API_KEY`  | Dummy key (handled by proxy) |

All Stage 1 data sources are publicly accessible — no keys required. Stage 2 adds two keys:

| Variable | Required? | Purpose |
|---|---|---|
| `BIOGRID_API_KEY` | **Required** for BioGRID interactions | Free key from the [BioGRID REST registration](https://webservice.thebiogrid.org/). Without it the Biologist still runs but returns no interaction partners (logged as a warning). |
| `NCBI_API_KEY` | Optional | Raises PubMed E-utilities rate limit from 3 → 10 req/s. PubMed works without it. |

openFDA (FAERS) requires no key.

Stage 3 adds one key:

| Variable | Required? | Purpose |
|---|---|---|
| `BOLTZ_API_KEY` | **Required** for structure prediction | Key for the [Boltz API](https://api.boltz.bio) (protein–ligand structure/affinity + ADME). Without it the structure-validation node still runs and the pipeline completes, but Boltz fields are recorded as `unavailable` (logged, no spend). UniProt and AlphaFold DB require no key. |

> **Cost note:** each Boltz prediction is paid (~$0.025/prediction at time of writing); the structure-validation node logs an estimated cost per call. Set `STAGE3_MAX_CANDIDATES` to cap how many candidates are predicted per run (default 3).
>
> Predictions are made against the **real** Boltz API at `api.boltz.bio` via the official `boltz-api` SDK. The pipeline never contacts `alphafoldserver.com`.

---

## Running Stage 1

```bash
python -m agents.target_selection
```

This runs the full pipeline end-to-end. Expect it to take **15–60 minutes** on first run due to API rate limits across Orphanet (~10,000+ diseases), Open Targets, ChEMBL, AlphaFold, and ClinicalTrials. Subsequent runs are fast because all API responses are cached locally in `cache/cache.db`.

### What happens step by step

1. **Build candidate universe** — fetches all Orphanet rare diseases via API + appends the 20 hardcoded WHO NTDs.
2. **EFO resolution** — for each disease, queries the Open Targets search endpoint to get its EFO ID.
3. **Target discovery** — for each disease, pulls the top 5 associated protein targets (by Open Targets association score).
4. **Per-pair data collection** — for each (disease, target) pair:
   - ChEMBL: IC50/Ki count and median pChEMBL (Homo sapiens, confidence ≥ 8 only)
   - AlphaFold DB: mean pLDDT structural confidence
   - ClinicalTrials.gov: prior trial history for this drug+disease combination
5. **Scoring** — two independent scores are computed (never blended):
   - `tractability_score` — reflects how druggable the target is
   - `unmet_need_score` — reflects disease burden and lack of existing treatments
6. **Ranking & output** — top 30 pairs saved as JSON and CSV with all raw numbers.
7. **LLM narration** — one Anthropic API call generates a 2-3 sentence plain-English summary of the top 5, referencing only numbers already in the table.

---

## Output Files

All outputs are written to `output/`.

### `top_candidates.json` and `top_candidates.csv`

One row per (disease, target) pair. Fields:

| Field | Description |
|---|---|
| `disease_name` | Disease name (from Orphanet or WHO NTD list) |
| `orpha_code` | Orphanet ORPHAcode (null for WHO NTD-only entries) |
| `disease_source` | `orphanet` or `who_ntd` |
| `icd10` / `omim` / `mesh` | Orphanet cross-references (enriched per-code for the top 30 only) |
| `target_symbol` | HGNC gene symbol |
| `ensembl_id` | Ensembl gene ID |
| `uniprot_id` | UniProt accession |
| `ot_association_score` | Open Targets overall association score (0–1) |
| `chembl_activity_count` | IC50/Ki records with pChEMBL present, confidence ≥ 8, Homo sapiens |
| `median_pchembl` | Median pChEMBL value across qualifying ChEMBL records |
| `chembl_pooled_multi_target` | True if multiple ChEMBL target IDs were pooled (interpret with caution) |
| `afdb_has_structure` | True if AlphaFold model exists for this UniProt ID |
| `afdb_mean_plddt` | Mean per-residue pLDDT confidence (0–100) |
| `prior_trial_count` | Number of ClinicalTrials.gov records found for this pair |
| `has_negative_repurposing_result` | True if any trial was TERMINATED / WITHDRAWN / SUSPENDED |
| `has_approved_treatment` | Whether the disease has an approved treatment (null = unknown, flagged for review) |
| `prevalence_per_million` | Orphanet prevalence per million if available |
| `treatment_status_needs_review` | True when `has_approved_treatment` is unknown |
| `tractability_score` | Weighted numeric score (ChEMBL log-count 40%, pLDDT 35%, failure penalty 25%) |
| `unmet_need_score` | Weighted numeric score (no treatment 70%, prevalence 30%) |

### `narration.txt`

A 2-3 sentence plain-English commentary generated by `claude-sonnet-4-6`, referencing only the numbers in the table. It does not generate or modify any scores.

---

## Scoring Logic

### tractability_score

```
tractability = 0.40 × log_scale(chembl_count, cap=500)
             + 0.35 × (pLDDT / 100)
             + 0.25 × (−1.0 if prior_failure else 0.0)
```

- **ChEMBL component**: log1p-scaled, capped at 500 records, normalised to [0, 1]
- **pLDDT component**: normalised to [0, 1]; missing structure → 0
- **Failure penalty**: −0.25 subtracted from score if any prior trial was terminated/withdrawn for this exact pair

### unmet_need_score

```
unmet_need = 0.70 × treatment_component
           + 0.30 × log_scale(prevalence_per_million, cap=1_000_000)
```

- `treatment_component`: 1.0 (no treatment), 0.5 (unknown), 0.0 (treatment exists)
- Missing prevalence → 0 for that component

Both scores are always shown separately in the output. They are ranked by their sum but reported individually so a human can audit the math.

---

## Caching

All API responses are stored in `cache/cache.db` (SQLite). Default TTL:

| Source | TTL |
|---|---|
| Orphanet, Open Targets, ChEMBL, AlphaFold, PubChem | 7 days |
| ClinicalTrials.gov | 3 days |

To force a fresh run, delete `cache/cache.db`.

---

## Running Stage 2

Stage 2 builds on Stage 1's output. Run Stage 1 first (so `output/top_candidates.json` exists), then run the three agents **in order** — each reads the previous agent's output file:

```bash
python -m agents.biologist   # reads top_candidates.json  -> biologist_output.json
python -m agents.chemist     # reads biologist_output.json -> chemist_output.json
python -m agents.reviewer    # reads chemist_output.json   -> reviewed_candidates.json
```

The Biologist resets `output/provenance_log.json` at the start of each fresh run; the Chemist and Reviewer append to it. All three are cache-first (same `cache/cache.db`), so re-runs are fast.

### The three agents

1. **Biologist** (`agents/biologist.py`) — takes the **top** Stage 1 target and gathers biological context:
   - **BioGRID** physical/genetic interaction partners. These edges are labelled *"physical/genetic interaction, not mechanism"* — an interaction is **not** a claim that one gene activates or inhibits another.
   - **PubMed** abstracts for the target↔disease pairing, each passed through a constrained `claude-sonnet-4-6` YES/NO relevance gate. Only abstracts where the model confirms an *asserted* relationship (not mere co-mention) are kept, with their PMIDs recorded.

2. **Chemist** (`agents/chemist.py`) — turns the target into ranked candidate compounds:
   - **ChEMBL** candidate compounds for the target (Homo sapiens, IC50/Ki, assay `confidence_score ≥ 8`), aggregated per molecule with median pChEMBL.
   - **PubChem** InChIKey cross-reference + ATC-code classification to confirm approved/known-drug status (corroborated by ChEMBL `max_phase ≥ 4`).
   - **RDKit Tanimoto** (Morgan fingerprints, radius 2, 2048 bits) of each candidate vs every *other* approved drug in the working set — a real computed similarity, the "bisociation" signal. *Scope note: a full download of PubChem's approved-drug subset is infeasible here, so the reference set is the approved/known drugs found among this target's own candidate pool. The numbers are fully computed; only the comparison scope is bounded.*
   - **One** constrained LLM call per candidate writes a 2-sentence rationale that may reference **only** the affinity, Tanimoto score, and interaction network already computed — no new facts.

3. **Reviewer** (`agents/reviewer.py`) — scores and finalises:
   - **RDKit** Lipinski/Veber descriptors (MW, logP, HBD, HBA, TPSA, rotatable bonds).
   - **openFDA** real-world adverse-event signal and **ClinicalTrials.gov** prior-trial check for the exact drug+disease pair.
   - **Provenance de-duplication**: the same PMID or ChEMBL activity id is counted only once across the scoring pass (audit integrity).
   - A single auditable **composite score** (formula below).

### `reviewed_candidates.json` (Stage 2 final output)

```jsonc
{
  "formula": { "composite_weights": {...}, "lipinski_penalty": 0.25, "strong_match_threshold": 0.7, ... },
  "n_candidates": N, "n_strong_matches": M,
  "candidates": [ { "drug_name", "composite_score", "strong_match", "score_components",
                    "descriptors", "adverse_events", "tanimoto_score", "rationale",
                    "provenance": { "counted_once", "collapsed_as_duplicate" }, ... } ]
}
```

### Composite score

The exact weights are named constants at the top of `agents/reviewer.py` — the code is the source of truth. The properties that matter for auditability:

- Components cover binding evidence (ChEMBL pChEMBL and assay confidence), Open Targets association, Tanimoto bisociation, and prior-trial failure, each normalised against **absolute ranges** — never per-run min-max — so a composite means the same thing across runs and pools.
- **Coverage-aware**: a component that was unobserved or whose lookup failed drops out of *both* sides of the weighted sum instead of being silently treated as zero; `evidence_weight_coverage` is reported per candidate.
- A flat **Lipinski penalty** (`0.25`) applies when violations > 1 (a soft developability flag, not a hard ADME prediction), and hard **caps** clamp the composite at `0.40` for unapproved-drug, mechanism-mismatch, or safety findings; `pre_cap_score` is always recorded alongside so the uncapped value stays visible.
- A candidate is flagged `STRONG_MATCH` when `composite_score ≥ 0.70` (`STRONG_MATCH_THRESHOLD`).
- Provenance de-dup ensures evidence ids (PMIDs, ChEMBL activity ids) are counted only once in the audit trail; the formula's inputs are independent metrics, so de-dup affects evidence accounting rather than the weighted sum.

---

## Running Stage 3

Stage 3 wires every stage into a single, **checkpointed** [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline that ends in a human-in-the-loop review gate:

```
target_selection → biologist → chemist → reviewer → structure_validation → writer → human_review
```

Start a run:

```bash
python main_graph.py                 # auto-generates a thread_id
python main_graph.py my-run-id       # or pass your own thread_id
```

The run executes through `writer`, then **pauses** at `human_review` (a LangGraph `interrupt()`), printing the `thread_id` and the report path(s). State is persisted to `checkpoints.db`, so you can resume later from a **separate process**:

```bash
python resume_review.py <thread_id> approve
python resume_review.py <thread_id> reject "binding pose confidence too low"
python resume_review.py <thread_id> edit   "rerun with more samples"
```

The decision is written to `output/review_decision.json`.

### Design guarantees

- **Durable checkpoints** — a `SqliteSaver` backed by an on-disk `checkpoints.db` persists every node, enabling cross-process pause/resume.
- **Single interrupt** — **only** the `human_review` node calls `interrupt()`, and it performs no API calls before the interrupt, so resuming never re-spends on Boltz.
- **Idempotent upstream** — the Stage 1/2 nodes reuse existing `output/*.json` artifacts when present, so re-running Stage 3 does not redo the expensive Stage 1/2 work. Set `STAGE3_FORCE_RECOMPUTE=1` to force fresh upstream computation.

### What `structure_validation` does

For each selected candidate (STRONG_MATCH first; see the env switches below):

1. **AFDB apo pre-check** (per target) — fetches the ligand-free AlphaFold model's mean pLDDT. This is informational only; **Boltz is always called regardless** because AFDB contains no ligand and cannot describe the complex.
2. **UniProt** — resolves the target's canonical protein sequence (FASTA).
3. **Boltz** — `predict_complex(protein_sequence, ligand_smiles)` returns `{structure_confidence, binding_pose_confidence, predicted_affinity, pdb_or_cif_url}`, and `predict_adme(smiles)` returns lipophilicity / permeability / solubility. Results are cached and an estimated cost is logged per call.

> `predicted_affinity` is Boltz's **relative optimization score** (0–1), **not** a Kd or IC50. This caveat is stated in the code and in every report's Limitations section.

### The `writer` node

Writes one Markdown report per selected candidate to `output/reports/{disease}_{drug}.md`, each with exactly five sections: **(1)** hypothesis summary, **(2)** evidence table, **(3)** full source citations (deduplicated PMIDs, ChEMBL activity IDs, NCT numbers), **(4)** composite-score breakdown (every weighted term, reconciled against `reviewed_candidates.json`), and **(5)** limitations. The writer invents no facts — it only restates numbers already produced upstream.

### Environment switches

| Variable | Default | Effect |
|---|---|---|
| `STAGE3_FORCE_RECOMPUTE` | `0` | `1` re-runs Stage 1/2 instead of reusing `output/*.json`. |
| `STAGE3_STRONG_ONLY` | `0` | `1` writes reports only for true STRONG_MATCH candidates. When `0` and there are none, the single highest-ranked candidate is reported, **clearly flagged as below threshold**. |
| `STAGE3_MAX_CANDIDATES` | `3` | Caps how many candidates receive a (paid) Boltz prediction. |
| `STAGE3_BOLTZ_SAMPLES` | `1` | Number of Boltz structure samples per prediction. |

## Running Stage 4 — AgentBio API

Stage 4 wraps the exact Stage 1–3 LangGraph pipeline in a **FastAPI** backend so runs can be started, monitored, and approved over HTTP. It is a single-user hobby service: **no auth, no accounts, no Celery/Redis** — each run executes on a plain Python background thread.

The API layer never reimplements pipeline logic. It imports `build_graph` from `main_graph.py` and reuses `resume_review.resume_run()` — the *same* function the CLI uses — for the approve/reject step.

### Run it

```bash
uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

`PORT` is provided by Replit (defaults to `8000` locally). On Replit the **AgentBio API** workflow runs this for you; open the webview and append `/docs`.

### Job tracking

Job metadata lives in a **PostgreSQL** store (Replit's built-in database) — kept separate from the LangGraph `checkpoints.db` (graph state, SQLite) so the API schema never couples to LangGraph internals. The `jobs` table tracks `status` (`queued` → `running` → `awaiting_review` → `completed` / `error`) and `current_stage`, which is updated **after each graph node actually completes** (`target_selection` → `biologist` → `chemist` → `reviewer` → `structure_validation` → `writer` → `awaiting_review` → `done`) by hooking into LangGraph's `stream()` output — not a single flip from running to done.

### Endpoints

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/runs` | `{"disease_name": "optional"}` | `{"job_id": "..."}` immediately; the run starts on a background thread |
| `GET /api/runs` | — | all jobs, most recent first |
| `GET /api/runs/{job_id}` | — | the full job record, plus the compiled `report` content when `status` is `awaiting_review` or `completed` |
| `POST /api/runs/{job_id}/resume` | `{"action": "approve"｜"reject", "notes": "optional"}` | resumes via `resume_run()`; job becomes `completed` / `done` |
| `GET /api/runs/{job_id}/cost` | — | `{"total_cost_usd": ...}` (summed Boltz spend) |

Further route groups — batch runs, dossier audit (`/api/audit*`), the pooled candidate list (`/api/candidates*`), and read-only frozen benchmark results (`/api/research/benchmarks`) — are documented in the interactive `/docs`.

> **`disease_name` drives target selection — two modes:**
>
> - **Named (manual mode).** Pass a `disease_name` and Stage 1 looks it up directly in the rare-disease / WHO-NTD universe (case-insensitive name match, falling back to any ICD-10 / OMIM / MeSH cross-reference already pulled). Its top targets are then scored with the **exact same `tractability_score` / `unmet_need_score` formulas** the ranking sweep uses — never faked or skipped. If the disease is **not** in that universe, the run **errors** with a clear message ("scoped to rare and neglected diseases"); it never silently substitutes a different disease. The stored `disease_name` is updated to the canonical matched name.
> - **Blank (auto-explore mode).** Omit `disease_name` and Stage 1 picks the **highest-ranked (disease, target) pair not yet used by any prior run**. Every selected pair is recorded in an `explored_targets` table in the Postgres jobs store, so repeated blank runs walk *down* the ranked list instead of re-picking the same #1 candidate. Once the whole ranked list is exhausted, it falls back to the top pair.
>
> Both modes carry the real Stage 1 scores into the final report (a **"Stage 1 prioritization scores"** section), and selecting a new target automatically invalidates the stale Stage 2/3 artifacts so the report always describes the pair actually chosen.

## Stage 5 — AgentBio web frontend

Stage 5 is a **React + Vite + Tailwind** single-page app in `artifacts/web-frontend/` (a pnpm workspace package) that turns the Stage 4 API into a usable interface. It is styled as a **"case dossier"** rather than a generic admin dashboard: warm paper/ink palette with brass accents, Fraunces / Inter / JetBrains Mono type, file-folder tabs, a vertical pipeline stepper for live runs, an inline report with a sign-off panel, and a wax-style **stamp** (`STRONG MATCH` / `REJECTED`) on completed cases. The voice throughout frames every result as a *hypothesis to investigate*, never a cure.

The whole pipeline is presented as a chain of hypotheses: each run opens a new case, walks the pipeline stages, pauses for a human sign-off, and is stamped closed. Beyond the case flow (**Case Files** tab), the app includes **Research** (frozen benchmark results), **Candidates**, **Audit**, and **Saved Reports** tabs.

### Develop (hot reload)

```bash
pnpm --filter @workspace/web-frontend run dev   # Vite dev server on :21854
```

On Replit the **web** workflow runs this for you; run the API alongside it (`uvicorn api.main:app --host 0.0.0.0 --port 8000`). The app calls relative `/api/*` paths only — no hardcoded hosts.

### Build (production)

```bash
PORT=21854 BASE_PATH=/ pnpm --filter @workspace/web-frontend run build
```

In deployment the frontend artifact serves `/` and the API artifact serves `/api/*` as two separate processes behind Replit's reverse proxy; the FastAPI service no longer serves static files.

### How the UI talks to the API

- The app polls `GET /api/runs` (plus per-case detail and cost endpoints) only while a visible job is non-terminal, and stops automatically once everything is `completed` or `error`.
- The stepper reads `current_stage` as the **last completed node** (the backend's contract) — it checks that stage off and highlights the *next* one as "Working…".
- A finished case shows the report read-only plus the stamp; the chosen `decision` and `review_notes` are persisted server-side, so they survive a page reload.

> The first cold run is slow (Stages 1–3 populate the cache). A repeated Boltz prediction is a **cache hit ($0 spend)**.

### Driving the API directly (no UI)

You can still exercise everything from **`/docs`** (interactive Swagger UI):

1. **`POST /api/runs`** → *Try it out* → body `{"disease_name": "Pompe disease"}` for manual mode (must be a rare/NTD disease, or the job errors), or `{}` to auto-explore the next-ranked pair → **Execute**. Copy the returned `job_id`.
2. **`GET /api/runs`** → confirm the job appears with `status: running`.
3. **`GET /api/runs/{job_id}`** → re-execute every few seconds and watch `current_stage` advance through the real nodes until `status` becomes `awaiting_review`; the response now includes the full `report` text.
4. **`GET /api/runs/{job_id}/cost`** → confirm `total_cost_usd` (e.g. `0.025`).
5. **`POST /api/runs/{job_id}/resume`** → body `{"action": "approve", "notes": "looks good"}` → **Execute**. The job flips to `completed` / `done`. (Resuming a job that isn't `awaiting_review` returns `409`; an invalid `action` returns `400`.)

## Validation status (honest)

- A pre-registered, blind retrospective benchmark (50 primary + 15 development
  drug-repurposing rediscovery cases) **has completed and is frozen** — hits,
  misses, and miss-reason breakdowns are published in full in
  `validation/validation_campaign_dossier.md`, with raw results in
  `validation/benchmark_results_v2.json`. The pipeline was frozen at tag
  `benchmark-freeze-v2`; case-selection criteria are in
  `validation/benchmark_case_selection_criteria.md`; provenance is independently
  re-checkable via `python3 validation/verify_v2_provenance.py`.
- Two follow-on frozen studies (a dossier-audit claim set and a triage
  discrimination study) are also complete and documented in `validation/`.
  Wherever validation results are quoted, the audit claim-set **v1 FAIL** must be
  disclosed alongside the v2 PASS — see the dossier for both, unedited.
- Earlier small development-suite runs were used for iteration only and are
  superseded by the frozen benchmark — do not quote them.
- Known limitations and past failure postmortems are documented in
  `validation/`.

## Beta status & bug reports

AgentBio is in **beta**. Dossiers are machine-generated hypotheses for expert
review — not medical advice. In-app feedback goes through the beta Google Form
(linked from the banner). Please file bugs as **GitHub Issues** on
<https://github.com/groundlogic-ai-source/agentbio>.

## How to cite

If you use AgentBio in your work, please cite the software and, when referring to
validation results, the validation campaign dossier:

> Britto, E. (2026). *AgentBio: an audited drug-repurposing research pipeline for
> rare and neglected diseases* (Version benchmark-freeze-v2) [Computer software].
> MIT License.

```bibtex
@software{britto2026agentbio,
  author  = {Britto, Evan},
  title   = {AgentBio: an audited drug-repurposing research pipeline for rare and neglected diseases},
  year    = {2026},
  version = {benchmark-freeze-v2},
  license = {MIT}
}
```

This repository also ships a `CITATION.cff` file, so GitHub and reference managers
(Zotero, Mendeley) pick the citation up automatically. Validation results of record
are documented in `validation/validation_campaign_dossier.md`; the provenance of the
frozen benchmark is independently re-checkable via
`python3 validation/verify_v2_provenance.py`. AgentBio is a research prototype and
is not clinically validated.
