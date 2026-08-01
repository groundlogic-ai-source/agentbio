# F2 — Pharmacological-precedent calibration: written justification (pre-registration)

**Date:** 2026-07-31 · **Status:** APPROVED 2026-07-31 (as written) and IMPLEMENTED — umbrella demotion at `agents/target_selection.py` (`_tag_umbrella_precedent`), convergence cap at both ranking paths (`_apply_mechanistic_convergence_cap`), unit tests in `validation/test_f2_precedent.py`. Constants are now frozen: changes require a written amendment before any case-level inspection.

## 1. Discipline statement

1. The constants below are chosen on **principle** (an evidence-strength argument), not by re-running the 13-case retrospective suite or any other case-level evaluation under candidate settings.
2. The expanded 40–60 case blind benchmark — case list selected by fixed criteria and committed to git *after* code freeze — is the **sole evaluation** of this calibration. It is run once.
3. Any change to these constants after commit requires a written amendment with explicit rationale, itself made before inspecting case-level effects of the change.

## 2. The defect (mechanism-level)

From `validation/target_selection_diagnosis.md` §B: `tractability_score = raw_tractability × association_score`, with `PHARM_PRECEDENT_ASSOC_SCORE = 0.90`, flat, for any approved drug's MOA target. Two design errors exist **on principle**, independent of any case count:

- **(a) Provenance-blindness.** Direct-EFO precedent (the approval is *for this disease concept*) and parent-umbrella precedent (the approval is for a *parent* concept, imported by an EFO-hierarchy walk) receive identical evidentiary weight. The umbrella import is an indication-adjacent inference — a strictly weaker claim — and should not score identically to a disease-specific approval link.
- **(b) Double-counting.** Precedent targets are well-drugged *by construction*, so they already win on raw tractability. Multiplying by a flat 0.90 lets them win the multiplier too, and in doing so asserts *mechanistic centrality to this disease* — a claim precedent alone cannot establish. A drug can manage a disease's biology through a downstream node (IL5 in HES) while another node is causal (PDGFRA); both approvals are real, only one is the mechanistic node.

## 3. Calibration (constants fixed here)

| Setting | Value | Principle |
| --- | --- | --- |
| Direct-EFO pharmacological precedent | **0.90** (unchanged) | The claim is disease-specific: an approved therapy for this exact disease concept acts on this target. Full weight. |
| Parent-umbrella precedent | **0.70** | One level of EFO-hierarchy indirection weakens the claim to "an approved therapy for a concept that *subsumes* this disease acts on this target." That is comparable in evidentiary strength to a *strong genetic association*: Open Targets genetic_association scores for causal genes in monogenic disease concentrate in the 0.5–0.8 band. 0.70 is the midpoint of that band. Any value in [0.6, 0.8] expresses the same principle; we fix **0.70** a priori. |
| Mechanistic-convergence cap | genetic threshold **0.50** | If any considered target has independent genetic support ≥ 0.50, a **precedent-only** target (no genetic association for the disease) may not outrank the best such genetically supported target. 0.50 is Open Targets' conventional moderate-association boundary. |

**Rationale for the cap.** AgentBio's remit is rare, predominantly monogenic disease, where the causal node is genetically defined *by construction*. Precedent-only support asserts "a therapy touches this node"; it cannot overturn causality evidence. The cap is a **rank cap, not an exclusion**: precedent-only targets stay in the considered set, stay visible in the dossier's alternatives, and still win when no genetic target ≥ 0.50 exists (non-monogenic or genetically unmapped indications — the sildenafil/PDE5A-for-PAH pattern).

## 4. What this deliberately does not do

- **No per-case tuning.** The diagnosis identified the *mechanism* (flat 0.90, provenance-blind); the constants come from the evidence-strength argument in §3, not from inspecting which values flip the 8 diagnosed cases.
- **No suppression of legitimate precedent picks.** Where the drug's MOA target *is* the causal node, that target typically also carries genetic support — the cap does not bite.
- **Not exclusion.** Rank demotion only; the precedent target's approval evidence is still disclosed in the dossier.

## 5. Predictions registered before evaluation (falsifiable)

- **P1:** In the frozen benchmark, cases where the true target has OT genetic support ≥ 0.50 and a precedent-only hijacker previously won will now select the genetically supported target.
- **P2:** Cases with no genetic target ≥ 0.50 are unaffected — precedent still decides.
- **P3:** Umbrella-imported precedents lose ties and near-ties against direct-EFO precedents and against genetic targets in the 0.5–0.8 band.

Scored against the **full published table** of the single benchmark run, with enrichment-vs-chance statistics — never case-picked.

## 6. Known limitations (disclosed, with mitigations — not accepted silently)

- **Downstream-node therapies.** Where a therapy genuinely works through a downstream, non-causal node carrying no genetic support (symptomatic pathway modulation), the cap can demote the therapeutically right node. Mitigations, in order: (i) demotion is one rank, not removal — the dossier still presents the precedent target with its approval evidence; (ii) the writer's mechanism-reconciliation disclosure (already implemented) surfaces the reasoning to the reader; (iii) post-benchmark, if the published table shows this failure mode recurring, the response is a *documented* feature (e.g., a mechanism-direction-aware exemption) proposed in a new written amendment — not a silent constant tweak. Accepted residual: AgentBio's positioning is causal-node repurposing; symptomatic-node picks remain visible but demoted, and we say so in outreach materials.
- **Constants are principled, not fitted.** If the benchmark shows the [0.6, 0.8] band matters, that is an amendment-cycle question, handled in writing, after the frozen run — never mid-run.

## 7. Implementation sketch (non-normative)

- Split the constant: `PHARM_PRECEDENT_ASSOC_SCORE` → direct vs umbrella variants. Provenance (direct vs parent-umbrella) is already known at enrichment time (`_enrich_approved_via_parents`); it must be **stamped on the precedent target record** if it is not already — verify at implementation.
- Cap applied as a post-scoring comparator over the considered set in target selection (both auto-sweep and manual paths).
- Unit tests: direct vs umbrella scoring; cap bites only when a genetic target ≥ 0.50 exists; precedent-only target still wins when none does.

## 8. Sequence (held exactly)

This document → **user approval** → implementation + unit tests → full test suite → code freeze (commit/tag) → benchmark case list by fixed written criteria (committed) → **single run** → full table + enrichment statistics published.
