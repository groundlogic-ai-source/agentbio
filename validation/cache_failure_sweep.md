# Cache failure-caching sweep — complete audit & fix report

**Date:** 2026-07-31 · **Trigger:** MTOR/TSC pool-zeroing incident (`validation/target_selection_diagnosis.md`) revealed a *second* poisoning vector the earlier exception-only sweep could not catch. Ordered as step 1 of the pre-validation sequence: "this needs to be the last time this bug class gets discovered by accident."

## Method (mechanical, not case-driven)

1. **AST scan** of `data_sources/`, `agents/`, `api/`, `validation/`, `data_prep/` for every `cache_set` call site → **72 sites** in 20 files (`/tmp/cache_audit.py`).
2. **Classification** of every site by failure mode, then **manual verification of every flagged site** against the actual code (no verdict accepted on trust).

Two bug classes:
- **CLASS_E — exception-swallow:** an `except` handler logs and falls through to a terminal `cache_set`, freezing a default/partial/empty value for 7–30 days.
- **CLASS_D — empty-payload ambiguity:** a degraded 200-OK-with-empty-body response is indistinguishable from a genuine empty result and gets cached as such. *This is the MTOR vector — raising on HTTP errors cannot catch it.*

## Results

| Verdict | Count |
| --- | --- |
| Sites audited | 72 |
| SAFE as-is (verified) | 45 |
| Safe-by-design after review (holdout sentinel `chembl.py:~894`; explicit error-verdict caches with 1-day TTL `safety_check.py:186`, `mechanism_direction.py:334`; deliberate 1-day failure TTL `orphadata.py:250`) | 4 |
| **Vulnerable — fixed** | **23 sites → 26 edits across 7 files** |

### Fixes by file

| File | Sites fixed | What was poisoned before |
| --- | --- | --- |
| `data_sources/chembl.py` | 11 | Accession fallback cached as gene symbol (30d); empty target-resolution cached as zero bioactivity (7d); **empty activity payload cached (7d) — the MTOR vector**; empty pool cached without distinguishing empty-payload from empty-after-filter (7d, now gated on `saw_activity_payload`); **safe defaults cached as "not withdrawn / no black-box" after a failed safety fetch (30d — safety-critical false negative)**; empty mechanism/indication lookups cached (7d/30d); `resolved=False` cached after failure (30d); precedent lookup failures cached as empty precedent list (7d). Also: `except RuntimeError: raise` added so fail-loud holdout/resolution errors abort instead of being swallowed back into a cacheable empty. |
| `data_sources/afdb.py` | 2 | Failure cached as `has_structure=False` (7d) — silently demoted tractability; empty predictions payload cached. |
| `data_sources/open_targets.py` | 5 | **Failed association query cached as empty target list (7d) — silently removes all genetic targets, lets precedent win by default**; failed parent/descendant lookups cached as empty/fail-closed-None (7d/30d); failure cached as `""` sentinel for canonical name and Orphanet code (now gated on success flag). |
| `data_sources/orphadata.py` | 2 | **Failed universe fetch cached as empty rare-disease list (7d) — would zero the entire sweep**; failed xref lookup cached as all-None (7d). |
| `data_sources/reactome.py` | 3 | `_get` failure conflated with genuine empty via `or []` and cached (30d); partial neighbor aggregates cached when some participant fetches failed (now tracked via `fetch_failed`, cached only when clean). |
| `data_sources/uniprot.py` | 1 | Failed sequence fetch cached as `""` sentinel (30d). |
| `agents/biologist.py` | 1 | LLM summarisation failure cached as partial druggability context (7d). |

### Cache purge (existing poison)

| Round | Rows purged |
| --- | --- |
| Empty candidate pools (`"compounds": []`) — earlier today | 341 |
| Empty lists / `""` sentinels / nulls / failure-signature dicts (afdb defaults, molecule-safety all-defaults, unresolved molecule data) | 14,807 |
| **Total** | **15,148** |

All purges are refetch-safe: genuine negatives are simply re-fetched on next access and re-cached only when they represent confirmed, post-filter empties.

## Post-review hardening (architect code review of the sweep diff)

The review confirmed the 26 edits but caught one objective mismatch: `_fetch_activities_full` applies the confidence filter *internally*, so the pool-level gate couldn't distinguish "raw payload empty" (ambiguous) from "raw rows existed, all filtered out" (genuine post-filter empty). Fixed by threading a `raw_seen` flag through both activity fetchers (`_fetch_activities`, `_fetch_activities_full` now return `(kept, raw_seen)` tuples; activities cache bumped to `_fetch_activities_full_v2` storing `{kept, raw_seen}`) and gating both aggregate caches (`get_target_bioactivity_count` — itself a CLASS_D site the initial audit missed at the count level — and `get_target_candidate_compounds`) on it. `validation/miss_classifier.py` caller updated for the tuple contract.

## Verification

- `python3 -m py_compile` on all touched files: OK.
- `python3 -m unittest validation.test_holdout`: 6/6 OK (holdout regression suite intact after the `RuntimeError` re-raise change).
- `python3 -m unittest validation.test_cache_failures` (new): 7/7 OK — pins both bug classes: empty payloads never cached; genuine post-filter empties remain cacheable at activities, pool, and count levels.

## Residual, deliberately accepted

- `get_drug_action_type`: an empty mechanism list from a *successful* 200 is still cached as `not_found` (genuine no-MOA-record drugs are common; the failure path via exception is fixed). Residual risk: a degraded-200 empty mechanism payload caches `not_found` for 30d — accepted as low-stakes (reviewer action-type check only).
- `reactome._get` returns `None` on 404 as well as on errors, so a genuine 404 is now treated as "failure, don't cache" — conservative, costs a refetch.
- Fixed functions re-fetch genuine empties once per run instead of serving from cache (small, bounded API cost; correctness over cost ahead of the frozen benchmark).
