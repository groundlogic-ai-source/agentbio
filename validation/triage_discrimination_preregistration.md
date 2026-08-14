# Pre-registration: Triage Discrimination Study (Study A + negative controls)

Date: 2026-08-11. Written before any case was executed against live sources.
Bound by freeze manifest `validation/triage_discrimination_freeze_manifest.json`.

## Claim boundary (read first)

This study measures **reliance-safety** of the audit/triage evidence layer:

1. Can the layer resolve and say something about real, confirmed repurposing
   drugs? (resolution/eligibility)
2. Does it spuriously raise disqualifying findings on them under default
   operation? (guard-rail)
3. Does it detect planted, mechanically false assertions? (detection)

It does **not** measure hypothesis discrimination — whether the layer can tell
a good drug–disease hypothesis from a plausible-but-unapproved one. The
disease-dependent dimensions (rank, mechanism direction, safety caps) are
computed from a pool that is **not disease-blind** (agents/reviewer.py derives
composite/rank from disease-linked OpenTargets and trial data;
`check_mechanism_direction` takes the disease name). Those dimensions are
reported descriptively in Study B (12 pipeline re-runs) and never enter the
scored claims below.

## Design

- **Unit of analysis: the distinct drug.** Disease-independent dimensions are
  per-drug; pairs sharing a drug are pseudoreplicates and are never counted
  as independent observations.
- **Case set:** `validation/triage_discrimination_cases.json`
  (contract `triage-discrimination-cases-v1`, seed 20260811, builder is
  deterministic and offline). cohort_a n=200 confirmed repurposing drugs,
  dev-suite drugs excluded at drug level (E3); nc1 n=15 biologics with planted
  `claimed_modality="small molecule"`; nc2 n=15 non-oral drugs with planted
  `claimed_route="oral"`.
- **Instrument under test:** the shipped code path — `build_audit_context`
  (with holdout redaction active, verified per case) + `detect_audit_findings`
  + `validation/evidence_profile.build_profile` (rule fingerprint
  `cf9bb3b9…f40d9f`). The pool-free `no_case` path is used deliberately:
  cohort A tests the disease-independent dimensions, which need no pool.
  Mechanism entity for the N3 literature lane is resolved drug-side via ChEMBL
  (`get_drug_mechanism_targets_for_audit`), exactly the disease-blind lookup
  the shipped absent-path uses.
- **Blindness enforcement:** every case runs inside
  `holdout.holdout_active([drug])`; the runner aborts if any returned context
  lacks `holdout_redaction.applied == True`.

## Pre-registered endpoints and thresholds

Scored exactly once on the eligible subsets defined below. Wilson score 95%
intervals throughout.

**E1 — Resolution rate (cohort A).** Fraction of the 200 drugs whose openFDA
label lane returns status `ok` (not unavailable/parse_failed/degraded).
Report with CI. No pass threshold — this is a capability measurement a partner
needs verbatim. (Pre-registered expectation, not a gate: ≥70%.)

**E2 — Guard-rail spurious-disqualification rate (cohort A, eligible subset).**
Fraction of E1-eligible drugs whose `primary_disposition == DISQUALIFIED`.
PASS if the Wilson 95% upper bound ≤ 5%. Disclosed limitation: under claim-free
operation the only disease-independent hard disqualifier that can fire is
PRECLINICAL_ONLY, and the marketed-label guard makes that near-impossible for
label-resolved approved drugs — so E2 is expected to be near-zero **by
construction**. It is reported as a guard-rail regression check, not as
evidence of discrimination.

**E3 — Soft-caution rate (cohort A, eligible subset).** Fraction with
`primary_disposition == QUALIFIED`. Descriptive; reported per dimension. The
known direction of the XLogP signal (confirmed positive repurposing signal,
currently surfaced as caution-only) is disclosed.

**E4 — NC1 detection rate.** A control is *valid* if its label lane resolved
AND at least one cutoff-eligible product has `product_modality` in
{biologic, vaccine}. Detection = N2 `flagged` among valid controls.
PASS if ≥ 12/15 (80%) of valid controls are detected.

**E5 — NC2 detection rate.** A control is *valid* if its label lane resolved
AND its approved routes are non-empty AND exclude oral. Detection = N4
`flagged` among valid controls. PASS if ≥ 12/15 (80%) of valid controls are
detected. Controls that turn out to have an oral label route are mislabeled
ground truth — disclosed and excluded from the denominator.

## Operational rules

- **One scored run.** Results are written once, fail-closed: if a scored
  results file with a results hash exists, the runner refuses to re-score.
  Interrupted runs resume from the per-case checkpoint (append-only JSONL).
- **Health gate.** Before any case runs: ChEMBL `status.json`, openFDA
  `label.json`, and PubTator must all respond healthy. Degraded source =
  abort, never score. (Convention from prior outage poisoning.)
- **Per-case blindness check.** Every case asserts redaction was applied; a
  single unredacted context aborts the run.
- **Cache hygiene.** Transient source failures are never cached; the 30-day
  label cache is shared with production, which is why redaction runs
  post-cache at the lane boundary.
- **Reporting contract.** Every case where a confirmed repurposing was
  disqualified is named in the report. If any endpoint fails, the report says
  so in plain language. Non-confirmed top candidates (Study B) are never
  scored as errors: absence of approval is not evidence of a wrong hypothesis.

## Study B (descriptive, separate freeze)

The 12 benchmark diseases' pools are rebuilt via approved pipeline re-runs
(LLM cost pre-approved by the user 2026-08-11). Confirmed repurposings for
those diseases are audited against the rebuilt pools (full configuration, all
dimensions live). Reported descriptively with n disclosed, explicitly labelled
as computed on non-disease-blind pools. No threshold, no pass/fail.

### Amendment (2026-08-14): trial-evidence coverage gate

**What happened.** ClinicalTrials.gov rate-limited the pool builds
(thousands of 429s; the client had no backoff). A failed trial query is
handled by dropping the trial term from that candidate's composite as a
coverage gap — correct per-candidate behaviour, but it means a pool builds
to completion while an arbitrary subset of its candidates is scored on
thinner evidence than the rest. Measured coverage of the five pools built
before the gate existed:

| Disease | Trial evidence observed |
|---|---|
| Acute Promyelocytic Leukemia | 100.0% |
| Aspergillosis | 95.5% |
| Brucellosis | **32.1%** |
| Dermatomyositis | **25.7%** |
| Gaucher Disease | 100.0% |

**Decision.** The Brucellosis and Dermatomyositis pool records were
discarded and are rebuilt from scratch. Their per-target biologist/chemist
records were retained: those are upstream of the reviewer and unaffected by
trial-source availability. No results file existed at any point, so this
is a checkpoint invalidation, not an edit to a scored artifact.

**Prevention.** `_trial_coverage()` now gates pool finalization at
`TRIAL_COVERAGE_MIN` (default 0.95, env-overridable). A pool below the
threshold is refused and left for resume rather than checkpointed, and
every finalized pool records its `trial_evidence_coverage`. The
ClinicalTrials client additionally throttles requests process-wide and
honours `Retry-After` with exponential backoff.

**Disclosure.** Aspergillosis (95.5%, 217 gapped candidates) passes the
threshold and is retained; its residual gap is disclosed here and travels
with the results as a per-pool coverage figure.
