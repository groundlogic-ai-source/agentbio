# Pre-registration — Radical / multi-part hypothesis support (v1)

**Status: FROZEN before implementation.** Thresholds below were fixed by
inspecting only the *label distribution* of the dataset (counts of
repurposed-success / genuine-failure / administrative-exclude), never any
feature–outcome association. No hypothesis had been tested with the new ops at
the time of writing.

Date frozen: 2026-08-04

---

## 1. Motivation

The feature DSL could express only two hypothesis shapes:

* a single predictor (`X predicts success`), and
* a two-way interaction (`the effect of X differs across binary Y`).

Hypotheses of the form the user actually wants —

> "X has lower repurposing success under Y, when Z is happening, but not when
> F is happening"

— could not be encoded, so the generators never proposed them and the lead
reviewer had no op to rescue them into. This is a **vocabulary** limit, not a
statistical one, and it is what this change removes.

## 2. The binding constraint is data, not vocabulary

Label counts in the built dataset (`enriched_dataset.csv`):

| label | n |
|---|---|
| repurposed-success | 5582 |
| administrative-exclude | 2715 |
| original-approval | 646 |
| **genuine-failure** | **114** |

Per split and framing, the minority class governs power:

| framing | split | positives | negatives | minority |
|---|---|---|---|---|
| narrow | discovery | 2862 | 51 | **51** |
| narrow | confirmation | 2720 | 63 | **63** |
| broad | discovery | 2862 | 1341 | 1341 |
| broad | confirmation | 2720 | 1488 | 1488 |

The trustworthy framing (narrow — genuine efficacy failures only) has **51
negatives in the discovery half**. Under the standard 10-events-per-parameter
rule this supports at most 5 model parameters.

Consequence, stated plainly and in advance: **a saturated three-way interaction
(8 parameters) is NOT powered in the narrow framing and will be refused there.**
It is powered only in the broad framing, which is the framing already known to
manufacture label artifacts (administrative-exclude rows outnumber genuine
failures ~26:1). Any three-way effect that appears broad-only is therefore
presumed an artifact and is already screened by the existing admin-artifact
replay + `LABEL_ARTIFACT_SUSPECT` gate. That gate is unchanged by this work.

This is why two routes are added, not one.

## 3. Route A — Boolean composition (powered in the narrow framing)

New ops, all producing a single 0/1 column:

```
{"op": "all_of", "params": {"terms": [<binary op>, ...]}}   logical AND
{"op": "any_of", "params": {"terms": [<binary op>, ...]}}   logical OR
{"op": "not_op", "params": {"term": <binary op>}}           logical NOT
```

These encode a *conditional subgroup* — "oncology AND lipophilic AND NOT oral"
— as one binary predictor, tested with **Fisher's exact test on a 2×2 table
(1 predictor parameter)**. This is the route by which a multi-part conditional
claim becomes testable on 51 negatives: the complexity moves into the feature
definition instead of into the model's parameter count.

Fisher's exact is exact, so small cells produce conservative (large) p-values
rather than false positives; no asymptotic approximation is relied upon.

**Pre-registered limits (fixed now):**

* `MAX_COMPOSITION_TERMS = 4` — at most 4 terms in one `all_of` / `any_of`.
* `MAX_COMPOSITION_DEPTH = 3` — nesting depth cap; prevents unbounded
  auto-generated feature trees.
* `MIN_COMPOSITE_TRUE_N = 10` — the TRUE cell of the composed feature must
  contain at least 10 rows in the framed subset. Below this the subgroup odds
  ratio is too unstable to interpret even when Fisher's p is valid.
* Existing `separation_ok()` (rejects any empty 2×2 cell) continues to apply.
* Every leaf term must itself be a supported binary op; the label-confounded
  and indication-stage-proxy hard guardrails recurse into every term, so a
  composed feature cannot smuggle in `prior_repurposing_count` or a
  refractory/relapsed stage keyword.

## 4. Route B — Three-way conditional interaction

```
{"op": "interaction3",
 "params": {"base": <op>, "moderator": <binary op>, "moderator2": <binary op>}}
```

Fits `y ~ b + m1 + m2 + b:m1 + b:m2 + m1:m2 + b:m1:m2` and reports the OR / CI
/ p of the **three-way term** `b:m1:m2`. That term is exactly the claim "the
moderation of X by Y is itself different depending on Z" — i.e. "…when Z, but
not when F", where F is the complement of Z.

**Pre-registered power guards (fixed now, applied before the fit):**

* `n_params = 8`; require `min(n_success, n_failure) >= 10 * 8 = 80` in the
  framed subset (10 events per parameter).
* Each of the 4 `(m1, m2)` strata must have `>= 30` rows, both outcome classes
  present, and a varying base.
* Both moderators must vary, the base must vary, and the two moderators must
  not be the identical spec (non-identifiable).
* Non-finite OR/CI/p after fitting (singular or non-converged) → recorded as
  not tested, never logged to the FDR family. This matches the existing
  two-way behavior.

**Expected outcome, recorded in advance:** these guards will refuse
`interaction3` in the narrow framing on the current dataset (51 < 80). This is
the intended, honest behavior — the module reports that it *cannot* test the
claim rather than emitting an underpowered p-value.

## 5. Retro-fit — two-way interactions gain the same EPP guard

The existing `interaction` op had **no** minimum-n or power guard of any kind;
it could fit a 4-parameter logistic model on arbitrarily few events. It now
requires `min(n_success, n_failure) >= 10 * 4 = 40`, consistent with the rule
above. The narrow discovery half (51) still satisfies this, so no currently
supported analysis is lost.

## 6. Multiplicity

Unchanged in principle: every test — including every composed and three-way
test, in both framings — enters the single cumulative Benjamini-Hochberg family
over `hypothesis_log`, across all runs ever. Richer hypothesis shapes therefore
do not get a multiplicity discount; proposing more elaborate features raises the
bar for everything.

## 7. What this change does NOT do

* It does not weaken the label-artifact screen, the direction pre-registration,
  the mechanistic-justification requirement, or the dedup rule.
* It does not make broad-only findings trustworthy.
* It does not create statistical power that the 114 genuine failures do not
  contain. Radical hypothesis *shapes* cannot substitute for outcome data.
