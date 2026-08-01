# AgentBio — Drug Repurposing Pipeline

A Python drug-repurposing research pipeline that ranks rare disease / neglected tropical disease targets by tractability and unmet need, using six public biomedical APIs and a local SQLite cache.

## Run & Operate

- `python -m agents.target_selection` — run the full Stage 1 pipeline (writes to `output/`)
- Required env (auto-set via Replit AI Integrations): `AI_INTEGRATIONS_ANTHROPIC_BASE_URL`, `AI_INTEGRATIONS_ANTHROPIC_API_KEY`

## Stack

- Python 3.11+, requests, anthropic SDK, pandas
- SQLite cache (`cache/cache.db`) with per-source TTLs
- Anthropic `claude-sonnet-4-6` for final narration only (no LLM in scoring)

## Where things live

- `data_sources/` — one wrapper per API: orphadata, open_targets, chembl, afdb, clinicaltrials, pubchem
- `cache/cache.py` — SQLite key-value cache with TTL; key = SHA-256(fn_name + args)
- `agents/target_selection.py` — core pipeline: builds universe, resolves EFOs, scores pairs, writes output
- `output/` — `top_candidates.json`, `top_candidates.csv`, `narration.txt` (created at runtime)

## Architecture decisions

- **Two scores, never blended at collection time**: `tractability_score` and `unmet_need_score` are always output separately so a human can audit the math.
- **Ranking (Stage 1)**: top-30 ordered by `tractability_score + unmet_need_score`. Both scores are real (not constants): `tractability_score` = ChEMBL log-count + AlphaFold pLDDT − trial-failure penalty, weighted by OT `association_score` (gate ≥ 0.1). `unmet_need_score` = 0.7 × treatment + 0.3 × prevalence (log-scaled) when both signals present; falls back to treatment signal alone; 0.5 when neither is available.
- **OT approved-treatment data**: `disease.drugAndClinicalCandidates { rows { drug { id name } maxClinicalStage } }`. `maxClinicalStage == "APPROVAL"` → has_approved_treatment=True. Cache key `get_disease_known_drugs_v2` (v1 used wrong field `knownDrugs` → 400 errors).
- **Prevalence**: Orphadata epidemiology API `/rd-epidemiology/orphacodes/{code}`, returns `None` gracefully on 401/403/404. Short TTL on failure.
- **Association score gate**: only (disease, target) pairs with OT `association_score >= 0.1` are scored. Tractability is multiplied by `association_score` to reward well-supported targets.
- **Manual disease lookup — 4-step matching**: (1) exact Orphanet name, (2) ICD-10/OMIM/MeSH xref, (3) unique substring (OBSOLETE entries excluded), (4) OT EFO search → `get_disease_orphanet_code` (dbXRefs) → match by ORPHA code. Handles common names like "Pompe disease" that differ from Orphanet's official name.
- **InChIKey-first in PubChem**: drug name → InChIKey → all downstream properties; never match on raw name string.
- **ChEMBL strict filtering**: only Homo sapiens targets, IC50/Ki, `confidence_score >= 8`, `pchembl_value` present. `pooled_across_multiple_targets` flag is surfaced when multiple ChEMBL IDs match a UniProt accession.
- **Cache-first everywhere**: all six API wrappers check `cache/cache.db` before making a network call. First run is slow (1–3 h cold); repeat runs are near-instant.
- **LLM narration is post-hoc**: the Anthropic call happens only after the ranked table is fully built and written to disk, and is explicitly constrained to reference only numbers already in the table.
- **Sweep trigger**: `POST /internal/run-sweep` on the uvicorn API starts the sweep as a child `subprocess.Popen` (survives shell exits). `GET /internal/sweep-status` polls status. Output streams to `/tmp/sweep_run.log`.

## Product

Stage 1: Given the universe of Orphanet rare diseases + 20 WHO NTDs, ranks the top 30 (disease, target) pairs by a two-dimensional score and produces an auditable output table plus a plain-English narrative of the top 5.

## User preferences

- Do NOT create/propose sub-tasks or task-agent work (user finds them confusing bureaucracy; a mid-benchmark task merge broke the code freeze). Do work directly in Build mode.
- While the benchmark freeze is armed, never merge changes to `agents/`, `data_sources/`, or `cache/`.

## Two-mode target selection (Stage 5)

- **Manual mode** (`disease_name` given): `target_selection.select_for_disease()` resolves the query via 4-step matching (see Architecture), scores its top targets with the SAME `_score_pair` formulas, returns rows sorted best-first. Not found → `DiseaseNotInUniverse` (surfaces as a job error; never silently auto-picks). Does NOT overwrite `output/top_candidates.json`.
- **Blank mode**: the graph picks the highest-ranked pair not in the `explored_targets` table (`jobs.db`); every selection (both modes) is recorded so repeated blank runs walk down the list.
- **Critical**: selecting a new target invalidates stale Stage 2/3 artifacts (`biologist_output.json`, `chemist_output.json`, `reviewed_candidates.json`, `structure_validation.json`) via the `output/active_selection.json` marker — otherwise the cached-by-existence reuse makes a new target reuse the previous target's downstream output. Invalidation does not affect resume (which replays from `checkpoints.db`).

## Gotchas

- `set` from `cache.cache` shadows Python's built-in `set()` — every module that imports it (all data sources AND `agents/target_selection.py`) must import it as `cache_set`.
- **Orphanet list endpoint** is `GET /rd-cross-referencing/orphacodes?lang=en` (~11.4k diseases). The old `/en/product1` path returns 404. Cross-refs (ICD-10/OMIM/MeSH) are NOT on the bulk list — they need a per-code lookup (`get_disease_xrefs`), so the pipeline only enriches the top-30 output rows, not all 11k.
- **Orphanet has ~1,024 "OBSOLETE:" entries** — the substring step of `_match_disease` explicitly excludes them (name starts with "obsolete" after normalization) to prevent false positives.
- **ChEMBL `confidence_score` is an assay-level field**, absent from `/activity` records. The `/activity?assay_confidence_score__gte=` filter is silently ignored. The wrapper pulls activities, then batch-joins `/assay` to filter `confidence_score >= 8`. Never filter confidence on the activity record directly.
- **PubChem renamed `CanonicalSMILES` → `ConnectivitySMILES`** (2025); the wrapper requests/reads the new key (falls back to `SMILES`).
- AlphaFold mean pLDDT: try `pLDDT` residue URL first, fall back to `meanPlddt` / `globalMetricValue` top-level field.
- **Cache `None` values are not cached**: `cache.get()` returns Python `None` for both a cache miss and a stored JSON `null`. Pattern `if cached is not None: return cached` means None results are always re-fetched. Use `""` (empty string) as the sentinel for "confirmed no result" when negative caching matters (see `get_disease_orphanet_code`).
- Do NOT delete `cache/cache.db` between short test runs — Orphanet + ChEMBL (cold ~29s/target) calls are the slowest and benefit most from caching.
- Validate any new wrapper against a data-rich entity first (EGFR `P00533`, Marfan ORPHAcode `558`) — wrappers swallow errors and return empty, so a wrong endpoint shows up as an all-None/all-zero column, not a crash.

## Pointers

- See `README.md` for full run instructions, output field descriptions, and scoring formula details.
