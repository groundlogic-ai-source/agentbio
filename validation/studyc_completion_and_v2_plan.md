# Study C — completion (v1) and v2 plan

Status as of 2026-08-19. Decided with the user: finish v1 (Path 3), then run a
universe-verified v2 (Path 2). AgentBio is the product name; "Silver Bullet"
appears only in legacy strings.

## Path 3 — v1 finalization (this publish)

The frozen 27-case set contained 20 diseases outside the rare-disease/NTD
universe — they were never scorable by a rare-disease tool and stranded in
`skipped` forever, which blocked results from being written at all.

Resolution (amendment #3, RULE_FINGERPRINT unchanged): out-of-universe cases
are recorded as permanent disclosed exclusions, the same path as
deterministically-unscorable cases.

Final v1 scope: **6 scored diseases + 21 disclosed exclusions.**

To eliminate the risk of yet another prod-side defect, finalization runs in
dev against the checkpoint snapshot pulled from prod
(`validation/run_studyc_local_finalize.py` — identical code path; only the
git commit-pin check is made to fail open, exactly as it does in the prod
snapshot which ships without git). No LLM calls on this path: all six pools
are checkpoint-finalized, exclusions raise before any agent call, and drug
profiling is the deterministic LLM-free audit lane.

Shipped in the publish snapshot:

- `validation/triage_discrimination_studyc_results.json` (results of record,
  hash-bound to cases + rule fingerprint)
- `validation/triage_discrimination_studyc_checkpoint.jsonl.gz` (final state,
  including all 21 exclusion records)
- `validation/.prod_studyc_done` = "complete" (terminal marker: the prod
  supervisor chain exits at boot and never runs the runner again)

## v1 outcome (2026-08-19, results_sha256 247bed53…)

**The per-disease rank AUC is vacuous: 0/6 diseases have both a pool-present
positive and a pool-present negative, so no contrast is computable.**

Verified against the checkpoint pools by substring search (e.g. the Lupus
pool contains three betamethasone esters but no plain betamethasone): this is
**genuine pool absence, not a name-matching artifact**. Drivers, all matching
the documented miss classes:

- biologics (infliximab, adalimumab, vedolizumab, pertuzumab) never enter an
  activity-data pool;
- the known drug's target was not among the disease's top-3 selected targets
  (bortezomib/cyclophosphamide in Multiple Myeloma; hydroxychloroquine in
  Lupus) — the target-not-considered class;
- ChEMBL assay-strictness pool gaps (existing task: recover Sapropterin-like
  drugs).

Pool presence (reported, never scored): positives found 6/22, negatives 1/9.
Conditional on presence, known positives ranked high (ranks 7–64 in pools of
1,140–22,702 candidates), but with a single pool-present negative there is no
separation claim to make.

**Honest framing for adoption:** v1's result is "pool coverage is the binding
constraint; ranking conditional on coverage looks strong in 6 anecdotes." That
is a coverage finding, not a discrimination finding — and it independently
motivates the already-tracked pool-recovery work.

v1 is reported as a **pilot** with this outcome disclosed; 6 diseases could
never have anchored the adoption claim alone, and as completed they anchor a
coverage claim instead.

## Path 2 — Study C v2 (universe-verified case set)

## Path 2 — Study C v2 (universe-verified case set)

Goal: n = 15–20 scored rare diseases. At n = 16, an observed 80%
discrimination recall carries a 95% Wilson CI of roughly 55–93% — wide but
defensible; v1's n = 6 interval (36–99%) is not.

Design rules, each traceable to a v1 failure:

1. **Universe verification at freeze time.** The case-set build script runs
   `select_for_disease` on every candidate; only in-universe diseases enter
   the frozen case set, and each case records its resolved Orphanet ID as
   evidence. v1 froze 20 unverifiable cases because verification was never
   part of the freeze.
2. **Orphanet canonical names only.** v1 lost genuinely rare diseases
   (Aplastic Anemia, Hamman-Rich syndrome, Myelofibrosis, Iron Overload) to
   strict name-matching. v2 resolves names against Orphanet at case-build
   time instead of trusting colloquial labels. (The underlying lookup
   robustness gap is a separate product fix, not a study fix.)
3. **Same anchors as v1:** ≥1 confirmed repurposed positive AND ≥1
   genuine-failure negative per disease, so a per-disease ranking contrast
   is always defined.
4. **Prevalence-band strata** per the benchmark selection protocol.
5. **Freeze gates:** universe verification + ChEMBL health probe + no
   concurrent LLM-bearing run + amend-never-regenerate.
6. **Cost:** the rationale budget gate (top-25 per pool) keeps total LLM
   spend under ~$10; wall-clock ~2–3 days at observed throughput with the
   streaming checkpoint loader.
7. **v1 stays unedited** as the pilot; v2 becomes the result of record for
   the discrimination claim.

Sequencing: v1 results ship first (this publish, $0 remaining). v2 case-set
construction and freeze can start immediately after — it is cheap and
offline-first — with the run itself greenlit once sources are healthy.
