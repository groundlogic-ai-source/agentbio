# Drug Repurposing Pipeline — Stage 1

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
- **InChIKey-first in PubChem**: drug name → InChIKey → all downstream properties; never match on raw name string.
- **ChEMBL strict filtering**: only Homo sapiens targets, IC50/Ki, `confidence_score >= 8`, `pchembl_value` present. `pooled_across_multiple_targets` flag is surfaced when multiple ChEMBL IDs match a UniProt accession.
- **Cache-first everywhere**: all six API wrappers check `cache/cache.db` before making a network call. First run is slow (15–60 min); repeat runs are near-instant.
- **LLM narration is post-hoc**: the Anthropic call happens only after the ranked table is fully built and written to disk, and is explicitly constrained to reference only numbers already in the table.

## Product

Stage 1: Given the universe of Orphanet rare diseases + 20 WHO NTDs, ranks the top 30 (disease, target) pairs by a two-dimensional score and produces an auditable output table plus a plain-English narrative of the top 5.

## User preferences

_Populate as you build._

## Gotchas

- `set` from `cache.cache` shadows Python's built-in `set()` — every module that imports it (all data sources AND `agents/target_selection.py`) must import it as `cache_set`.
- **Orphanet list endpoint** is `GET /rd-cross-referencing/orphacodes?lang=en` (~11.4k diseases). The old `/en/product1` path returns 404. Cross-refs (ICD-10/OMIM/MeSH) are NOT on the bulk list — they need a per-code lookup (`get_disease_xrefs`), so the pipeline only enriches the top-30 output rows, not all 11k.
- **ChEMBL `confidence_score` is an assay-level field**, absent from `/activity` records. The `/activity?assay_confidence_score__gte=` filter is silently ignored. The wrapper pulls activities, then batch-joins `/assay` to filter `confidence_score >= 8`. Never filter confidence on the activity record directly.
- **PubChem renamed `CanonicalSMILES` → `ConnectivitySMILES`** (2025); the wrapper requests/reads the new key (falls back to `SMILES`).
- AlphaFold mean pLDDT: try `pLDDT` residue URL first, fall back to `meanPlddt` / `globalMetricValue` top-level field.
- Do NOT delete `cache/cache.db` between short test runs — Orphanet + ChEMBL (cold ~29s/target) calls are the slowest and benefit most from caching.
- Validate any new wrapper against a data-rich entity first (EGFR `P00533`, Marfan ORPHAcode `558`) — wrappers swallow errors and return empty, so a wrong endpoint shows up as an all-None/all-zero column, not a crash.

## Pointers

- See `README.md` for full run instructions, output field descriptions, and scoring formula details.
