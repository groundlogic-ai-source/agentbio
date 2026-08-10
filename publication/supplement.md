# Supplement — AgentBio frozen benchmark and external audit validation

**[Author Name]**, Independent Researcher · 2026-08-10

This supplement contains the complete provenance inventory, per-case and
per-claim tables, the LLM touchpoint inventory, source versions, and the
reproducibility checklist. All artifacts listed are committed in the project
repository.

## S1. Frozen artifact inventory

| Artifact | Path | Identity |
|---|---|---|
| Benchmark v2 results | `validation/benchmark_results_v2.json` | freeze `benchmark-freeze-v2`, mode `deployment-attestation` |
| Benchmark case list (selected 50) | `validation/benchmark_case_list.json` | seed 20260731 |
| Selection criteria + attrition | `validation/benchmark_case_selection_criteria.md`, `validation/benchmark_attrition.md` | pre-registered 2026-07-31 |
| Benchmark v2 pre-registration | `validation/benchmark_v2_preregistration.md` | 2026-08-01 + Amendments 1–6 |
| v1 partial (terminated) | `validation/benchmark_results_v1_partial.json`, `validation/benchmark_v1_partial_report.md` | freeze `benchmark-freeze-v1` |
| Source-ablation control | `validation/v2_source_ablation_results.json` | 52/52 arms, pre-freeze |
| Engineering acceptance | `validation/engineering_acceptance_results.json` | label `engineering_acceptance` |
| Audit claim set (frozen) | `validation/audit_claim_set_v1.json` | sha256 `32efd7d965f62e2cd0900578e6ab2f78b0585d65f33095a651faf6be796523c4` |
| Audit construction protocol + log | `validation/audit_claimset_construction_protocol.md`, `validation/audit_claimset_construction_log.md` | — |
| Audit pre-registration | `validation/audit_claimset_preregistration.md` | freeze record + Amendments 1–3 |
| Audit freeze manifest | `validation/audit_claimset_freeze_manifest.json` | code commit, harness config, cache policy, results-hash binding |
| Audit raw outputs | `validation/audit_claimset_raw_outputs.json` | 100/100, archived before scoring |
| Audit results | `validation/audit_claimset_results.json` / `.md` | scored 2026-08-10; verdict FAIL |
| Rerun-allowance consumption | `validation/audit_claimset_rerun_allowance_consumed.json` | allowance permanently consumed |
| Trap benchmark | `validation/audit_trap_results.json` / `.md` | label `audit_trap_benchmark`, PASS |
| Scope settlement (audit) | `validation/audit_source_scope_settlement.md` | pre-claim-construction |
| DrugCentral snapshot | `data_sources/drugcentral_2023_snapshot.sqlite` | dump sha256 `055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f`; build record `validation/drugcentral_snapshot_build.json` |
| Reviewer pilot | `validation/reviewer_pilot_report.md` | development instrument |
| repoDB development suites | `validation/repodb_results*.md`, `combined_table*.md` | development instruments |

## S2. Benchmark v2 — per-case results (47 executed cases)

<!-- INCLUDE: supplement_cases.md -->

## S3. Audit claim-set — per-claim outcomes (100 claims)

Ground-truth citations (FDA label revisions, ChEMBL mechanism records,
Orphanet entries, primary papers) are embedded per claim in
`validation/audit_claim_set_v1.json`; per-claim raw audit outputs are in
`validation/audit_claimset_raw_outputs.json`.

<!-- INCLUDE: supplement_claims.md -->

## S4. LLM touchpoint inventory (full disclosure)

| Touchpoint | File | Model | Output can affect score/rank? |
|---|---|---|---|
| Mechanism-direction compatibility | `data_sources/mechanism_direction.py` | gpt-5.4 | Yes — INCOMPATIBLE caps composite at 0.400 |
| Safety web-check step 2 (withdrawal / black-box classification) | `data_sources/safety_check.py` | claude-sonnet-4-6 (+ web search) | Yes — confirmed withdrawal applies the hard safety cap; black-box is advisory-only |
| Clinical-trial stop-reason classification | `data_sources/clinicaltrials.py` | claude-haiku-4-5-20251001 | Yes — decides whether a stopped trial counts as efficacy failure |
| PubMed relationship extraction (assertion vs co-mention) | `data_sources/pubmed.py` | claude-sonnet-4-6 | Indirect — YES/NO gates literature-assertion inclusion |
| Biologist druggability abstract screening | `agents/biologist.py` | claude-sonnet-4-6 | Indirect — selects supporting PMIDs for the druggability signal |
| Difficulty summary | `agents/biologist.py` | claude-haiku-4-5-20251001 | No (context only) |
| Chemist candidate rationale | `agents/chemist.py` | claude-sonnet-4-6 | No — constrained restatement of computed facts |
| Stage-1 top-5 narration | `agents/target_selection.py` | claude-sonnet-4-6 | No — narrates a fixed numeric table |
| Audit narration | `api/audit.py` | claude-opus-4-5 | No — and never invoked in scored runs (`narrate=False`) |

All benchmark and audit scoring paths run with `narrate=False`: zero
narration LLM calls in any scored path. During retrospective evaluation,
deterministic filters redact the held-out drug's names/synonyms/salts/
identifiers from all LLM query construction upstream of the model.

## S5. Pre-registration and amendment log

**Benchmark v2** (`validation/benchmark_v2_preregistration.md`): base
2026-08-01; A1 screened case list + headline framing + dry-run + post-cutoff
stratum; A2 broad evidence architecture + anti-overfit rules; A3
operationalization (screen version, tiered-pool supersession disclosed,
results separation, preflight automation); A4 GtoPdb structure-204 tolerance
+ blessed fingerprint transition; A5 deployment freeze attestation; A6
DrugCentral local snapshot (access-mode change after a 25+ h upstream
outage; health gate refused to start throughout).

**Audit claim-set v1** (`validation/audit_claimset_preregistration.md`):
base freeze 2026-08-10; A1 mechanical citation-validity rule (cutoff
2026-08-10); A2 class-level-vs-instance novelty protocol; freeze record
(100 claims; N3 closed at zero); A3 one-fix-one-rerun allowance exercised
(harness plumbing crash before any metric; re-run scored the original
archived outputs; allowance since permanently consumed and results
hash-bound).

## S6. Source versions and access modes

| Source | Version / access at freeze | Notes |
|---|---|---|
| ChEMBL | live API (current release at freeze, 2026-08); cached responses preserved | molecule `max_phase` string-float quirk handled |
| DrugCentral | official 2023-11-01 PostgreSQL dump, sha256-pinned local snapshot | access-mode change (live API → snapshot) registered as Amendment 6; conformance replay 0 mismatches |
| GtoPdb/IUPHAR | versioned downloads (release current at freeze) | commercial access terms are a production go-live dependency |
| Open Targets | live API at freeze | genetics-only association for Stage 1 |
| Orphanet | live API at freeze | universe + epidemiology |
| Reactome | live API at freeze | pathway neighbors, low-maxDepth preference |
| Europe PMC / PubTator3 | bounded entity-linked queries; raw responses archived | counts never score; identified papers only |
| ClinicalTrials.gov | API v2 | registration alone never counts as efficacy |
| openFDA / DailyMed | structured label fields; SPL revision dates recorded | label assertion is human-use evidence, not efficacy proof |
| PubChem | PUG REST | identity via InChIKey-first |
| AlphaFold DB / Boltz | structure confidence (apo pre-check; at most 1 Boltz call per run when K>1) | structure prediction never substitutes for measured pharmacology |

## S7. Compute environment

Python 3.11 (Replit NixOS container); FastAPI/uvicorn service; SQLite
key-value cache with per-source TTLs; scoring deterministic given frozen
sources and cache state. Validation harnesses: `validation/run_benchmark.py`,
`validation/run_audit_claimset.py`, `validation/run_audit_traps.py`,
`validation/run_v2_preflight.py`. Tests: 544 unittest cases green at report
commit (unittest-only environment).

## S8. Reproducibility checklist

1. `python3 publication/make_figures.py` — regenerates every headline number,
   table, and figure from the committed artifacts; asserts the audit metrics
   recomputed from the raw archive match the stored frozen results.
2. `python3 -m validation.run_audit_claimset --label audit_claimset_v1
   --recalc-only` — independent recomputation of the frozen audit metrics
   (read-only; refuses if results drift).
3. `python3 publication/build_pdf.py` — rebuilds this supplement and the
   manuscript PDFs.
4. Freeze integrity: claim-set sha256 + code-commit ancestry + `.py` drift +
   harness-config equality are enforced by the harness on every invocation;
   the results artifact is hash-bound in the freeze manifest.
5. The benchmark's deployment attestation pins pipeline fingerprint +
   control-artifact hash + screened-list hash; a published deployment is
   immutable per publish, so redeploying different code fails attestation.
