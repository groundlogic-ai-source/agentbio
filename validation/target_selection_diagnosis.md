# Why target selection picks indirect targets — diagnosis & fix proposal

**Date:** 2026-07-31 · **Task:** #33 · **Scope:** reconstruct TSC (PRKAA2 over MTOR) + the retrospective wrong-target misses; identify the demoting term; propose scoped fixes.

---

## Executive summary

Wrong-target selection is **two distinct mechanisms**, not one scoring bug. One theme underlies both: no score in the pipeline measures *"this target is the mechanistic node of the disease."* The scores measure druggability and "some approved drug touches this target" — and two artifacts exploit that gap.

| Mechanism | Cases | What happens |
| --- | --- | --- |
| **A. Pool zeroing by transient API degradation** | TSC → PRKAA2 (Jul 28 UI run) | The direct node (MTOR) is eliminated *before any scoring*: a degraded ChEMBL response is cached as a genuine empty pool. |
| **B. Precedent hijack** | Imatinib×2 (IL5, JAK1), Riociguat (PDE5A), Lorazepam (CA2), Temozolomide (FKBP1A) | A flat 0.90 precedent score lets any approved drug's MOA target — even one imported from a parent-EFO umbrella — outrank the causal/driver gene. |

Wrong-target ≈ 8 of 13 small-molecule cases; in **5 of 8 the confirmed drug's true target is already in the considered list** — the failure is selection, not discovery.

---

## Case 1: TSC — PRKAA2 beat MTOR (pathway-neighbor class)

### Reconstruction (job c5cec9422eaf4436b419f78e58aa1b08, 2026-07-28T16:00)

1. **Stage 1 worked.** TSC1 — the causal gene — won Stage 1 (tractability 0.6632, per the archived report). The failure happened *downstream* of a correct selection.
2. TSC1's own approved-drug pool was thin → **lazy pathway-neighbor expansion** fired (chemist.py). Both **MTOR** and **PRKAA2** are TSC1 neighbors (pathway_count=3 each, same specificity tier — Reactome does not separate them).
3. **MTOR produced zero candidates.** Evidence: the pool cache row `get_target_candidate_compounds_v2('P42345', 25, True)` contains `{"compounds": []}` and — back-inferred from its 7-day TTL — was written in the **2026-07-28 ~16:00 UTC window, coinciding with the job's creation** (approximate: the cache stores only `expires_at`, not a write timestamp). Yet MTOR's pool is verifiably non-empty (SIROLIMUS pChEMBL 9.075, TACROLIMUS, TEMSIROLIMUS, DASATINIB — confirmed earlier this month).
4. **Root cause of the empty pool:** ChEMBL was in a degraded window (intermittent 500s and 200-OK-with-empty-payload responses — reproduced live today: the same endpoint flapped 500→200→500 within an hour). The code raises on HTTP errors, and the exception path deliberately does not cache — but a **degraded 200 with an empty payload is indistinguishable from genuine emptiness, so it IS cached** (empty-activities `cache_set` in `_fetch_activities_full`, ~line 233; terminal `cache_set` of the empty pool in `get_target_candidate_compounds`). The chemist then treats it as "no compounds exist" and moves on — silently.
5. **Scale of the problem:** a cache sweep today found **341 empty-pool rows** (purged; they self-heal by refetch). This is systemic, not one bad row.
6. **Why PRKAA2 then won on "merits":** every neighbor compound is stamped `ot_association_score = 0.0`. The reviewer composite weights are pchembl .30 / confidence .20 / **ot .20** / tanimoto .15 / no_failed_trial .15 — with ot zeroed for all neighbors, the contest is pure pharmacology. EBASTINE (pChEMBL 7.58, conf 9) → 0.5696. Had MTOR's pool been intact, SIROLIMUS (pChEMBL 9.075, conf 9, structurally similar to co-pool rapalogs) would composite ≈0.63–0.72 under the formula *(scenario estimate — the exact value depends on run-time Tanimoto and trial-credit inputs)* → **likely wins, and TSC becomes a rapalog rediscovery (a hit or nearest-sibling near-hit)**.
7. **K-cap amplifier:** Top-K=5 parallel pursuit multiplies ChEMBL fan-out, maximizing exposure to degradation windows exactly when the stakes are highest.
8. **Secondary bug spotted:** the report's "Target druggability context" for the *PRKAA2* report lists FKBP1A's mechanism drugs (EVEROLIMUS, SIROLIMUS, TACROLIMUS, PIMECROLIMUS, TEMSIROLIMUS). The dossier stamps the wrong target's context — a credibility hazard in outreach.

## Case 2: precedent hijack (5 recoverable retrospective cases)

Logged ranking inputs from the blind (holdout) runs — `candidate_targets_considered`:

| Case | Winner (precedent, ot=0.90) | True target — in list, demoted |
| --- | --- | --- |
| Imatinib / Hypereosinophilic syndrome | IL5 (mepolizumab) | **PDGFRA** ot=0.206 (FIP1L1-PDGFRA = the driver) |
| Imatinib / Chronic eosinophilic leukemia | JAK1 (fedratinib/ruxolitinib) | **JAK2** ot=0.289; STAT5B 0.375 |
| Riociguat / CTEPH | PDE5A (sildenafil, *via parent umbrella*) | sGC subunits below genetic fold |
| Lorazepam / Lennox-Gastaut | CA2 (carbonic-anhydrase inhibitors) | SCN1A 0.677, DNM1 0.774; GABA-A subunits in list |
| Temozolomide / Anaplastic astrocytoma | FKBP1A (everolimus's SEGA approval, *via parent umbrella*) | TP53 0.520, IDH1 0.398 (TMZ's real target is DNA — non-protein) |

### Which term demotes the direct target — the task's core answer

`tractability_score = raw_tractability × association_score` with **`PHARM_PRECEDENT_ASSOC_SCORE = 0.90`, flat**, for any approved drug's MOA target. Precedent targets are well-drugged by construction (high raw tractability), so they win the multiplier *and* the base. Genetic/driver associations in these diseases sit at 0.11–0.77 and lose on the multiplier alone. The 0.90 asserts "an approved drug for this disease touches this target" — true, but indifferent to *whether the target is the node the disease's mechanism runs through*. Mepolizumab manages HES eosinophil biology via IL5; imatinib kills the clone via PDGFRA. Both are real approvals; only one is the mechanistic node. The parent-umbrella supplement widens the blast radius by importing indication-adjacent links (everolimus-SEGA → FKBP1A for astrocytoma; sildenafil-PAH → PDE5A for CTEPH).

---

## Fix proposals (scoped, with expected impact)

**F1 — Pool-fetch integrity (highest confidence, do first).** Never cache an empty pool unless every sub-fetch succeeded with non-empty payloads that *filters* then reduced to zero. Empty payload = degraded = return empty but do not cache, and stamp `pool_fetch_failed: true` so the chemist logs loudly (and, in benchmark runs, aborts rather than silently competing a crippled pool). Purge cadence for existing rows.
*Impact:* TSC flips to a rapalog win (hit/near-hit). Protects every future run and the benchmark's integrity during ChEMBL outages. Small diff, no scoring change. *Distinct from task #16 (assay-strictness pool gap): that is genuine data absence; this is fake absence.*

**F2 — Precedent calibration (highest hit-rate leverage).** Two levers, chosen by re-running the 13-case suite under each setting:
  - Demote `pharmacological_precedent_via_parent_umbrella` below direct-EFO precedent (0.90 → ~0.70): parent links are indication-adjacent, not disease-specific.
  - Cap precedent-only targets (no genetic support) below the best genetic target when a genetic target ≥0.5 exists (mechanistic-convergence preference).
*Impact:* 5 of 8 wrong-target cases have the true target in the considered list; flipping even half recovers 2–4 hits → retrospective 1/13 → 3–5/13 (~23–38%), approaching the 6/12 goal. Must verify legitimate precedent picks (e.g. Sildenafil/PDE5A for PAH) aren't suppressed.

**F3 — Dossier target-mismatch.** Stamp druggability context for the *candidate's* target, not the primary (the PRKAA2 report currently advertises everolimus/sirolimus as PRKAA2's mechanism drugs). Small, but it's an outreach-facing correctness bug.

**No code was changed in this task** (diagnosis only). Cache hygiene performed: 341 poisoned empty-pool rows purged; they self-heal on refetch. The concurrently-running biologics suite hit the same outage (Anakinra: target_selection read timeout).
