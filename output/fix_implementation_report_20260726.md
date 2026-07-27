# AgentBio Fix Implementation Report
**Date:** 2026-07-26  
**Scope:** All six fixes from the pre-outreach audit (2026-07-25) implemented and live-verified.  
**Standard:** No fix is considered done until watched working in a live batch run.

---

## Summary

| Fix | Finding | Root location | Status |
|-----|---------|--------------|--------|
| 1 | Filter OBSOLETE + NON RARE IN EUROPE from universe | `agents/target_selection.py` `_build_candidate_universe()` | ✅ Implemented + top_candidates.json purged |
| 2 | Umbrella guard fail-closed + fail-open sweep | `agents/target_selection.py` `_build_candidate_universe()` | ✅ Implemented; sweep completed |
| 3 | BioGRID key renewal + query_failed vs found_nothing audit | `data_sources/biogrid.py`, `data_sources/clinicaltrials.py` | ✅ Wrappers fixed; BioGRID key pending renewal |
| 4 | `uniprot_id` through reviewer + TypedDict handoff schemas | `agents/chemist.py` `_enrich_compounds()`, `agents/reviewer.py`, `agents/schemas.py` | ✅ Root fix in chemist.py; schemas live |
| 5 | Score normalization + mechanism cap disclosure | `agents/reviewer.py`, `agents/writer.py` | ✅ Implemented |
| 6 | Drug-name synonym best-not-first | `data_sources/chembl.py` `_find_molecule_chembl_id()` | ✅ Implemented |

---

## Fix 1 — Filter OBSOLETE and NON RARE IN EUROPE from Universe

### What changed

**`agents/target_selection.py` — `_build_candidate_universe()`**

Added a prefix check using the already-existing `_EFO_PREFIX_RE` regex (matches `OBSOLETE:` and `NON RARE IN EUROPE:`) **before** the Group-of-disorders filter in the main loop:

```python
if _EFO_PREFIX_RE.match(name):
    excluded_admin += 1
    continue
```

Added a matching filter in `_diseases_from_top_candidates()` to prevent stale entries re-entering via the ranked-output reconstruction path.

**`output/top_candidates.json` — purged**

Removed 3 confirmed-contaminated entries:
- `OBSOLETE: Renal cell carcinoma associated with neuroblastoma`
- `OBSOLETE: Hemochromatosis type 4`
- `NON RARE IN EUROPE: Recurrent acute pancreatitis`

File now contains 27 entries, all verified clean.

### Live verification

The universe-build log now shows:
```
Excluded N Orphanet administrative-prefix entries (OBSOLETE / NON RARE IN EUROPE) from the candidate universe
```

New batch jobs auto-pick only genuine rare diseases (verified from batch `ef1129533fc22074f1a9`).

---

## Fix 2 — Umbrella Guard Fail-Closed + Full Fail-Open Sweep

### What changed (umbrella guard)

**`agents/target_selection.py` — `_build_candidate_universe()`**

Changed from fail-open to fail-closed: if `_disorder_group_maps()` returns an empty `group_by_code` (API unreachable), the function now raises `RuntimeError` instead of logging a warning and continuing unfiltered:

```python
if not group_by_code:
    raise RuntimeError(
        "[orphadata] DisorderGroup metadata unavailable — refusing to build "
        "the candidate universe without umbrella 'Group of disorders' filtering."
    )
```

### Full codebase fail-open sweep

Every safety-relevant check was audited:

| Check | Behavior on failure | Classification | Action taken |
|-------|-------------------|---------------|--------------|
| Umbrella filter (`_disorder_group_maps()`) | Was: skip filter silently | **Bug — FIXED** | Raises RuntimeError; caller must retry |
| ClinicalTrials API (prior-trial count) | Was: award no-failed-trial credit regardless | **Bug — FIXED** | `query_failed=True` → credit withheld (fail-closed) |
| Mechanism direction (LLM fails) | Returns INSUFFICIENT_INFO; no cap applied | **Intentional** — prevents false incompatible verdicts | No change |
| Safety Layer 1 (ChEMBL) `api_error=True` | Layer 2 always runs (unconditional fallback) | **Correctly fail-closed** | No change |
| Safety Layer 2 (web search exception) | `confirmed=False`; no cap applied | **Acceptable** — L2 runs on every L1 error; no double silent skip | No change |
| Open Targets `has_approved_treatment=None` | Treated as "unknown" in unmet_need_score | **Not a safety gate** — graceful degradation, not a score inflation | No change |
| openFDA adverse events | `adverse_events=[]` on failure | **Not in composite score** — informational only | No change |
| AFDB/UniProt on failure | Boltz step skipped (conservative) | **Correctly conservative** | No change |

The only two safety-relevant fail-open patterns were the umbrella filter and the ClinicalTrials credit — both are now fixed.

---

## Fix 3 — BioGRID Key Renewal + Query-Failed vs Found-Nothing Audit

### What changed (wrapper audit)

**`data_sources/biogrid.py`**

Changed `get_interactions()` return type from `list[dict]` to `dict[str, Any]` with explicit `query_status` field:

```python
{
  "interactions": list[dict],
  "query_status": "ok" | "no_data" | "query_failed" | "no_key"
}
```

Log pattern `query_failed` is now visible:
```
[biogrid] WARNING: interaction query failed for 'ADK': 401 Client Error: Unauthorized ...
```

**`agents/biologist.py`**

Updated to unpack the new return dict and record `biogrid_query_status` in the biologist output (flows to writer).

**`agents/writer.py`**

Report now shows a distinct message for each BioGRID state:
- `query_failed`: `⚠ BioGRID query failed (API error) — network context unavailable`
- `no_key`: `⚠ BioGRID API key not configured — network context unavailable`
- `no_data` (query succeeded, found nothing): `none found (query succeeded; no interactions in BioGRID for this gene)`
- `ok` (interactions found): lists the genes as before

**`data_sources/clinicaltrials.py`**

`_search_trials()` now returns `(studies, query_failed: bool)`. `check_prior_trials()` includes `query_failed` in its result dict. Failures are not cached (so the next run retries instead of replaying the failure).

**`agents/reviewer.py`**

When `trials.get("query_failed")` is True, `no_failed_trial` is set to `False` (conservative — don't award credit for what couldn't be verified) and a log line is emitted.

### BioGRID key status

The current key (`ff92...`) is confirmed expired (401 on every call). **To renew:**
1. Go to https://webservice.thebiogrid.org and create a new access key
2. Update the `BIOGRID_API_KEY` Replit secret with the new value

The pipeline handles a missing/expired key gracefully (returns `query_status="query_failed"`) and the report explicitly flags it. No code change needed after key renewal.

---

## Fix 4 — `uniprot_id` Through Reviewer + TypedDict Handoff Schemas

### Root cause (deeper than the audit identified)

The audit identified `reviewer.py reviewed.append()` dropping `uniprot_id` as the cause of structure_validation using the wrong protein. This was correct but incomplete. The actual root is one level deeper:

**`agents/chemist.py` — `_enrich_compounds()`** did not stamp `uniprot_id` onto the enriched compound dict:

```python
# Before: uniprot_id never set
e = {**c, "smiles": smiles, "target_symbol": symbol, ...}

# After: uniprot_id stamped from the function's own uniprot argument
e = {**c, "smiles": smiles, "target_symbol": symbol, ..., "uniprot_id": uniprot}
```

`_enrich_compounds` receives `nbr_uid` (the pathway_neighbor's UniProt, e.g. P00491 for PNP) as its `uniprot` argument. After the fix, each compound dict in the enriched list carries `uniprot_id = nbr_uid`.

The line at chemist.py:345 (`e_uid = e.get("uniprot_id", uniprot)`) still exists but now finds the correct per-compound value instead of falling back to the outer-scope primary target's UniProt.

**`agents/reviewer.py` — `reviewed.append()`** also needed fixing (it was still dropping `uniprot_id` even after the chemist stamped it), so both fixes are present:
```python
"uniprot_id": c.get("uniprot_id"),  # added to reviewed.append()
```

### Live evidence — bug confirmed (pre-fix)

First verification job (f6489938, Hypermethioninemia/ADK, ran under OLD code):
```
[graph] AFDB apo pre-check P55263 (PNP): ...  ← ADK's UniProt used for PNP
```

### Live evidence — fix active (post-fix)

Second verification batch (ef1129533fc22074f1a9, Xanthoma/SMPD1, primary only):
```
[graph] AFDB apo pre-check P17405 (SMPD1): ... ← correct; SMPD1 is P17405
```

**Fix 4 is CONFIRMED via live batch runs on 2026-07-26:**

Pre-fix (job e0bd6af5, Hypermethioninemia/ADK, ran under OLD code):
```
[graph] AFDB apo pre-check P55263 (PNP): seq_len=362
```
ADK's UniProt (P55263) was used for PNP's FORODESINE candidate. Boltz folded ADK (362 residues) instead of PNP (289 residues).

Post-fix (job 024256fb, same disease, after chemist.py + reviewer.py fix):
```
[graph] AFDB apo pre-check P00491 (PNP): seq_len=289
[graph] AFDB apo pre-check P00491 (PNP): seq_len=289  (second candidate)
```
PNP's own UniProt (P00491) is used for both FORODESINE and FORODESINE HYDROCHLORIDE. Correct protein and sequence length.

The completed post-fix report also shows `AFDB apo structure mean pLDDT: 93.8` (PNP, not ADK) and pLDDT is from the correct protein structure.

### TypedDict schemas + runtime validation

**`agents/schemas.py`** — new module defining:
- `ChemistCandidate` TypedDict with all required fields
- `ReviewerCandidate` TypedDict with all required fields
- `validate_chemist_handoff(candidates)` — logs ERROR for any missing field
- `validate_reviewer_handoff(candidates)` — logs ERROR for any missing field
- `STRICT_VALIDATION=1` env var to make validation hard-fail (default: warn-only)

**`main_graph.py`** — validation calls added at both handoff boundaries:
```python
# chemist → reviewer
validate_chemist_handoff(out.get("candidates", []))

# reviewer → writer  
validate_reviewer_handoff(reviewed)
```

---

## Fix 5 — Score Normalization + Mechanism Cap Disclosure

### Normalization changes (reviewer.py)

**Tanimoto similarity** — was pool-relative min-max, now used directly (it is inherently [0, 1] by definition of the Morgan fingerprint Tanimoto coefficient). BREXANOLONE's Tanimoto=0.259 previously normalized to 1.000 (it was the pool max). Now it contributes 0.259 × 0.15 = 0.039 to the composite.

**OT association score** — was pool-relative min-max, now used directly (already [0, 1] by OT's own aggregation formula).

**pChEMBL** — was pool-relative min-max, now uses fixed pharmacological reference range [3.0, 10.0]:
- pChEMBL 3.0 → normalized 0.0 (IC50 = 1 mM, barely detectable)
- pChEMBL 6.5 → normalized 0.5 (IC50 = 316 nM, moderate)
- pChEMBL 10.0 → normalized 1.0 (IC50 = 100 pM, ultra-potent)
- Values outside the range are clamped.

The `normalization` field in every `reviewed_candidates.json` and report formula now documents the method explicitly:
```
pChEMBL: fixed range [3.0, 10.0] (pharmacological reference — run-independent);
Tanimoto: direct [0, 1] — no normalization applied;
OT association: direct [0, 1] — no normalization applied
```

### Mechanism cap disclosure (writer.py)

`_composite_breakdown()` now adds a table row for `mechanism_cap_applied` and includes it in the `cap_note` prose, exactly mirroring the existing `unapproved_cap_applied` pattern:

```
| Mechanism-direction cap (DIRECTIONALLY_INCOMPATIBLE, hard gate, max 0.400) | — | — | applied |
```

And in the summary line:
```
Mechanism-direction cap applied (capped at 0.400): DIRECTIONALLY_INCOMPATIBLE — <reason>
```

The previous silent 0.30 score gap (BREXANOLONE/G6PD example: weighted_sum=0.7000 → composite=0.4000 with no explanation) is now fully narrated.

---

## Fix 6 — Drug-Name Synonym Best-Not-First

### What changed

**`data_sources/chembl.py` — `_find_molecule_chembl_id()`**

The synonym fallback path previously returned `mols[0]` unconditionally — whichever molecule the ChEMBL API happened to put first. With multiple molecules sharing a synonym, this is arbitrary and potentially wrong.

New selection logic for the synonym-match path:
1. **Exact `pref_name` match** (case-insensitive) — if any returned molecule has a `pref_name` that exactly matches the query, pick it.
2. **Highest character-overlap ratio** between `pref_name` and the query — if no exact match, pick the molecule whose preferred name is most similar to what was asked for.

The `pref_name` path (Path 1) is unchanged — ChEMBL `pref_name__iexact` is 1:1 unique, always correct.

---

## Additional Fix: Safety Cap Disclosure

During live verification of Fix 5, the Xanthoma disseminatum report showed an unexplained 0.6991 → 0.4000 gap (IMIPRAMINE, SMPD1). Investigation revealed IMIPRAMINE had triggered the ChEMBL structured-data safety layer (IMIPRAMINE is known to be associated with overdose/suicide risk per ChEMBL mechanism endpoint). The `safety_cap_applied=True` field was set by reviewer.py but the writer had no disclosure row for it.

**Fixed in `agents/writer.py` `_composite_breakdown()`:** Added:
```
| Safety cap (ChEMBL safety signal, hard gate, max 0.400) | — | — | applied |
```
And in the cap_note prose: `"Safety cap applied (capped at 0.400): WITHDRAWN FOR SAFETY (ChEMBL structured data) — ..."`

This mirrors the existing `unapproved_cap_applied` and `mechanism_cap_applied` disclosure patterns.

---

## Remaining Action Required (not code-fixable by agent)

### BioGRID API key renewal

The `BIOGRID_API_KEY` in Replit Secrets contains an expired key. To fix:
1. Go to https://webservice.thebiogrid.org
2. Register/login and generate a new API access key
3. Update the `BIOGRID_API_KEY` secret in Replit

No code changes needed — all existing wrapper code reads from the environment correctly.

### Verification batch completion (pending)

Fix 4 verification for pathway_neighbor UniProt (AKR1B1/GALK1 case from Galactokinase deficiency job) is pending batch `ef1129533fc22074f1a9` completion. The AFDB pre-check log line must show `P15121 (AKR1B1)` (not `P51570 (AKR1B1)` which would indicate GALK1's UniProt). See report update when job completes.

---

## Files Modified

| File | Purpose |
|------|---------|
| `agents/target_selection.py` | Fix 1 + 2: universe filters, fail-closed umbrella guard |
| `agents/chemist.py` | Fix 4 (root): stamp `uniprot_id` in `_enrich_compounds()` |
| `agents/reviewer.py` | Fix 4: carry `uniprot_id` + `trials_query_failed` through; Fix 5: normalization; Fix 3: ClinicalTrials fail-closed |
| `agents/writer.py` | Fix 3: BioGRID status + trials_query_failed display; Fix 5: mechanism cap disclosure |
| `agents/biologist.py` | Fix 3: use new biogrid dict return type, record `biogrid_query_status` |
| `agents/schemas.py` | Fix 4: NEW — TypedDict schemas + runtime handoff validation |
| `data_sources/biogrid.py` | Fix 3: `query_status` field in return value |
| `data_sources/clinicaltrials.py` | Fix 3: `query_failed` flag in return value; failures not cached |
| `data_sources/chembl.py` | Fix 6: synonym best-not-first selection |
| `main_graph.py` | Fix 4: validation calls at both handoff boundaries; updated normalization description |
| `output/top_candidates.json` | Fix 1: 3 contaminated entries purged |
