# Audit Claim-Set v1 — Construction Protocol (locked before claim selection)

**Status:** LOCKED 2026-08-10, before any claim is selected or any real claim is
run through the audit path.
**Governs:** construction of `validation/audit_claim_set_v1.json` under
`validation/audit_claimset_preregistration.md` (incl. Amendments 1–2) and
`validation/audit_source_scope_settlement.md`.
**Citation cutoff:** the cited artifact's own date must be strictly before
2026-08-10 (Amendment 1 mechanical rule).

## 1. Claim model

A claim is a tuple a user could legitimately submit to AgentBio's audit:

```
input:    { disease_name, drug_name, job_id_hint?, claim: {route?, dose?,
            modality?, context?} }
truth:    { defect_class, expected: <mechanical scoring rule>,
            citation: { source, identifier, artifact_date, release?, url,
                        retrieved_at } }
```

The scored runtime receives ONLY `input`. `truth` never reaches the runtime;
the harness separates them structurally and a leakage guard test proves it.

## 2. Groups, classes, and quotas (n = 100)

| Group | Class | Quota | Pool context | Expected behavior (mechanical rule) |
|-------|-------|-------|--------------|--------------------------------------|
| existing_fix | E1 safety_withdrawal | 2–4 | yes | `found` AND safety cap applied AND cap disclosed |
| existing_fix | E2 boxed_warning_not_withdrawal | 12–16 | yes | `found` AND black-box advisory shown AND NO safety cap |
| existing_fix | E3 direction_incompatible | 2–4 | yes | `found` AND mechanism-direction cap disclosed |
| existing_fix | E4 unresolved_name_honesty | 6–8 | yes | status `unresolved` (never `absent`) |
| novel | N1 combination-product splitting | 8 | no | N1 finding `flagged` |
| novel | N2 biologic modality mis-scope | 7 | no | N2 finding `flagged` (claim asserts small molecule) |
| novel | N3 species/preclinical-only mismatch | 7 | yes (target context) | N3 finding `flagged` |
| novel | N4 dose/route implausibility | 8 | no | N4 finding `flagged` (route mismatch) or `review` (systemic-context), per claim's declared expectation |
| control | none | 32 pool-free + 8 pool-context | mixed | NO N1–N4 finding with status `flagged` |

Total defect claims = 60, controls = 40. If a class under-yields after external
verification, the shortfall is reallocated in the fixed order E2 → E4 → E1 → E3
(existing-fix) or N1 → N4 → N2 → N3 (novel), and every shortfall and
reallocation is recorded in the construction log. Group totals (30/30/40) are
invariant.

## 3. Candidate enumeration sources (independence)

Claim CANDIDATES are enumerated from sources independent of the audit lanes
that will detect the defect:

- **Pool membership/flags** (persisted `output/candidates/*.json` for the three
  completed cases: Multiple myeloma `2de0698b…`, MEN2A `61f54232…`, hereditary
  chronic pancreatitis `cddaa8e1…`) are used ONLY for reachability of E-class
  claims (the drug must be auditable against a real persisted pool) and for
  absence checks of pool-context N3/control claims. Pool flags are NEVER claim
  ground truth.
- **N1** candidates: a fixed list of ten well-known FDA-approved fixed-dose
  combinations declared in the construction script. *(Amendment A, 2026-08-10,
  before any claim was constructed: the originally declared enumeration source —
  repoDB rows whose drug name contains `+` — is empty in the committed dataset,
  so it cannot enumerate anything. The substitute list is enumerated from domain
  knowledge, NOT from the openFDA audit lane; each candidate's combination
  status is then verified against the raw FDA label artifact per §4, and any
  candidate that fails verification is excluded and logged.)*
- **N2** candidates: enriched-dataset rows with non-small-molecule
  `chembl_molecule_type` (antibody/protein/enzyme — excluded from the discovery
  benchmark by criterion E1 — reused here).
- **N3** candidates: preclinical tool compounds (no INN/USAN approval) with
  literature evidence against the pool targets NR3C1 / RET / ADRB2, verified
  absent from repoDB and from the pinned DrugCentral 2023 snapshot.
- **N4 / controls**: established FDA-labeled drugs selected from domain
  knowledge and repoDB, constrained by §4.

## 4. Ground-truth verification (external, mechanical)

Every claim's defect (or cleanliness) is verified against the RAW external
artifact, never against AgentBio's parsed envelopes:

- **fda_label:** raw openFDA label JSON. Record set_id, document version,
  effective_time, application number, active_ingredient list, and route list
  verbatim. Valid only if effective_time parses and is strictly before
  2026-08-10.
- **chembl_mechanism / chembl_molecule:** live ChEMBL records, citing the live
  release. *(Amendment B, 2026-08-10, before any claim was constructed: the
  release date is read mechanically from ChEMBL's own `status.json`
  (`chembl_release_date`), which is part of the release artifact itself — no
  hand-maintained date map. The citation is valid iff that self-reported date
  is strictly before the cutoff; at the freeze the live release is ChEMBL_37,
  released 2026-05-01.)*
- **europe_pmc:** publication metadata (publication date, publication type).
  Valid only if dated strictly before the cutoff and not a review/editorial.
- **drugcentral_2023 / repodb:** committed, dated, hash-pinned offline
  datasets — valid by construction.

A claim whose citation cannot be verified at construction is EXCLUDED and
recorded in the construction log with the reason. No claim is substituted
after the scored run; exclusions happen only during construction.

It is explicit policy that ground-truth verification reads raw source
artifacts (including raw openFDA label JSON, which the N1/N2/N4 lane also
consumes). This does not inspect pipeline behavior: the scored quantity is
whether AgentBio's OWN parse of those artifacts produces the finding. A parser
miss is a study result, not a construction input. Detectors, thresholds, and
fixtures are never changed after any real claim is constructed (Amendment 2 §4).

## 5. Diversity and duplicate rules

- Exactly one claim per normalized drug name (INN) across the entire set;
  brand-name E4 claims use a brand whose generic appears nowhere else in the set.
- E-class claims are spread across at least two of the three pooled diseases
  where the eligible population allows.
- N4 claims span at least three distinct local-route families (ophthalmic,
  topical, nasal, …) where available.
- Control claims span at least ten distinct drugs and both pool-free and
  pool-context forms (32 + 8).

## 6. Scoring rules (mechanical, fixed pre-run)

For defect claims, `caught` is evaluated exactly as the table in §2 states,
from the archived raw audit output. For controls, `false_flagged` iff any
N1–N4 finding has status `flagged`. Findings with status `review` or
`unresolved` are NEVER counted as flags; they are reported as review/
abstention counts. Source failure states (`degraded`, `parse_failed`,
`unavailable`) on a claim's relevant lane make the claim an ABSTENTION:
not counted as caught, not counted as a miss; reported per class. (The
registered denominators assume no abstentions; both eligible-denominator
metrics and the conservative fixed-denominator view — abstentions counted as
not-caught — are reported. PASS is judged on the registered rule
`caught/eligible` for recall ≥ 0.80 with Clopper–Pearson lower 95% ≥ 0.65 and
false-flag ≤ 0.15 with upper 95% ≤ 0.30; if abstentions exceed 10% of a group
the study reports INVALID-DATA rather than PASS/FAIL.)

Citation revalidation at score time (Amendment 1 §5): if a claim's cited
artifact no longer verifies against the cutoff at audit time (e.g. a label
revised after construction), the claim is excluded as a construction defect,
reported, and removed from denominators (it is neither caught nor missed).

## 7. Disclosure-accuracy annotation (non-scored)

Because the persisted pools predate the black-box/withdrawal classifier fix,
the disclosure TEXT of a caught defect may contradict the external artifact
(e.g. a "withdrawn" badge on a marketed drug). A post-run annotation pass
classifies each caught defect's disclosure as consistent/contradicted against
the claim's citation. This annotation is reported separately and NEVER changes
the scored metrics.

## 8. Harness requirements (sealed)

- Label guard: runs only under `--label audit_claimset_v1`.
- Health gate immediately before scoring: live probes of ChEMBL (molecule
  endpoint), openFDA (label endpoint), Europe PMC (metadata), and PubTator3
  (search) must all succeed; any failure refuses the run and produces no score.
- Freeze manifest: sha256 of the claim set, code commit, harness config,
  declared cache policy (production caches as-is, 30-day TTL, degraded states
  never cached by design), and health requirements. Hash drift refuses the run.
- Idempotency: a completed scored artifact refuses re-run; the only exception
  is the pre-registered one-fix-one-rerun allowance for a proven harness
  defect, recorded in the preregistration.
- Raw per-claim outputs are archived BEFORE metric computation; metrics are
  computed from the archive by an independent recalculation path.
- Exactly one scored run. No detector, threshold, claim, or source
  configuration changes after results are seen.

## 9. Out of scope (restated)

No discovery-accuracy claims; no detector tuning on results; no claim
substitution after scoring; no clinical recommendations.
