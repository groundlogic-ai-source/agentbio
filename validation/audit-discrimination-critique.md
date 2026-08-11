# Self-critique of the triage discrimination benchmark (2026-08-11)

Request: "attack your own plan for a minute, if you see holes (are we testing the
right thing and is the methodology sound), fix anything that needs to be fixed."

This file records the attack, what survived, and what changed. Each claim was
verified against the code before acting on it.

## Verdict
The study's *purpose* is right and worth doing. The *methodology* as originally
planned had one fatal flaw (a circularity that would have invalidated the
headline output) and two structural weaknesses. All three are fixed in the
revised plan (`.local/tasks/triage-discrimination-benchmark-v3.md`).

## Hole 1 — FATAL: the pool is not disease-blind, so the head-to-head is circular

The study's headline was a "head-to-head": for each disease, compare the
confirmed repurposing against the pipeline's top-ranked candidates. That
comparison leans on the candidate's **rank** and the **mechanism/safety caps**.

Trace (explore subagent, confirmed by direct read):
- `api/audit.py` `run_audit()` is itself disease-blind beyond job/pool lookup —
  it never re-runs discovery. Good.
- **But the persisted pool it reads is not.** `agents/reviewer.py`:
  - `ot_association_score` enters the composite (`:466-495`) and is disease-linked.
  - trial evidence is queried *with the disease* (`:444-458`).
  - `check_mechanism_direction(..., disease, ...)` (`:855-864`) takes the disease
    name; its cache key includes it (`data_sources/mechanism_direction.py:153-157`).
  - rank is a post-hoc sort over those composites (`_sort_reviewed`, `:699-700`).
- The redaction added in Step 1 (`data_sources/audit_redaction.py`, applied in
  `api/audit_context.py:307`) covers **only the label and literature lanes** that
  feed N1–N4. It does **not** touch `rank`, `composite_score`,
  `safety_cap_applied`, `mechanism_cap_applied`, or `black_box_advisory` — exactly
  the fields `api/triage.py:_verdict` (`:114-125`) and
  `validation/evidence_profile.py` read for the safety/mechanism/rank dimensions.

Consequence: a head-to-head on rank measures the very disease-side signal the
study claims to hold out. That is circular and would have produced a confident,
wrong, headline number.

**Fix:** the head-to-head is dropped as a scored output. The disease-dependent
dimensions (rank, mechanism, safety) are quarantined into a clearly-labelled
*descriptive* table, never part of the pre-registered claim.

## Hole 2 — underpowered and non-generalizable

Only 6 of 22 primary benchmark cases had the confirmed drug actually in the
pool, across just 3 diseases. Wilson 95% on a zero-false-disqualification rate:
n=6 → upper bound 39% (uninformative). Even the full 22 → 15%. No defensible
claim was ever possible from the head-to-head, independent of the leak.

**Fix:** the *primary* metric becomes the false-disqualification rate of
confirmed repurposings on the **disease-independent** dimensions, which need no
pool and therefore no pipeline runs. repoDB `data_prep/output/enriched_dataset.csv`
holds 6,228 Approved pairs / 1,491 distinct drugs (most with PubChem XLogP and
ChEMBL molecule-type), so n is limited by selection, not data. Wilson upper bound
at n=60 ≈ 6%; at n=200 ≈ 1.8%.

## Hole 3 — "no judgment in the scored path" was overstated

`evidence_profile.py` claims the profile is fully mechanical. The label-lane
dimensions (modality/route N1–N4) are. But `mechanism_direction` and the
underlying `composite_score`/`rank` are LLM/model-derived upstream. Presenting
the whole profile as "no judgment" hides that.

**Fix:** disclose it. The primary metric uses only the genuinely mechanical,
disease-independent dimensions; the LLM-influenced dimensions are exactly the
ones quarantined into the descriptive table, which carries the caveat.

## What survives unchanged
- Step 1 holdout redaction (`7649f1e`) — still valid and necessary for the
  disease-independent dimensions. It just doesn't extend to the pool.
- Step 2 evidence profile (`cddabc8`) — sound as an instrument; needs one
  refactor to tag dimensions disease-independent vs disease-dependent.
- The 12 approved pipeline re-runs — still needed, but now to populate the
  *descriptive* disease-dependent table, not a scored head-to-head.
- The false-disqualification focus — now promoted to the primary claim, which
  is what a partner actually needs.

## Net effect
The study gets *more* credible and *cheaper* at once: the headline claim moves
onto dimensions that are provably blind and cost no LLM runs, and the expensive
runs are demoted to a clearly-caveated descriptive add-on.
