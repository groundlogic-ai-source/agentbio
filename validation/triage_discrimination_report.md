# Triage Discrimination Study — Study A Report

Frozen results: `validation/triage_discrimination_results.json`
(`results_sha256 a21632d1…`). Freeze manifest:
`validation/triage_discrimination_freeze_manifest.json` (commit `aa1c81d`,
rule fingerprint `cf9bb3b9…f40d9f`). Pre-registration:
`validation/triage_discrimination_preregistration.md`. Scored exactly once,
health-gated, every case blindness-asserted (redaction marker present).

## What this study does and does not claim

It measures **reliance-safety** of the audit layer: does it resolve real
confirmed repurposing drugs, does it avoid spuriously disqualifying them under
default operation, and does it catch planted mechanically-false assertions.
It does **not** measure hypothesis discrimination (telling a good hypothesis
from a plausible unapproved one) — the disease-dependent dimensions needed for
that come from pools that are not disease-blind, and are reported descriptively
in Study B only.

## Endpoints (pre-registered; Wilson 95% intervals; unit = distinct drug)

| # | Endpoint | Result | Verdict |
|---|----------|--------|---------|
| E1 | Resolution rate (label lane `ok`) | 148/200 = **74.0%** [67.5%, 79.6%] | reported (expectation ≥70% met) |
| E2 | Guard-rail spurious-disqualification (eligible) | **0/148**, upper bound **2.5%** ≤ 5% | **PASS** |
| E3 | Soft-caution rate (eligible) | 0/148 [0%, 2.5%] | reported |
| E4 | NC1 modality-contradiction detection (valid controls) | **11/11 = 100%**, lower bound 74.1% | **PASS** (≥80% of valid) |
| E5 | NC2 route-contradiction detection (valid controls) | **7/7 = 100%** (see Amendment 1) | **PASS** (≥80% of valid) |

- **E2 near-vacuity, disclosed:** under claim-free operation the only
  disease-independent hard disqualifier that can fire is PRECLINICAL_ONLY, and
  the marketed-label guard makes that near-impossible for label-resolved
  approved drugs. E2 is a guard-rail regression check, not discrimination
  evidence. No confirmed repurposing was disqualified — there are none to name.
- **E1 is the partner-relevant capability number:** the layer produced
  resolved label evidence for 74% of real confirmed repurposing drugs. The 52
  unresolved are predominantly drugs without a US openFDA label record
  (`label_status=empty`) — a coverage limit of the source, not a judgment.
- **E3 = 0:** under the pool-free claim-free configuration, no live
  soft-caution surface fired (lipophilicity and route claims require a pool
  or a claim). Reported honestly as a property of the configuration.

## Negative-control validity attrition (pre-registered handling)

- NC1: 4/15 invalid — 2 labels unresolved (Daclizumab, Lepirudin), 2 whose
  label did not confirm biologic modality under the detector's vocabulary
  (Bacitracin, Carfilzomib). Detection computed on the 11 valid.
- NC2: 8/15 invalid — label unresolved or label routes did not exclude oral.
  Detection computed on the 7 valid.

## Amendment 1 (2026-08-11) — scoring-surface defect found by the controls

The frozen run's NC2 endpoint initially read `0/7 detected`. Forensics on the
frozen records showed the audit **detector fired correctly on all 7** valid
controls (planted `oral` claim vs. labels listing only non-oral routes —
persisted `approved_routes` proves it), but `evidence_profile.py`'s
`route_feasibility` dimension had no mapping for N4 `flagged` and let it fall
through to `CLEAR`. The defect was in the study's scoring surface, not the
audit layer — and catching it is exactly what the negative controls are for.

Disposition, per frozen-study convention:
- `evidence_profile.py` fixed: N4 `flagged` → `route_feasibility=FLAGGED`,
  added to SOFT_CAUTIONS. Rule fingerprint advances
  `cf9bb3b9…f40d9f` → `c600f834…d260bf`; the pinned test and Study B freeze
  carry the new value.
- The change is **unreachable under Study A's claim-free cohort** (no claims →
  route findings cannot fire), so frozen E1–E3 are unaffected.
- E5 is re-derived from the frozen records' persisted deterministic detector
  inputs (`approved_routes` + planted claim), not by re-running any case.
  Results hash untouched.
- The runner's NC pass booleans were also miscomputed (absolute count ≥12
  instead of the pre-registered rate ≥80% of valid); the booleans in the
  frozen JSON are wrong, the counts are right, and this report's verdict
  column applies the pre-registered criterion to the frozen counts.

## Plain-language bottom line

Under default operation the audit layer resolved three quarters of real
confirmed repurposing drugs, never disqualified one (upper bound 2.5%), and
detected every valid planted false assertion (11/11 modality, 7/7 route) —
after the controls exposed and we fixed a dropout in the study's own scoring
surface. Whether the layer can rank a good hypothesis above a plausible
unapproved one is **not** established by this study; Study B's descriptive
table is the only evidence on that, and it is underpowered by construction.
