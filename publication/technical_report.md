# AgentBio — Technical Validation Report

**Version:** 1.0 · **Date:** 2026-08-10 · **Status:** frozen results, pre-publication
**Companion artifacts:** `publication/derived_metrics.json` (all numbers herein are
recomputed by `publication/make_figures.py` from the committed frozen artifacts),
`publication/tables.md`, `publication/figures/`.

---

## 1. What was evaluated

AgentBio is a research-prioritization and evidence-audit system for drug
repurposing in rare and neglected diseases. Given a disease, it ranks
(disease, target) pairs by tractability and unmet need (Stage 1), generates
approved-drug candidates against a selected target from a union of evidence
lanes (Chemist), scores and caps them deterministically (Reviewer), and
produces an auditable dossier (Writer). A separate **audit layer** answers
the inverse question — given a specific drug→disease claim, does the pipeline
surface the defect that would make a naive pipeline untrustworthy?

Three instruments were run, each pre-registered and frozen, and they answer
**different questions that must never be conflated**:

| Instrument | Question | Artifact |
|---|---|---|
| Benchmark v2 (`benchmark-freeze-v2`) | Discovery accuracy: does a confirmed repurposed drug reappear in the ranked list when all drug-side identity is redacted? | `validation/benchmark_results_v2.json` |
| Audit claim-set v1 (`audit_claimset_v1`) | External audit validation: does the audit layer catch real, externally-grounded defect claims — including classes nobody pre-fixed for? | `validation/audit_claimset_results.json` |
| Audit trap benchmark | Engineering regression: do the twelve defect classes AgentBio was explicitly fixed for still trip the audit? | `validation/audit_trap_results.json` |

This report also summarizes, clearly labeled, the development instruments:
the v1 partial benchmark (terminated by protocol), the five-miss engineering
acceptance, the pre-freeze source-ablation control, the reviewer pilot, and
the earlier repoDB development suites.

**Scope statement.** Nothing in this report is evidence of clinical
efficacy, and no output of the system is a treatment recommendation. The
system proposes *research hypotheses* and *audits evidence chains*; every
headline number below measures rediscovery of already-known drug–disease
pairs or detection of planted/real evidence defects under pre-registered
rules.

## 2. Provenance and freeze controls

- **Benchmark v2** ran under freeze tag `benchmark-freeze-v2`. Because the
  production deployment ships without a git repository, the freeze is sealed
  by a deployment attestation (`freeze_mode: deployment-attestation` in the
  results) pinning the pipeline source fingerprint, the completed
  source-ablation control hash, and the screened case-list hash (pre-registration
  Amendment 5). The screened list itself was produced in the deployment; the
  executed primary set in the results artifact *is* the screened list.
- **Audit claim-set v1** is sealed by `validation/audit_claimset_freeze_manifest.json`:
  claim-set sha256 `32efd7d9…`, code-commit ancestry + `.py` drift checks, harness
  config equality, and a results-hash binding that refuses any silent replacement
  of the published outcome. Exactly one scored run was executed; the
  pre-registered one-fix-one-rerun allowance was exercised once for a harness
  plumbing defect discovered *before any metric was computed* (Amendment 3 of
  the preregistration) and is now permanently consumed (committed consumption
  marker + manifest cross-check). Independent recomputation from the raw
  archive reproduces every stored metric exactly (`--recalc-only`).
- **Trap benchmark** ran offline under `audit_trap_benchmark` with its own
  pre-registration; it is an engineering instrument and is never pooled with
  the external audit study.

## 3. Benchmark v2 — discovery accuracy

### 3.1 Design (pre-registered 2026-08-01; amendments through 2026-08-08)

Case selection was mechanical and offline-first from repoDB/DrugCentral:
approved, repurposed-success, small-molecule, one case per drug, dev-suite
drugs excluded at drug level, rare-disease universe membership, EFO
resolvable, ≥1 target in the discovery universe (attrition:
`validation/benchmark_attrition.md`; criteria:
`validation/benchmark_case_selection_criteria.md`, seed 20260731). 50 cases
were selected; the Amendment-1 screen (`v2-screen-1`: OT re-resolution,
umbrella guard, ≥1 gated OT target, non-empty production union pool)
admitted 32. Development-suite cases (15) were run as a clearly labeled
development subset, never pooled with the primary endpoint.

Holdout: each case's drug — names, synonyms, salts, active moiety, source
identifiers — is redacted from disease-side inputs; matching happens only
after ranking. The headline framing is pre-registered: *the hit rate answers
"how good is the ranking core on funnel-feasible diseases," reported in the
same sentence as the funnel-feasibility rate.*

### 3.2 Headline result

**On funnel-feasible diseases (32 of 50 selected cases passed the screen,
64%), the pipeline rediscovered 6 of 22 in-scope primary cases, 27.3% (95%
Clopper–Pearson CI 10.7%–50.2%).** All six rediscoveries ranked in the top 10
(ranks 3, 4, 4, 5, 5, 8); 2 of 6 reached the pre-registered STRONG_MATCH
threshold.

| Subset | Executed | Out of scope | Error | In-scope | Rediscovered | Top-10 | STRONG_MATCH |
|---|---|---|---|---|---|---|---|
| Primary | 32 | 10 | 0 | 22 | 6 (27.3%) | 6/6 | 2/6 |
| Development (labeled) | 15 | 2 | 1 | 12 | 2 (16.7%) | 1/2 | 0/2 |

Prevalence stratification of the primary result is stark and is a scope
finding, not noise: **ultra-rare (<1 per million) 6/9 rediscovered; rare
(1–10/M) 0/6; less rare (>10/M) 0/6.** The pipeline's genetic-association
core works exactly where monogenic rare disease biology is cleanest, and
fails where disease mechanisms are polygenic or symptomatic.

### 3.3 Miss taxonomy (mechanical classifier, `miss_classifier.py` v2)

All 16 primary misses are classified, not hand-waved:
- **wrong_target (13/16)** — the drug's true mechanism target was absent
  from, or outranked within, the considered set. In 8 misses the true target
  *was* considered but at ranks 4–10, i.e. a ranking failure, not a coverage
  failure.
- **unresolved_no_mechanism (3/16)** — no ChEMBL mechanism records exist for
  the drug (e.g. Phenobarbital); the miss is a mechanism-annotation gap, not
  a pool defect.

The 10 development misses are dominated by `biologic_not_addressable`
(enzyme replacements, recombinant proteins, antibodies) — a disclosed
structural scope boundary of the small-molecule contract.

### 3.4 Chance baseline — reported as computed, and why it is uninformative

The pre-registered mechanical baseline (probability a specific drug appears
in the reviewed lists by chance, `1 − Π(1 − n_reviewed/pool)`; exact
Poisson-binomial test) **saturates at 1.0 for every v2 case**, because the
union-lane reviewed lists are as large as or larger than the recorded
per-target pools. Expected hits under the baseline therefore equal the number
of scorable cases and the test carries no information. We report this
mechanically, flag the baseline construction as a limitation (pool
denominators do not reflect the post-union candidate universe), and rely on
absolute rediscovery rates, CIs, strata, and the miss taxonomy instead. A
revised baseline against the true union candidate universe is future work;
it is not recomputed here because any change after seeing results would be
post hoc.

### 3.5 v1 → v2 (published side-by-side, per protocol)

Benchmark v1 was terminated at 14/50 cases by protocol decision (1 hit,
5 genuine misses, 5 errors, 3 administrative exclusions). Its misses showed
two structural defects with perfect consistency: over-strict ChEMBL-only
pools, and genetic-association-only target selection. v2 replaced pool
strictness with multi-source union candidate generation (Amendment 3 item 16:
tiered ChEMBL pools were *superseded, not silently dropped*) and added
pathway-aware target augmentation. On the primary funnel-feasible subset the
rediscovery rate moved from v1's 1/11 scorable (9.1%) to 6/22 (27.3%) — with
different denominators, explicitly *not* a paired comparison.

### 3.6 Pre-freeze source-ablation control (52 arms, disclosed development corpus)

13 development cases × 4 source conditions, completed before the freeze tag:

| Condition | Generated + mechanistically valid |
|---|---|
| ChEMBL only | 5/13 |
| ChEMBL + GtoPdb | 8/13 |
| ChEMBL + DrugCentral | 10/13 |
| all three | 10/13 |

Union lanes rescued candidates ChEMBL alone cannot see; GtoPdb and
DrugCentral each added distinct recoveries. This control fixed the v2
production source set (all three) *before* the benchmark ran and is never
used to tune scoring.

### 3.7 Five-miss engineering acceptance (development instrument)

The five archived v1 genuine misses passed the pre-registered acceptance on
2026-08-05: **5/5 generated, 5/5 mechanistically valid** through production
code paths with full holdout; **Top-10 0/5** (reported separately, never
tuned to 5/5). Labeled `engineering_acceptance`; it demonstrates the
diagnosed gaps were repaired, not general accuracy.

## 4. Audit claim-set v1 — external audit validation

### 4.1 Design (frozen 2026-08-10)

100 claims with external, checkable, pre-cutoff ground truth: 60 defect
claims (30 from classes with existing fixes E1–E4 analogues; 30 from four
novel pre-registered classes N1–N4 with no prior fix, test, or trap) and 40
clean controls. Construction protocol, scope settlement, and all amendments
are committed; detectors were developed against synthetic fixtures only, and
real claim instances were sealed before any scored run. One scored run;
health gates before execution and before scoring; citation revalidation at
score time; exact Clopper–Pearson one-sided 95% bounds; INVALID-DATA if
abstention exceeded 10% of a group (actual: zero abstentions, zero exclusions).

### 4.2 Result: FAIL — reported as measured

| Metric | Result | PASS threshold | Met? |
|---|---|---|---|
| Defect recall | 32/60 = 0.533 (CP lower 0.420) | ≥ 0.80, lower ≥ 0.65 | **No** |
| Control false-flag rate | 7/40 = 0.175 (CP upper 0.304) | ≤ 0.15, upper ≤ 0.30 | **No** |
| Novel-class recall | 29/30 = 0.967 (CP lower 0.851) | none (registered) | — |

**The audit layer fails its first external validation.** Per class:

| Class | Caught | Interpretation |
|---|---|---|
| E1 safety withdrawal | 2/2 | works |
| E2 boxed-warning-not-withdrawal | **0/19** | **stale persisted pools**: the archived candidate pools predate the black-box/withdrawal classifier fix, so all 19 marketed boxed-warning drugs carried a wrong "WITHDRAWN FROM MARKET (web search)" badge and safety cap. The audit correctly surfaces what the (stale) pipeline shows — the defect is pool staleness, a data-refresh failure with a disclosure consequence |
| E3 direction incompatible | 1/1 | works |
| E4 unresolved-name honesty | **0/8** | construction assumption falsified: ChEMBL's synonym index resolved all 8 brand names (e.g. Zyvox→CHEMBL126), so `unresolved` never occurs. A claim-design lesson (verify non-resolution at construction), not a product defect |
| N1 combination splitting | 8/8 | generalizes |
| N2 biologic mis-scope | 13/13 | generalizes |
| N3 preclinical-only | **0 claims** | all 7 externally verifiable candidates failed construction verification (registered limitation; N3 retains synthetic coverage only) |
| N4 dose/route | 8/9 | generalizes (1 miss: oxymetazoline oral-claim produced no N4 finding) |

Control false-flags (7/40): five are the **N3 "preclinical-only" finding
firing on approved drugs** (all pool-context controls — the bounded
literature window is dominated by preclinical assertions even for approved
drugs; a real detector precision bug), and two are N1 multi-ingredient flags
on single-ingredient labels (amyl nitrite, perphenazine — label-parse edge
cases). Zero caught defects carried a contradicted safety disclosure.

The study also exercised the sealed-harness controls for real: the first
scored run crashed after archiving all raw outputs but before any metric was
computed; the pre-registered allowance was exercised exactly once, the re-run
scored the original archive without re-executing audits, and the allowance is
now permanently consumed with the results hash bound in the freeze manifest.

### 4.3 What the FAIL does and does not mean

The novel-class result (29/30) shows class-level defenses generalize to real
instances for N1, N2, N4. The FAIL is concentrated in (a) a **stale-data
disclosure defect** (E2) that the audit faithfully surfaced rather than hid —
arguably the study working as designed, and the motivation for a pool-refresh
program; (b) a **construction assumption** (E4); (c) an **N3 precision bug**
inflating the control false-flag rate. The correct next actions are a pool
staleness/refresh mechanism and N3 precision work — *after* this report is
frozen, as new studies with their own pre-registrations.

## 5. Engineering regression: audit trap benchmark

12/12 planted traps caught (threshold ≥ 0.9), 1/4 controls false-flagged
(0.25, exactly at the pre-registered ≤ 0.25 boundary), precision 0.923 —
**PASS**. This instrument proves the twelve previously-fixed defect classes
still trip the audit (safety-cap disclosure, black-box≠withdrawal, direction
incompatibility, label-artifact screen, confirmation discipline,
unresolvable-name honesty, salt-form dedup, degraded-source honesty,
unobserved≠zero, XLogP-unresolved disclosure, degraded-200 pool poisoning,
holdout name leakage). It is engineering acceptance only and is never
reported as external validation — the claim-set study (§4) exists precisely
because passing classes you already fixed proves nothing new.

## 6. Earlier development instruments (clearly labeled, not pooled)

- **Reviewer pilot** (compact retrospective rediscovery, present-day data,
  bioactivity pool intentionally unredacted, subset not pre-registered):
  1/9 recovered (11.1%), correct mechanism target first 4/9, true target
  considered 8/9, 0 pipeline errors.
- **repoDB development suites** (2026-07-29, pre-v2 pipeline): combined n=13
  → 1 hit (8%); small-molecule subset 1/13 (7.7%); these suites include
  biologics structurally outside the small-molecule pool and were the
  development corpus that *motivated* the v2 redesign. Retrospective
  rediscovery on the same suites under the v1-era taxonomy: top-3 biologic-
  leaning 0/9, small-molecule top-1 1/13.
- **First small retrospective** (2026-07-27, n=7): 1/7 top-10.

These numbers are the development history; the headline discovery number is
§3.2 only.

## 7. LLM use inside the system (full disclosure)

AgentBio uses LLMs at bounded, enumerated touchpoints; **no LLM assigns a
candidate score freely**, but four classifiers can affect scoring inputs:

| Touchpoint | Model | Can affect score/rank? |
|---|---|---|
| Mechanism-direction check (`data_sources/mechanism_direction.py`) | gpt-5.4 | **Yes** — INCOMPATIBLE caps the composite at 0.400 |
| Safety web-check step 2 (withdrawal / black-box classification; `safety_check.py`) | claude-sonnet-4-6 + web search | **Yes** — confirmed withdrawal applies the hard safety cap; black-box is advisory-only |
| Clinical-trial stop-reason classification (`clinicaltrials.py`) | claude-haiku-4-5 | **Yes** — decides whether a stopped trial counts as efficacy failure |
| PubMed relationship extraction (`pubmed.py`) | claude-sonnet-4-6 | **Indirectly** — YES/NO gates literature-assertion inclusion |
| Biologist druggability screening (`biologist.py`) | claude-sonnet-4-6 | Indirect — selects supporting PMIDs for the druggability signal |
| Chemist rationale, top-5 narration, audit narration | claude-sonnet-4-6 / claude-opus-4-5 | **No** — constrained restatement of precomputed facts |

The benchmark and audit harnesses run with `narrate=False` — zero narration
LLM calls in any scored path. Deterministic gates holdout-redact LLM query
construction during retrospective evaluation. LLM-judgment components are
exactly the components the audit layer exists to police, and the E2 failure
above shows why that policing must include *data freshness*, not just logic.

## 8. Limitations (complete list)

1. **Retrospective rediscovery, not prospective validation.** Known answers;
  disease-side identity redacted, but the bioactivity pool is not (and cannot
  fully be) redacted — reported per the holdout memo.
2. **Funnel-feasibility selection.** The screened subset underrepresents
  sparse-data, genuinely neglected diseases; the 64% screen pass rate is part
  of the headline, and 10/32 screened cases were still out of scope at
  runtime (screen/runtime scope disagreement is itself a finding).
3. **Chance baseline saturation** (§3.4).
4. **Prevalence-stratified failure** (§3.2): 0/12 on rare/less-rare strata.
5. **Audit FAIL** (§4): defect recall 0.533 and control false-flag 0.175 both
  miss PASS thresholds; N3 untested (zero claims); E4 tested a falsified
  construction assumption.
6. **Stale persisted pools** can carry pre-fix safety labels; E2 quantifies
  the consequence (19/19 affected in that class).
7. **Small samples**: 22 in-scope primary cases; CIs are wide (±~20 points).
8. **Single disease universe** (Orphanet rare + 20 WHO NTDs); oncology-common
  and non-rare diseases are out of scope by design.
9. **Self-evaluation**: the author develops the evaluated system. Mitigations:
  pre-registration before code, frozen claim sets, mechanical scoring,
  external ground truth, one-scored-run rules, and committed raw archives.
10. **Source freshness**: DrugCentral pinned to the official 2023 dump
  (sha256-pinned local snapshot after a 25+ h upstream outage; access-mode
  change registered as Amendment 6); GtoPdb commercial access terms are a
  go-live dependency for production use.
11. **No clinical, experimental, or prospective validation is claimed or
  implied anywhere in this package.**

## 9. Conflicts, funding, ethics

The author develops AgentBio (self-evaluation — see Limitation 9). No
external funding. No human or animal subjects; all data are public databases
and published literature. AI assistance was used in software development and
manuscript preparation, and LLMs are components of the evaluated system (§7);
all benchmark and audit scoring paths are deterministic.

## 10. Reproducibility

- Code: this repository (see `publication/README.md` for the release
  checklist; the release decision is the author's).
- Frozen artifacts (all committed): benchmark results v2 + case list +
  attrition + all pre-registrations and amendments; audit claim set (sha256
  `32efd7d9…`), construction log, freeze manifest, raw outputs, results;
  trap results; ablation results; engineering-acceptance artifacts;
  DrugCentral snapshot (sha256-pinned) + build provenance.
- Recompute every number in this report: `python3 publication/make_figures.py`
  (also re-verifies the audit metrics from the raw archive).
- Re-verify the frozen audit run: `python3 -m validation.run_audit_claimset
  --label audit_claimset_v1 --recalc-only`.
- Source versions: ChEMBL (live API at freeze; cache preserved), Open Targets
  (live at freeze), Orphanet (live at freeze), DrugCentral 2023 official dump
  (pinned snapshot), GtoPdb versioned downloads, Europe PMC/PubTator3 (bounded
  queries, archived responses), ClinicalTrials.gov API v2, openFDA/DailyMed
  (label assertions with revision dates).

*Report prepared 2026-08-10 from frozen artifacts only. No analysis in this
report was added after seeing any reviewer preference.*
