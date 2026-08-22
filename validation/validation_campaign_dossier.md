# AgentBio — Validation Campaign Evidence Dossier

_Compiled 2026-08-21 from the artifacts of record. This dossier is a reading guide:
every number below is quoted from its cited artifact; no study was re-run, re-scored,
or edited to produce it. Frozen, hash-pinned studies are labeled as such; development
instruments and computed summaries are labeled as such and are not frozen studies.
Where a number and its source artifact ever disagree, the artifact is authoritative._

## What AgentBio is

AgentBio is a drug-repurposing research pipeline for rare and neglected diseases. Given
a disease, it selects candidate molecular targets (Open Targets genetic/literature
association plus pathway context), assembles candidate compounds from bioactivity and
mechanism evidence (ChEMBL, DrugCentral, GtoPdb, BindingDB), ranks them, and produces an
audited dossier per candidate. An **audit layer** independently checks every dossier for
known defect classes (safety mislabeling, mechanism-direction errors, modality mis-scope,
evidence gaps) before results are presented.

This dossier answers two questions an evaluator should ask separately:

1. **Discovery accuracy** — does the machine re-find drugs already known to be
   repurposed into a disease, when that knowledge is withheld from it?
2. **Audit integrity** — does the audit layer actually catch defects when they are
   present, without crying wolf on clean results?

## Methodology commitments (why this record is trustworthy)

- **Frozen studies.** Each result of record comes from a frozen study at a pinned code
  commit, with results hash-bound to the artifact, and reruns of completed studies are
  technically refused by the harness itself. One qualification, now registered as
  Amendment 3 of the audit v2 protocol: audit v2's first freeze was destroyed by an
  environment restart *before any scoring*, and the scored claim set is the registered
  rebuild under identical rules. Development instruments (the reviewer pilot, the
  miss-classifier summary, the early retrospectives) are not frozen studies and are
  labeled as such wherever they appear.
- **Failures are published, not edited.** A terminated benchmark and a failed audit
  study below are part of the permanent record. Fixes were made under registered
  amendments, and successor studies were new freezes — never silent edits.
- **Denominators always travel with rates.** Every headline below shows its funnel, not
  just its percentage.
- **Scope is stated per study.** Each entry lists what it supports and what it does not.

## Campaign timeline

| Date | Study | Outcome |
|---|---|---|
| 2026-07-27 | First 7-case retrospective | 1/7 rediscovered — scoping probe |
| 2026-07-29 | repoDB 10-case + combined 13-case + Top-K harness | 0/9, 1/13, 0/10 — established the biologic scope boundary |
| 2026-08-01 | **Rediscovery benchmark v1** (frozen) | **Terminated at 14/50 by protocol decision** |
| 2026-08-03 | Reviewer pilot (9-case) | 1/9 rediscovered; true target considered 8/9 |
| 2026-08-05 | Audit trap benchmark | PASS 12/12 traps — engineering acceptance only |
| 2026-08-09 | **Rediscovery benchmark v2** (frozen) | **27.3% in-scope rediscovery — result of record** |
| 2026-08-10 | **Audit claim-set v1** (frozen) | **FAIL — published unedited** |
| 2026-08-11 | **Audit claim-set v2** (frozen, registered rebuild) | **PASS — result of record** |
| 2026-08-11 | repoDB miss-classifier summary | 1/15; projected ceiling 10/15 under then-planned fixes |
| 2026-08-19 | Study C v1 (triage discrimination) | 6 scored + 21 disclosed exclusions; AUC vacuous |
| 2026-08-20 | Machine v2 acceptance | 0/16 v1 misses rescued — coverage ceiling is mechanistic |

## Results of record

| Claim area | Study of record | Headline | Verdict |
|---|---|---|---|
| Discovery rediscovery | Rediscovery benchmark v2 (`benchmark-freeze-v2`) | 32/50 selected cases screened (64%); **6/22 in-scope rediscovered (27.3%)**, all in top 10, 2 STRONG_MATCH | Complete |
| Audit defect detection | Audit claim-set v2 (`audit_claimset_v2`) | **Defect recall 60/60 (100%)**, control false-flag 2/40 (5.0%) | PASS vs pre-registered thresholds |
| Ranking discrimination | Study C v1 | No computable AUC (coverage-bound) | Pilot; supports no discrimination claim |

---

## Track 1 — Discovery accuracy

### Rediscovery benchmark v2 — the result of record (2026-08-09, frozen)

- **Artifact:** `validation/benchmark_results_v2.json` (47 case-rows: 32 screened
  primary + 15 development; per-case drug/disease identities and outcomes are embedded
  in the artifact). The results file's SHA-256
  (`b318f61c892b76df63c1e4a673d7e9be082b16b609e19722991b673e1cfc2c1d`) is pinned in the
  repository (`validation/benchmark_v2_completion.py`) and verified on every status
  read.
- **Provenance caveat (disclosed, not repaired):** the run executed under freeze
  `benchmark-freeze-v2` in deployment-attestation mode on the production deployment.
  The results artifact's own metadata names two inputs: `case_list` —
  `validation/benchmark_case_list.json`, the pre-screen 50-case selection, committed
  2026-08-01 (seed 20260731, eight days before the run), pinned by the
  `benchmark-cases-v2` tag and byte-identical at HEAD — and `screened_list` —
  `validation/benchmark_case_list_v2.json`, the post-screen 32-case file, which was
  NOT preserved. The screening *criteria* are code-locked pre-hoc:
  `validation/screen_v2_cases.py` carries the frozen Amendment-1/3 parameters
  (committed 2026-08-05, four days before the run; a post-run commit only pinned the
  source set for rerun reproducibility and changed no parameter). The screening
  *outcomes* depended on live Open Targets/ChEMBL data at run time, so they cannot
  be independently re-derived offline today; they are evidenced by the frozen
  artifact's rows, all of which match the committed selection (see below). Still
  missing: the deployment-side freeze attestation
  (`benchmark_freeze_v2_attestation.json`) and the `benchmark-freeze-v2` tag — the
  production source fingerprint cannot be independently re-verified, and campaign
  policy forbids reconstructing it. **Row-level cross-verification (2026-08-21,
  rerunnable via `validation/verify_v2_provenance.py`):** all 32 primary case-rows
  in the results artifact identity-match (drug + disease) the committed 50-case
  list — 32/32, no exceptions — and the artifact's funnel (32 screened → 22
  in-scope → 6 hits; 13 wrong-target + 3 unresolved misses; 10 out-of-scope)
  reproduces exactly from the per-row data.
- **Funnel (primary set):** 50 cases selected by pre-registered criteria from 9,057
  dataset rows (attrition: `validation/benchmark_attrition.md`, which also discloses 7
  coverage failures at selection) → 32 passed the feasibility screen (64%) → 22
  in-scope and scored → **6 rediscovered (27.3%)**. Of the 32 screened primary rows,
  the frozen artifact records 10 out-of-scope (umbrella/out-of-universe disease terms)
  and 16 genuine misses — 13 wrong-target and 3 unresolved-no-mechanism by the
  frozen miss classifier.
- **All 6 hits ranked in the top 10** (ranks 3, 4, 4, 5, 5, 8): Tretinoin/APL,
  Mercaptopurine/APL, Lansoprazole, Omeprazole, Esomeprazole, Vincristine, in reviewed
  pools of 85–200 candidates (the largest reviewed pool in the study held 557). 2 of 6
  reached the pre-registered STRONG_MATCH bar.
- **Holdout discipline:** disease-side signals (precedent lists, indication fallback,
  has-approved) were redacted against the held-out drug; the bioactivity pool is
  deliberately present-day — see "What this does not support."
- **Run integrity:** single pre-registered run; the harness now refuses any rerun of
  the frozen artifact.
- **Supports:** on in-scope rare-disease cases that pass screening, the pipeline
  rediscovers a known repurposing in its top 10 roughly one time in four — the six
  hits ranked 3–8 within their reviewed pools of 85–200 candidates.
- **Does not support:** a prospective discovery rate; historical counterfactual claims
  (the bioactivity pool is present-day data); any claim about the 36% of selected cases
  the screen set aside; performance on biologics.

### Rediscovery benchmark v1 — terminated by protocol (2026-08-01, frozen partial)

- **Artifact:** `validation/benchmark_results_v1_partial.json` + report
  `validation/benchmark_v1_partial_report.md` · freeze tag `benchmark-freeze-v1`.
- **Tally at termination (14/50 cases):** 1 hit, 5 genuine misses, 5 errors, 3 admin
  exclusions.
- **Why terminated:** the 5 genuine misses exposed two structural defects — a strict
  human IC50/Ki-only candidate pool (common to all five misses) and a
  genetic-association-only target ranking (observed across many confirmed repurposes
  in the cohort). Termination was a pre-registered protocol decision on diagnosed
  defects, not a results-based re-roll; v1 and v2 are never merged.
- **Disclosed v1 limitation:** literature (PubMed) was not sealed against the held-out
  drug in v1; it was sealed in v2.
- **Supports:** the documented defect diagnosis that motivated v2.
- **Does not support:** any rate estimate (n = 11 scorable at termination).

### Reviewer pilot (2026-08-03, development instrument)

- **Artifact:** `validation/reviewer_pilot_report.md` (source
  `validation/repodb_results_smallmol.json`).
- **Headline (9 cases):** 1/9 rediscovered (11.1%, Ibrutinib at rank 2); the true
  mechanism target was *considered* in 8/9 cases; 3/9 right-target but the strict
  activity pool was blind; 4/9 lost because only the top-1 target was pursued.
- **Supports:** the target-coverage vs. pool-coverage decomposition of misses; a
  positive control recovering near the top.
- **Does not support:** a rediscovery rate — the case set is a hand-readable pilot, not
  the frozen benchmark population, and makes no significance claim.

### Early retrospectives (2026-07-27 / 2026-07-29, scoping probes)

- **Artifacts:** `validation/results.md`, `validation/combined_table.md`,
  `validation/repodb_results.md`, `validation/repodb_results_smallmol.json`.
- **Headlines:** 1/7 (first probe); 0/9 in-universe on a biologic-leaning 10-case set;
  1/13 combined; 0/10 on the top-K harness.
- **What they established:** biologics are structurally outside the small-molecule
  activity pool — the dominant miss type in these cohorts (10/11 combined-table
  misses), an honest scope boundary, not a scoring failure — and, in the small-molecule
  subset, some target-rank limitations (e.g. CRBN at rank 6) that the reviewer pilot
  later quantified. Both findings fed the benchmark design.
- **Status:** superseded by the frozen benchmarks; retained for the audit trail.

### repoDB miss-classifier summary (2026-08-11, computed)

- **Artifact:** `validation/rediscovery_summary.md`.
- Combined 1/15 hits; miss classes: 8 wrong-target (5 with the true target already in
  the considered list), 4 right-target/pool-gap, 1 biologic-structural. Projected
  ceiling 10/15 under the then-planned pool and target fixes.
- **Supports:** where the recoverable headroom was believed to sit before machine v2
  was measured.
- **Does not support:** a current-system rate — it classifies development-cohort misses.

### Study C v1 — triage discrimination pilot (2026-08-19, frozen)

- **Artifact:** `validation/triage_discrimination_studyc_results.json` (hash-bound);
  narrative `validation/studyc_completion_and_v2_plan.md`.
- **Scope:** 6 scored diseases + 21 disclosed exclusions (the frozen 27-case set
  contained 20 out-of-universe diseases, recorded as permanent exclusions).
- **Outcome:** the per-disease rank AUC is **vacuous** — 0/6 diseases have both a
  pool-present positive and a pool-present negative, so no contrast is computable.
  Pool presence (reported, never scored): positives 6/22, negatives 1/9. Conditional on
  presence, known positives ranked in the top ~1.5% (ranks 7–64 in pools of
  1,140–22,702).
- **Supports:** a **coverage** finding — pool coverage is the binding constraint — and
  the anecdote that found positives rank very high.
- **Does not support:** any discrimination/separation claim.

### Machine v2 acceptance (2026-08-20, integrity-gated measurement)

- **Artifact:** `validation/machine_v2_acceptance.json` (exit 0, contract
  `machine-v2-acceptance-v1`).
- Two coverage lanes were built and measured against v1's misses: Reactome
  pathway-neighbor universe expansion and a mechanism-only approved-drug pool
  supplement. **Result: 0/16 v1 misses rescued.** The missed drugs' targets are
  mechanistically orthogonal to the diseases' genetic/literature neighborhoods, not
  pathway-adjacent.
- **Supports:** the coverage ceiling is mechanistic novelty — drugs whose working
  target has no genetic, literature, or precedent link to the disease are unreachable
  by any target-anchored lane, and the team measured this rather than assuming it.
- **Does not support:** that the new lanes are useless — universe superset holds on all
  six v1 diseases and sparse-universe diseases gain real targets; they simply do not
  move this established-therapy cohort.

---

## Track 2 — Audit integrity

### Audit claim-set v2 — the result of record (2026-08-11, frozen, PASS)

- **Artifact:** `validation/audit_claimset_v2_results.md` (+ raw outputs, freeze
  manifest, construction log) · claim-set sha256 `5013a57aca080e82…` · freeze code
  commit `0cce8376`.
- **Headline vs pre-registered thresholds:**
  - Defect recall: **60/60 = 1.000** (95% Clopper–Pearson lower 0.951; threshold
    ≥ 0.80, lower ≥ 0.65 — met).
  - Control false-flag: **2/40 = 0.050** (CP upper 0.149; threshold ≤ 0.15, upper
    ≤ 0.30 — met).
  - Novel-lane recall (no threshold pre-registered): 59/59 = 1.000.
  - Disclosure accuracy (non-scored): 0 caught defects contradicted the external
    artifact.
- **Defect classes tested:** combination-product splitting (8/8), biologic modality
  mis-scope (43/43), dose/route implausibility (8/8), boxed-warning-vs-withdrawal
  (1/1).
- **Supports:** the audit layer catches seeded real-world defects without material
  false-flagging of clean candidates, under independent ground truth with a mechanical
  citation cutoff (2026-08-10).
- **Does not support (registered limitations):** E1 (safety withdrawal) and E3
  (direction incompatible) were essentially tested only in v1; E4 (unresolvable-name
  honesty) was near-empty by construction; N3 (species/preclinical-only) yielded zero
  claims under v2's gates and is **untested** — reported, not padded. Pool-context
  coverage is limited to two persisted cases. The defect mix is dominated by the
  modality class (43/60), so the recall figure is strongest exactly there.

### Audit claim-set v1 — published failure (2026-08-10, frozen, FAIL)

- **Artifact:** `validation/audit_claimset_results.md` · claim-set sha256
  `32efd7d965f62e2c…` · freeze code commit `f41d7661`.
- **Headline:** defect recall 32/60 = 0.533 (threshold ≥ 0.80 — **not met**); control
  false-flag 7/40 = 0.175 (threshold ≤ 0.15 — **not met**). Per-class, the misses were
  concentrated in two classes: boxed-warning-vs-withdrawal (**0/19 caught**) and
  unresolvable-name honesty (**0/8 caught**); dose/route implausibility was caught 8/9.
  The classes that passed were combination-product splitting (8/8), biologic modality
  mis-scope (13/13), safety withdrawal (2/2), and direction incompatibility (1/1).
  Novel-lane recall (no threshold pre-registered, as in v2): 29/30 = 0.967 (CP lower
  0.851) — included here for symmetry with the v2 headline.
- **What happened next:** the failure was diagnosed to the black-box/withdrawal
  classifier conflation, fixed under amendment, and re-measured as a **new frozen
  study** (v2). v1 was left unedited; both artifacts remain in the record.
- **Supports:** the amendment trail is real — a failed frozen study exists and was not
  laundered.
- **Does not support:** any current-performance claim (superseded by v2).

### Audit trap benchmark (2026-08-05, engineering acceptance)

- **Artifact:** `validation/audit_trap_results.md`.
- 12/12 seeded traps caught (threshold ≥ 0.9); 1/4 clean controls false-flagged (at the
  ≤ 0.25 threshold); precision 0.92. Runs offline against stubbed inputs.
- **Supports:** regression protection for twelve known failure classes (safety-cap
  disclosure, salt-form dedup, degraded-source honesty, holdout leakage, and more).
- **Does not support:** by its own registered banner, this is an **engineering
  acceptance instrument, not discovery accuracy, and not external validation** — it
  must never be cited as benchmark evidence.

---

## Questions an evaluator will ask

**"Your audit recall is 100%, but 43 of 60 defects are one class. Isn't that narrow?"**
Yes, and it is registered as such. The composition is construction-determined (the
honest pool-bounded yield plus registered reallocation). E1/E3 have v1-era coverage
only, E4 is near-empty, and N3 is untested. The claim to take away is "verified on the
classes tested, with the untested classes named" — not "catches everything."

**"Everything here is retrospective. Why should we believe prospective performance?"**
You shouldn't infer it from these studies, and we don't ask you to. All rediscovery
evidence is measured in present-day data under disease-side holdout; the bioactivity
pool is deliberately not redacted because pool presence *is* the rediscovery event.
Prospective validation is the planned next step with a partner org, not a claim made
here.

**"Your discrimination study produced no AUC. Isn't that a failure?"**
It is a null result, published as one. Study C v1 established that pool coverage — not
ranking — is the binding constraint (positives present 6/22, negatives 1/9, so no
contrast exists to compute). The follow-on machine-v2 measurement (0/16 rescue)
pinpointed the ceiling as mechanistic novelty. Together they redirect the claim from
"the ranking separates" to "found positives rank very high; coverage is the frontier."

**"Only 6 rediscoveries — aren't the confidence intervals huge?"**
Yes. 6/22 is 27.3% with a wide interval, and the dossier presents the full funnel
(50 → 32 → 22 → 6) precisely so the denominator is never ambiguous. The early
development cohorts (1/7, 0/9, 1/13, 1/9) are shown so the improvement trajectory —
and its drivers — are visible rather than cherry-picked.

**"You terminated benchmark v1 and failed audit v1. How do we know v2 wasn't re-rolled
until it passed?"**
Both v1 artifacts remain frozen and unedited; every change between v1 and v2 is a
registered amendment with a stated cause; audit v2 is hash-bound to its freeze code
commit, and benchmark v2's results artifact is hash-pinned (with its disclosed
provenance caveat above); the completed-run harness now refuses reruns outright. The
record is designed to make silent re-rolling technically impossible, not merely
impolite.

**"Can we verify any of this independently?"**
Every headline in this dossier cites its artifact; the frozen artifacts carry their
hashes, freeze tags/commits, and construction logs. The audit claim sets include raw
per-claim outputs for spot-checking against external ground truth. One gap is
disclosed up front: benchmark v2's deployment-side freeze attestation was not
preserved (see its Provenance caveat), so the production source fingerprint cannot be
re-verified against the original attestation. What *is* independently checkable: the
pre-screen case selection (committed, tagged `benchmark-cases-v2`, eight days before
the run), the screening criteria (committed code, four days before the run), and the
row-level identity match between the frozen results and that selection —
`validation/verify_v2_provenance.py` regenerates all of this evidence on demand.

---

## The honest claim set

**Supported today:**

1. The audit layer is verified against independent ground truth: 100% recall on 60
   seeded defects across four classes, 5% false-flag on 40 clean controls (frozen v2,
   pre-registered thresholds met).
2. On in-scope, screen-passing rare-disease cases, the discovery pipeline rediscovers
   a known repurposing in its top 10 roughly one time in four (6/22), with all hits
   ranked 3–8 in reviewed pools of 85–200 candidates. (In the separate Study C pilot,
   pool-present positives ranked 7–64 in pools of 1,140–22,702 — a coverage anecdote,
   not a discrimination result.)
3. The failure modes are measured and named: mechanistic-novelty coverage ceiling,
   biologic scope boundary, and pool evidence gaps — each with a frozen study behind it.
4. The campaign methodology — frozen runs, published failures, registered amendments,
   hash-bound artifacts — is itself a demonstrated capability.

**Not claimed:**

- Prospective or historical-counterfactual discovery accuracy.
- Ranking discrimination between positive and negative controls (no computable AUC).
- Audit coverage of the named untested defect classes (E1/E3 v1-era only; E4 minimal;
  N3 untested).
- Any performance claim for biologics as discovery candidates.

---

## Artifact index

| Artifact | Study | Frozen |
|---|---|---|
| `validation/benchmark_results_v2.json` | Rediscovery benchmark v2 (result of record) | ✅ results hash-pinned; ✅ pre-run selection + screen criteria committed/tagged pre-run; ⚠ screened-list file + deployment attestation not preserved (disclosed) |
| `validation/verify_v2_provenance.py` | Benchmark v2 provenance evidence regenerator | read-only, rerunnable |
| `validation/benchmark_results_v1_partial.json` + `benchmark_v1_partial_report.md` | Rediscovery benchmark v1 (terminated) | ✅ `benchmark-freeze-v1` |
| `validation/benchmark_attrition.md` | Case-selection funnel | ✅ |
| `validation/audit_claimset_v2_results.md` (+ raw, manifest, log) | Audit claim-set v2 (result of record) | ✅ sha `5013a57a…` |
| `validation/audit_claimset_results.md` (+ raw) | Audit claim-set v1 (published FAIL) | ✅ sha `32efd7d9…` |
| `validation/audit_trap_results.md` | Trap benchmark (engineering acceptance) | ✅ label-locked |
| `validation/reviewer_pilot_report.md` | Reviewer pilot | development |
| `validation/rediscovery_summary.md` | repoDB miss-classifier summary | computed |
| `validation/results.md`, `combined_table.md`, `repodb_results.md` | Early retrospectives | superseded |
| `validation/triage_discrimination_studyc_results.json` | Study C v1 | ✅ hash-bound |
| `validation/studyc_completion_and_v2_plan.md` | Study C narrative + machine v2 | — |
| `validation/machine_v2_acceptance.json` | Machine v2 acceptance | ✅ integrity-gated |
