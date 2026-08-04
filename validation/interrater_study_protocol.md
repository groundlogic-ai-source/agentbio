# Inter-Rater Study Protocol — AgentBio Audit Mode (v1, frozen 2026-08-04)

## Objective

Measure whether domain-experienced analysts triage candidate portfolios
**faster and with fewer missed traps** when assisted by AgentBio's Audit
mode than when working unassisted. This is the external half of the audit
value claim; the internal half is `audit_trap_results.json` (engineering
acceptance). The two are reported separately, always.

## Design

Within-subject crossover, counterbalanced:

- Each participant audits **2 candidate lists**: one **unassisted**
  (spreadsheet/document of their choosing, no AgentBio), one **assisted**
  (AgentBio Audit mode: Triage a list + dossier workspace).
- Two parallel list forms (A and B), matched for trap composition, so the
  assisted/unassisted arms never reuse the same content. Assignment of
  form×arm×order is counterbalanced across participants (AB/BA ×
  assisted-first/unassisted-first).
- No time limit, but time-to-completion is recorded per list.

## Materials

Frozen in `validation/interrater_lists.json` before the first participant:

- 4 lists (A1, A2, B1, B2) of 8 candidates each, drawn against completed
  AgentBio cases with persisted pools.
- Each list seeds the same trap composition: 2 seeded deception items from
  the pre-registered trap classes (`audit_traps_preregistration.md`: e.g.
  unresolvable-name honesty, XLogP-unresolved, absent-vs-poor-candidate),
  2 clean controls, 4 fillers.
- Ground truth is derived at freeze time by running
  `POST /api/audit/triage` for each list and storing the full response in
  the list's `ground_truth` block. The frozen responses — not the live
  pool — are the scoring reference for the entire study.

## Participants

Target n ≥ 8 analysts with professional drug-repurposing or translational
research experience. Recruitment, compensation, and consent are out of
scope of this document.

## Measurements

Per participant per list, via `validation/run_interrater_scoring.py`:

- **Primary: seeded-trap recall** — fraction of seeded deception items the
  participant correctly flags (assisted arm is expected to approach the
  trap-benchmark recall; unassisted arm is the honest baseline).
- **Secondary: false-flag rate** — flags raised on clean controls.
- **Secondary: time-to-verdict** — minutes per list.

## Analysis and reporting rules

- Paired (within-subject) comparison of trap recall across arms; report
  descriptively with per-participant deltas. No re-rolls, no optional
  stopping, no post-hoc exclusion of participants.
- Success criterion (pre-registered): assisted-arm trap recall exceeds
  unassisted-arm recall with no increase in false-flag rate.
- Any deviation from this protocol is logged in the results artifact.
- Results live in `validation/interrater_results/` and are never merged
  into, or reported alongside, internal engineering acceptance numbers.
