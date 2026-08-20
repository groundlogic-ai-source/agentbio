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

## Miss autopsy + rescue analysis (2026-08-20)

Post-hoc forensics over the frozen v1 artifacts
(`validation/studyc_miss_autopsy.json`, `validation/studyc_target_rank_rescue.json`;
scripts re-runnable, cached, no pipeline stage re-run):

**Absence causes, 22 confirmed positives:**

| class | n | implication |
|---|---|---|
| found in pool | 6 | all ranked top ~1.5% |
| target_not_selected | 11 | drug's ChEMBL mechanism target was not among the disease's top-3 selected targets |
| biologic_structural | 4 | antibodies never enter an activity-data pool |
| no_mechanism_data | 1 | resolvable molecule, no ChEMBL mechanism rows |
| name_resolution_gap | 0 | both "unresolved" drugs resolved with curated alternates (pentosan polysulfate sodium, levocarnitine) |

**Rescue analysis: K-widening buys nothing.** For all 11
target_not_selected positives, the drug's mechanism target does not appear
ANYWHERE in the disease's ranked candidate-target list (lists of 5–20
targets): rescued_at_k = 0 for K = 3, 5, 10, 25. The wall is upstream of the
top-K gate — it is the target UNIVERSE construction (OpenTargets genetic
associations + approved-drug MOA targets). Bortezomib's PSMB5 is not among
myeloma's 20 candidate targets; triamcinolone's NR3C1 is not among GCA's 5.

Notably, the ChEMBL assay-strictness class (the Sapropterin/Pyridostigmine
pool-recovery task) occurred ZERO times in this case set — that upgrade would
not have moved v1 coverage. Caveat: ChEMBL mechanism coverage is itself
incomplete (e.g. hydroxychloroquine's immunomodulation), so "target not in
universe" partly reflects mechanism-knowledge gaps, not only selection.

**Upgrade ranking by expected coverage gain:**

1. **Target-universe expansion** (rescues the 50% class): pathway/PPI
   neighbor lanes — the Reactome pathway-neighbor prototype already exists
   (MTOR confirmed neighbor of TSC1). This is disease-blind (uses no answer
   drugs), so benchmark integrity is preserved. Tradeoff to decide: loosens
   the genetics anchor that is part of AgentBio's precision story.
2. **Biologics lane** (rescues the 18% class): mechanism/indication-based
   inclusion of antibodies rather than activity-data pooling.
3. Assay-pool recovery: valuable generally, but would not have changed v1.

**Consequence for v2:** running the discrimination study again on machine v1
would re-measure this ceiling. Build machine v2 (universe expansion first),
then freeze v2 cases against the new machine, and re-run v1's six diseases
as the labeled sensitivity cohort for the before/after claim.

## Machine v2 built + acceptance-measured (2026-08-20)

Shipped two coverage lanes, both kill-switched by `AGENTBIO_DISABLE_V2_LANES=1`:

1. **Path D — pathway-neighbor universe expansion** (`select_for_disease`):
   Reactome neighbors of drug-FREE seed targets only (genetic + literature
   lanes; pharmacological-precedent rows cannot seed), broad_metabolic tier
   excluded, ≤10 per disease, fixed association score 0.05 (half the genetic
   gate — handicapped, score-governed, not strictly subordinated).
2. **Mechanism-only pool supplement** (`run_chemist` +
   `chembl.get_mechanism_only_approved_drugs`): approved drugs with a ChEMBL
   mechanism row but no qualifying IC50/Ki (biologics structurally; the
   Sapropterin assay-strictness class). Rows carry pool_origin/mechanism/
   action through the chemist projection and normalize to a ledger MECHANISM
   record — never a null-pChEMBL bioactivity record. Cache discipline: any
   empty mechanism endpoint across all resolved target IDs = not cached;
   key namespaced `mechanism_only_approved_v2`.

**Acceptance result (validation/machine_v2_acceptance.json, exit 0,
integrity-gated): 0/16 v1 misses rescued.** Universe superset holds on all
six v1 diseases (v2 adds 0–19 targets/disease, e.g. UC 5→24, MM 20→30).
The 11 target_not_selected misses stay out: their targets are mechanistically
ORTHOGONAL to the disease's genetic/literature neighborhood (glucocorticoid
receptor for GCA/Lupus, proteasome/HDAC for myeloma), not pathway-adjacent.
The 4 UC biologics stay out one gate later: TNF/ITGA4 enter no top-5
selection, so the mechanism lane never fires for them.

**What this means:** pathway expansion and biologic lanes are live, safe, and
improve sparse-universe diseases and evidence honesty — but they do NOT move
the v1 cohort. The measured ceiling is mechanistic novelty: drugs whose
working target has no genetic/literature/precedent link to the disease are
unreachable by ANY target-anchored lane. v2 case-set design should therefore
expect recall similar to v1 on established-therapy positives and treat the
discrimination claim as resting on rank quality of found positives, not
coverage growth. A genuinely different lever (indication-anchored /
phenotype-first discovery) would be a separate, larger machine change.

Reviewed by architect subagent across 4 rounds (FAIL→FAIL→FAIL→PASS);
final full suite green. New regressions: validation/test_mechanism_only_lane.py
(6 tests); safety L2 test rot from Amendment 3 fixed (mock now patches
chat_text, the round-robin seam).
