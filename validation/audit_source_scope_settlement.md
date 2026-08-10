# Audit source and detector scope settlement

**Status:** committed pre-claim-set scope settlement
**Governing protocol:** `validation/audit_claimset_preregistration.md`, including Amendments 1–2
**Citation cutoff:** the cited artifact's own date must be strictly before 2026-08-10

## Purpose

This record closes Task 55's source-admission and detector-development scope
before construction or inspection of the scored audit claim set. It contains no
scored claim identity, citation, source identifier, label, or expected outcome.

## Admitted audit-context lanes

### 1. Structured regulatory product labels

Admitted through the openFDA drug-label API as an FDA SPL mirror. The audit
envelope preserves:

- active ingredients and explicit multi-ingredient status;
- regulatory product type/application-number modality basis;
- approved route and dosage form;
- SPL set id, document id, version, and effective date;
- exact quoted label fields and a stable DailyMed set-id URL;
- citation eligibility under the preregistered cutoff; and
- distinct `ok`, healthy `empty`, `filtered_empty`, `degraded`,
  `parse_failed`, and `unavailable` source states.

Only `ok`, healthy `empty`, and deterministic `filtered_empty` envelopes may be
cached. Degraded, malformed, and unavailable states are never cached as
biological absence.

### 2. Entity-linked literature assertions

Admitted through a bounded PubTator3 entity-relation search joined to Europe PMC
publication metadata. The audit envelope preserves:

- resolved chemical and mechanism entity identifiers and sentence spans;
- PMID, PMCID, DOI, title, journal, publication type, and publication date;
- relation/action phrase, normalized direction, and relation span;
- species and experimental context without inferring human context from target
  identity;
- exact evidence sentence/location and row-level lineage id; and
- distinct `ok`, healthy `empty`, `filtered_empty`, `degraded`,
  `parse_failed`, and `unavailable` source states.

Admission requires both resolved entities, an explicit non-negated action,
cutoff-eligible publication metadata, and a non-review publication type. Bare
co-mention, speculation, negation, and review/editorial evidence do not admit an
assertion. Source/provider counts never become a score.

## N1–N4 detector settlement

The shared deterministic detector contract is `audit-context-v1`.

- **N1 — combination-product splitting:** flag a dated label with two or more
  distinct active ingredients.
- **N2 — biologic modality mis-scope:** flag a direct claimed-modality conflict;
  otherwise require explicit scope review for a resolved biologic/vaccine.
- **N3 — species/preclinical-only mismatch:** flag when every admitted
  entity-linked assertion is animal, in vitro, or unresolved; source failures
  and no-admission outcomes remain unresolved rather than clear.
- **N4 — dose/route implausibility:** flag a direct claimed-route mismatch and
  require review when locally labeled routes are paired with a systemic claim
  context. Route alone never proves that exposure is impossible. Dose,
  formulation, pharmacokinetics, and tissue distribution remain human-review
  requirements.

All detector outputs are disclosure-only. They cannot alter candidate
generation, ranks, scores, caps, discovery benchmark reproduction, or clinical
recommendations. Single-drug audit and list triage use and persist the same
structured output object rather than separate detector implementations.

## Development and leakage boundary

Detector tests use invented entities and synthetic/non-scored records only.
Future scored drug names, diseases, products, citations, source ids, labels,
and expected outcomes are forbidden during detector development. This task does
not construct, inspect, sample, or label any member of the real 100-claim audit
set.

## Explicitly deferred

The following are not admitted for the frozen audit study and remain future
work:

- PubChem BioAssay;
- LINCS/Connectivity Map;
- pathogen-specific source expansion;
- full biologic candidate ranking or biologic discovery retuning;
- broad full-text relation extraction beyond the bounded entity-linked lane;
- any discovery-ranking retuning; and
- any clinical recommendation or treatment-selection claim.

The next task may construct and freeze the claim set only against this settled
contract and the preregistration amendments. Detector misses are not grounds
for post-hoc detector changes or reruns; only a demonstrated harness defect may
invoke the preregistered one-fix/one-rerun exception.