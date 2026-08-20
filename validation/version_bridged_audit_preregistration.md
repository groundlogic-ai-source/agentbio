# Version-Bridged Audit Upgrade — Pre-registration

**Registered:** 2026-08-20, before implementation or an acceptance run.  
**Study label:** `version_bridged_audit_acceptance_v1`  
**Purpose:** bounded engineering acceptance and paired attribution; **not** a new
Study C discrimination result and **not** a general-accuracy claim.

## Immutable comparators

The following artifacts are historical comparators and must not be edited,
re-scored, merged, or relabeled:

- Study C v1 results and miss autopsy
- Study C target-rank rescue analysis
- Machine-v2 acceptance result: **0/16** under its original exact-string
  target-matching contract

Their byte hashes are pinned in
`validation/version_bridged_audit_manifest.json`. The manifest also pins the
pre-change audit, triage, target-selection, ChEMBL, and holdout source files.

## Questions fixed before implementation

### Q1 — Candidate-conditioned audit coverage

When a scientist explicitly supplies a drug that discovery did not generate,
can AgentBio return a grounded, source-state-aware audit instead of only
`ABSENT_FROM_POOL`?

This is an audit question. The supplied drug must never be inserted into the
candidate pool, assigned a discovery rank, counted as a rediscovery hit, or
used to change any candidate score.

### Q2 — Stable target-identity correction

How many of the 16 frozen misses have a mechanism target that is already in
the machine-v2 disease target universe when compared by stable target identity
(ChEMBL target ID / UniProt accession / gene symbol) rather than raw display
labels?

This is a correction to a post-hoc acceptance measurement. The original 0/16
artifact remains immutable and reportable under its original contract.

### Q3 — Pharmacological-precedent/mechanism-pool completeness

Does a general holdout-safe completeness repair create at least one true pool
rescue?

The predeclared suspected defect is ordering: the mechanism-only lane caps raw
molecule IDs before approval filtering, so valid approved drugs may be omitted
according to arbitrary ChEMBL identifier order. The allowed repair is to
resolve metadata in batches, filter to approved molecules first, then apply a
deterministic bound. No case-specific drug, target, disease, weight, or mapping
may be added.

## Frozen cases and controls

### Acceptance cases

Exactly the 16 rows in `validation/machine_v2_acceptance.json`. Case membership
and historical miss classes are scoring-side metadata only.

### Controls

- Found-by-discovery positives: Prednisone / systemic lupus erythematosus and
  Lenalidomide / multiple myeloma
- Supplied-only unrelated or negative controls: Infliximab / giant cell
  arteritis and Mycophenolate mofetil / interstitial cystitis
- Unresolved identity: synthetic non-drug name `Definitely Not A Real Drug 9QZ`
- Degraded mechanism source: deterministic mocked transport failure

Controls may verify status separation, fail-closed source handling, and absence
of rank/score mutation. They may not set thresholds or tune target mappings.

## Endpoints

Reported separately:

1. `rediscovery_recovery` — unchanged discovery endpoint; audit-only results
   always contribute zero.
2. `stable_identity_universe_overlap` — target identity appears anywhere in the
   frozen machine-v2 universe.
3. `production_gate_overlap` — target identity is within the first five target
   rows used by production.
4. `mechanism_pool_recovery` — the frozen drug's stable molecule identity is
   returned by the general mechanism-only pool for a production-gate target.
5. `audit_scope_status` — one of `found_by_discovery`,
   `auditable_only_because_supplied`, `not_assessable`, or `source_failure`.
6. Deterministic miss reason — `FOUND`, `NAME_RESOLUTION_GAP`,
   `BIOLOGIC_STRUCTURAL`, `ASSAY_POOL_GAP`, `TARGET_NOT_SELECTED`,
   `NO_MECHANISM_DATA`, `NO_CASE`, or `NO_CANDIDATES`.

## Promotion gates

The additive audit contract may ship only if:

- discovery rank, score, candidate membership, and historical labels are never
  mutated;
- the supplied-only and found-by-discovery states remain distinct;
- unavailable/degraded mechanism evidence is not converted into biological
  absence;
- all controls and existing audit/holdout tests pass.

A discovery completeness repair may ship enabled only if:

- at least one of the 16 cases has a paired mechanism-pool recovery;
- no held-out name, molecule family, or structure identity re-enters through
  disease-side precedent;
- disabled mode reproduces the pre-change baseline;
- the unrelated/negative controls are not converted to discovery hits;
- no case-specific constants or target aliases are introduced.

If those gates fail, retain only the additive audit upgrade.

## Cost and stopping rules

- Acceptance uses no LLM calls.
- Frozen/cached facts are preferred; new source lookups are bounded and cached.
- The run is resumable and fail-closed.
- No new 15–20 case Study C run is authorized by this protocol.
- No weight or threshold iteration is allowed after inspecting case outcomes.

## Interpretation

The historical v1 and machine-v2 results remain valid for their frozen
machines and contracts. A paired bridge attributes differences to the new
contract; it does not erase the old result and does not establish general
accuracy. Audit coverage must never be added to rediscovery recall.