# Audit Trap Benchmark — Pre-registration (v1, frozen 2026-08-03)

## Purpose

AgentBio's rediscovery benchmarks measure **discovery** (can it find a known
drug?). This benchmark measures the claim the product is actually positioned
on: **audit** — can it catch the failure classes that make naive repurposing
pipelines untrustworthy, and can it do so without false-flagging clean cases?

This artifact is an engineering acceptance instrument. It is **not** benchmark
v2, must never be reported as discovery accuracy, and refuses to run under any
other label.

## Protocol

- **One run.** No re-rolls after seeing results. If the harness itself is
  defective, the defect is fixed, the fix is recorded here, and the run is
  repeated exactly once with a note.
- **Offline-first.** Every trap exercises production code paths
  (`api.audit`, `api.triage`, `api.dossier`, `agents.reviewer`,
  `data_sources.multisource_candidates`) against stubbed inputs. No live
  ChEMBL/PubChem/LLM calls, so results are reproducible and outage-proof.
- **Frozen cases.** Trap and control cases are fixed literals in
  `run_audit_traps.py`. Changing a case after the first scored run
  invalidates comparability with prior artifacts.

## Trap classes (10) — ground truth: must be caught

| # | Class | What a naive tool does | Required behavior |
|---|-------|------------------------|-------------------|
| T1 | safety_cap_disclosure | shows raw composite 0.72 | disclose cap; composite ≤ 0.400 |
| T2 | blackbox_not_withdrawal | treats black-box as withdrawal/cap | advisory only, no safety cap |
| T3 | direction_incompatible | ranks an antagonist for an agonist disease | mechanism-direction cap + disclosure |
| T4 | label_artifact_screen | publishes admin-exclude association as signal | LABEL_ARTIFACT_SUSPECT verdict |
| T5 | confirmation_discipline | reports discovery-significant as confirmed | NOT confirmed when holdout fails/absent |
| T6 | unresolvable_name_honesty | reports "absent" for a typo/brand name | UNRESOLVED — not evidence of absence |
| T7 | salt_form_dedup | counts salt + parent as two candidates | active-moiety identity dedup |
| T8 | degraded_source_honesty | silent zero coverage on source failure | provider health preserved verbatim |
| T9 | unobserved_not_zero | imputes 0 for unmeasured terms | drop from numerator AND denominator |
| T10 | xlogp_unresolved_disclosure | treats PubChem failure as low lipophilicity | XLogP UNRESOLVED disclosure |

## Controls (4) — ground truth: must NOT be flagged

| # | Control | Required behavior |
|---|---------|-------------------|
| C1 | clean approved candidate, full coverage, XLogP 2.1 | zero flags |
| C2 | hypothesis passing discovery+confirmation, all confounds survived | VERIFIED |
| C3 | measured tanimoto 0.0 (adverse but measured) | stays in scoring — not dropped as "unobserved" |
| C4 | resolvable drug genuinely absent from pool | ABSENT (not UNRESOLVED) |

## Metrics and pre-registered pass thresholds

- `trap_recall = caught / 10` — **PASS requires ≥ 0.90**
- `control_false_flag_rate = flagged / 4` — **PASS requires ≤ 0.25**
- `precision = caught / (caught + flagged_controls)` — reported
- Overall verdict PASS requires both thresholds.

Thresholds are set before the first scored run. A failure is a product defect
to fix, not a threshold to move.

---

## Addendum v1.1 (2026-08-04) — two trap classes added

Registered BEFORE the cases were implemented, per protocol. Thresholds are
unchanged (trap recall ≥ 0.90, control false-flag rate ≤ 0.25); the recall
denominator becomes 12. Prior v1 artifacts remain comparable per-class.

| # | Class | What a naive tool does | Required behavior |
|---|-------|------------------------|-------------------|
| T11 | degraded_200_empty_pool_not_cached | caches a degraded 200-with-empty-payload as an authoritative empty pool, zeroing a target's pool for the cache TTL | empty payload = failure; no cache write unless a non-empty raw activity payload was seen |
| T12 | holdout_name_no_api_leak | precedent-target path leaks the held-out drug by exact name, salt/ester shared parent, or ChEMBL drug_indication EFO fallback rediscovery after redaction | name + parent redaction; redacted-to-empty short-circuits to a holdout sentinel; fallback never fires |

Both classes are driven offline at the cache/redaction seam with transport
stubbed, per the offline-first convention (results must be reproducible
during a ChEMBL outage). Their fully live counterparts require a stable
ChEMBL window and stay out of the frozen set; adjacent behavior is also
pinned by `validation/test_cache_failures.py` and the holdout guard suites.

Harness defect note (2026-08-04): T12's first implementation spied on the
sentinel cache write by string-matching `"__holdout_redacted__"` in the
cache key, but `make_key()` hashes its arguments, so the write was invisible
to the spy and T12 recorded a false MISS on its first run (production
redaction behaved correctly throughout). The spy was fixed to assert by
written VALUE, per the one-fix-one-rerun rule in Protocol, and the run was
repeated exactly once.
