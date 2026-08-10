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

## Amendment 1 (2026-08-10, before any claim construction): citation-validity rule

"External ground truth that predates the study" is defined by a MECHANICAL
rule so that no judgment call about citation validity is ever made while the
claim set is being built:

1. **Cutoff date.** The study cutoff is **2026-08-10** (this amendment's
   commit date). A citation is valid only if the cited artifact's own date —
   publication date, label revision date, or database release date — is
   strictly earlier than the cutoff. "Before the study" means the artifact's
   date, NEVER the date we happened to retrieve it.
2. **Pinned releases.** Database citations must name a specific versioned
   release with a verifiable release date before the cutoff (e.g. "ChEMBL 35,
   released 2024-12"; "DrugCentral 2023 snapshot"; "FDA label, label-history
   revision date YYYY-MM-DD"; "Orphanet 2025 release"). A bare "current
   database" reference is invalid.
3. **Retrieval date is not validity.** Every citation records its retrieval
   date for reproducibility, but retrieval date never counts toward the
   cutoff rule.
4. **Unverifiable dates exclude the claim.** If a citation's date cannot be
   verified, the claim is dropped during construction and recorded in the
   construction log — never silently repaired or swapped for a convenient
   substitute after seeing pipeline behavior.
5. Any claim whose citation fails this rule at audit time is excluded and
   reported as a construction defect, not scored as a miss.

## Amendment 2 (2026-08-10, before detector implementation or claim construction):
## held-out instances of pre-registered novel classes

The original freeze correctly states that N1–N4 had no prior fix, test, or
trap in the codebase on 2026-08-10.  It did not state whether general defenses
could be implemented before the real claim instances were constructed.  This
amendment resolves that ambiguity before either event:

1. **What remains novel.** N1–N4 remain novel *classes at the original
   pre-registration snapshot*.  The study will not describe their eventual
   recall as spontaneous discovery of wholly unforeseen defect classes.
2. **Development is class-level only.** General N1–N4 detectors may be
   implemented against synthetic and non-scored development fixtures.  Those
   fixtures may encode the class definitions above, but must not contain a
   future scored claim's drug, disease, product, citation, source-record ID, or
   expected result.
3. **Real instances stay sealed.** The 30 real N1–N4 claims are constructed
   only after detector and source scope is settled.  Their identities,
   citations, labels, expected flags, and ground-truth fields cannot be read by
   detector development, tests, prompts, source queries, or the scored runtime.
4. **No tuning on the claim set.** A detector, threshold, source configuration,
   or fixture may not be changed after inspecting a scored claim or its output.
   The one-fix-one-rerun rule remains limited to a demonstrated harness defect;
   a detector miss is a result, not a harness defect.
5. **Reporting language.** `novel_recall` is reported as **held-out
   generalization to real instances from four pre-registered classes**.  It
   retains no PASS threshold and cannot be used alone to declare the audit
   benchmark PASS.
6. **Scope-settlement gate.** Before claim construction, a committed
   scope-settlement record must name every admitted source and detector and
   every deferred lane.  Claim construction begins only after that record;
   later source or detector additions require a new future study version.

This design tests whether class-level defenses generalize beyond their
development fixtures while preserving an honest distinction between an
unforeseen class and an unseen instance.

## Freeze record (2026-08-10)

- Claim set: `validation/audit_claim_set_v1.json` — 100 claims
  (existing_fix 30 [E1=2, E2=19, E3=1, E4=8], novel 30 [N1=8, N2=13,
  N3=0, N4=9], control 40 [32 pool-free + 8 pool-context]).
  File sha256: `32efd7d965f62e2cd0900578e6ab2f78b0585d65f33095a651faf6be796523c4`.
- N3 closed at zero claims: all seven externally verifiable candidates
  failed construction verification (no cutoff-eligible primary paper, or
  clinical-trial evidence found). Shortfall reallocated per the fixed order
  N1→N4→N2 (construction protocol §2). The N3 detector is therefore NOT
  tested by this study; it retains synthetic unit coverage only. This is a
  registered limitation, not a post-hoc change.
- Freeze manifest: `validation/audit_claimset_freeze_manifest.json`
  (code commit 6ed2e4e0, harness config, cache policy, health
  requirements). Construction log: `validation/audit_claimset_construction_log.md`.
- Exactly one scored run follows this freeze. No detector, threshold,
  claim, or source configuration changes after results are seen.

## Amendment 3 (2026-08-10): one-fix-one-rerun allowance exercised

The first scored run executed all 100 audits under the frozen code and
archived every raw output, then crashed in harness plumbing BEFORE any metric
was computed or seen (KeyError between the in-memory run dict and the on-disk
archive envelope in `score_from_archive`). No results artifact exists from
that attempt. Per the pre-registered one-fix-one-rerun allowance for a proven
harness defect: the plumbing was fixed (metrics now load strictly from the
on-disk archive; crash recovery scores the existing archive rather than
re-measuring), regression tests were added, and the freeze manifest's code
commit was advanced to f41d7661. The re-run scores the ORIGINAL archived
outputs — the audits were not re-executed. No claim, detector, threshold, or
source configuration was touched.
