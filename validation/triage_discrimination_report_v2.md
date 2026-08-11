# Triage Discrimination Study — Study A Report (v2, result of record)

Frozen results: `validation/triage_discrimination_results_v2.json`.
Freeze manifest: `validation/triage_discrimination_freeze_manifest_v2.json`
(commit `6c11982`, rule fingerprint `c600f834…d260bf`, case-set hash
`d0807a0c…e8ac98`). Pre-registration:
`validation/triage_discrimination_preregistration.md`. Scored exactly once,
health-gated, every case blindness-asserted.

**v2 supersedes v1** (`triage_discrimination_results.json`, kept frozen and
unedited): v1's cohort admitted ~10% original-approval drugs (status=Approved
without the `repurposed-success` label), its control validity counted
label-ineligible products, and its scoring surface dropped N4 `flagged`.
All three were caught by code review / the negative controls and fixed under
Amendments 1–2. v1's directional findings hold under the corrected design.

## Claim boundary (unchanged)

This study measures **reliance-safety** of the audit layer on confirmed
repurposings — resolution, spurious-disqualification guard-rail, and planted
false-assertion detection. It does **not** measure hypothesis discrimination;
the disease-dependent dimensions needed for that come from pools that are not
disease-blind and are reported descriptively in Study B only.

## Endpoints (pre-registered; Wilson 95%; unit = distinct drug; cohort =
200 confirmed repurposing drugs, `label == repurposed-success`, dev-suite
excluded at drug level)

| # | Endpoint | Result | Verdict |
|---|----------|--------|---------|
| E1 | Resolution rate (label lane `ok`) | 161/200 = **80.5%** [74.5%, 85.4%] | reported (expectation ≥70% met) |
| E2 | Guard-rail spurious disqualification (eligible) | **0/161**, upper bound **2.3%** ≤ 5% | **PASS** |
| E3 | Soft-caution rate (eligible) | 0/161 [0%, 2.3%] | reported |
| E4 | NC1 modality-contradiction detection (valid) | **11/11 = 100%**, lower 74.1% | **PASS** (≥80% of valid) |
| E5 | NC2 route-contradiction detection (valid) | **9/9 = 100%**, lower 70.1% | **PASS** (≥80% of valid) |

- **E2 near-vacuity, disclosed (unchanged from pre-registration):** under
  claim-free operation the only disease-independent hard disqualifier that can
  fire is PRECLINICAL_ONLY, and the marketed-label guard makes that
  near-impossible for label-resolved approved drugs. E2 is a guard-rail
  regression check, not discrimination evidence. No confirmed repurposing was
  disqualified — there are none to name.
- **E1 = 80.5%** is the partner-relevant capability number. The 39 unresolved
  are drugs without a cutoff-eligible US openFDA label (`label_status=empty`
  or unresolved) — a source-coverage limit, not a judgment. Non-US-approved
  and older/withdrawn-label drugs dominate the residue.
- **E3 = 0** is a property of the claim-free, pool-free configuration
  (lipophilicity and route-claim surfaces need a pool or a claim), not a claim
  about the full product.
- **Control attrition (pre-registered handling):** NC1 4/15 invalid (2 labels
  unresolved, 2 labels did not confirm biologic modality); NC2 6/15 invalid
  (label unresolved or routes did not exclude oral). Detection is computed on
  valid controls only.

## Amendment log

- **Amendment 1 (during v1 analysis):** N4 `flagged` fell through to CLEAR in
  the profile surface — the detectors fired on all valid NC2 controls but the
  study surface showed 0. Fixed (`route_feasibility=FLAGGED` + soft caution);
  fingerprint `cf9bb3b9…` → `c600f834…`; unreachable under claim-free cohort A.
  Runner NC pass booleans corrected to the pre-registered ≥80%-of-valid rate.
- **Amendment 2 (post-review, pre-v2):** cohort corrected to
  `repurposed-success`; control validity restricted to citation-eligible
  products (matching the detector surface); N2 `review` and N1 now map
  explicitly (descriptive-only, scoring rule unchanged); checkpoints
  hash-bound to the freeze; builder fail-closed after scoring; Study B
  rebuilt on production pooling semantics with per-target checkpoints.

## Plain-language bottom line

On 200 real confirmed repurposing drugs, the audit layer resolved usable
label evidence for 4 in 5, never disqualified one (95% upper bound 2.3%), and
detected every valid planted false assertion (20/20 across modality and route
classes). Its own scoring surface had two defects, both caught by the
study's controls and review, both fixed and re-verified. Whether the layer
can rank a good hypothesis above a plausible unapproved one is **not**
established here; Study B's descriptive table is the only evidence on that
and is underpowered by construction.
