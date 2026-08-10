# Audit Claim-Set Benchmark v1 — Pre-registration (frozen 2026-08-10)

## Purpose

The audit trap benchmark (`audit_traps_preregistration.md`) is an **engineering
regression suite**: its traps encode failure classes AgentBio was already fixed
for, so it proves fixes still work — not that the audit layer catches failure
classes nobody anticipated. This study is the **external validation** of the
product's core claim — that AgentBio's audit catches the defects that make
naive repurposing pipelines untrustworthy — measured against a frozen claim set
with **independent ground truth**, including defect classes the team did NOT
pre-fix for.

This artifact must never be reported as discovery accuracy (that is benchmark
v2's role), and the two instruments' results must never be conflated.

## Non-circularity commitments

1. **Novel classes included.** At least 4 of the defect classes below (N1–N4)
   are classes with NO prior fix, test, or trap in the codebase as of this
   freeze. Passing the classes we already engineered for proves nothing new;
   the headline includes novel-class recall reported separately.
2. **Ground truth is external.** Every claim carries a citation to an
   authoritative source (FDA label/withdrawal record, ChEMBL mechanism
   action_type, DrugBank, Orphanet) that existed before this study and is
   checkable offline. No claim's ground truth is "what our pipeline says."
3. **Set frozen before scoring.** The claim set is committed as
   `validation/audit_claim_set_v1.json` with its sha256 recorded here; any
   post-first-scored-run change invalidates comparability (same rule as the
   trap benchmark).

## Claim set composition (target n = 100)

| Group | n | Ground truth | Required behavior |
|-------|---|--------------|-------------------|
| Defect claims — classes WITH existing fixes (T1–T12 analogues) | 30 | external citation | defect caught + disclosed |
| Defect claims — NOVEL classes N1–N4 (below) | 30 | external citation | defect caught + disclosed |
| Clean controls — claims with no defect | 40 | external citation | NOT flagged |

Novel classes (no existing defense as of freeze):
- **N1 combination-product splitting** — a fixed-dose combination attributed to
  a single active moiety's mechanism.
- **N2 biologic modality mis-scope** — an antibody/enzyme claim scored as if
  small-molecule assay evidence applied.
- **N3 species/preclinical-only mismatch** — a claim whose only supporting
  potency evidence is non-human or in-vitro-only, presented as clinical-grade.
- **N4 dose/route implausibility** — a claim requiring an exposure the approved
  route cannot achieve (e.g. topical-only drug for a CNS indication).

## Protocol

- **One scored run.** No re-rolls after seeing results. Harness defect → fix,
  record here, repeat exactly once (the one-fix-one-rerun rule).
- **Live-source gating.** Unlike the offline trap suite, claim resolution uses
  live providers; the run is gated on the same health probe as benchmark v2
  and refused/deferred during any source degradation. Degraded runs are never
  scored.
- **Blinding.** The frozen pipeline sees claims as ordinary triage/audit
  inputs; no claim text, defect label, or ground-truth field is visible to the
  scored code path (verified by the same holdout-audit guard style as v2).

## Metrics and pre-registered PASS thresholds

- `defect_recall = caught / 60` — PASS requires **≥ 0.80** with exact
  Clopper-Pearson lower 95% bound **≥ 0.65**.
- `novel_recall = caught_novel / 30` — reported with CI; no threshold (first
  measurement of an unknown quantity; setting a pass bar on unmeasured
  performance would be unfalsifiable).
- `control_false_flag_rate = flagged / 40` — PASS requires **≤ 0.15** with
  upper 95% bound ≤ 0.30.
- Overall verdict PASS requires both thresholded metrics. Per-class recall is
  a secondary breakdown; the headline is the pair (defect_recall, false-flag
  rate) with CIs.

Power note: n=60/40 gives CI half-widths of ≈±10 points at the registered
operating points — enough to separate "audit works" from "audit is noise,"
which is the adoption question this study answers.

## Phase 2 (optional, registered in advance): inter-rater arm

If an outside researcher engages (expected via outreach), they audit a random
50-claim subset unaided while blinded to AgentBio's output; we compare catch
rate and wall-clock time. Registered now so the comparison is not designed
around a known tool result. No collaborator, no phase 2 — the absence is
reported, not papered over.

## Relationship to existing artifacts

- Trap benchmark: internal regression suite (planted defects, stubbed inputs).
  Reported as engineering acceptance only.
- This study: external audit validation (real claims, live sources).
- Benchmark v2: discovery accuracy. Separate number, separate paper section.
