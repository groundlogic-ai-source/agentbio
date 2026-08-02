# Source-Coverage Matrix — the 5 v1 genuine misses

**Purpose:** before committing to a v2 multi-lane evidence architecture, prove *empirically*
which real, licensable data sources actually contain the mechanistic evidence each v1 miss
needs. This is the pre-implementation feasibility gate ("prove the sources contain the
evidence before adding the plumbing").

All probes were live calls to the public endpoints of each source, anchored on the *specific
mechanistic target* the miss requires (not a generic name search). Freeze note: this is
research/documentation only — no changes to `agents/`, `data_sources/`, or `cache/`.

## The matrix

| Miss | Needed mechanism | ChEMBL (v1 funnel) | GtoPdb/IUPHAR | openFDA label | Europe PMC (anchored) |
|---|---|---|---|---|---|
| Phenobarbital | GABA-A potentiation | **dark** (0 mechanism rows) | **wrong** — only Pregnane X receptor | weak — barbiturate CNS text, GABA-A **not** named in extracted MoA | **1579 hits** anchored on GABA-A |
| Lamotrigine | Naᵥ / SCN sodium channel | family-target ID, 0 direct activities | **HIT** — Naᵥ1.2 | label says "mechanism unknown"; pharm_class wrong (OCT2/DHFR) | (not needed) |
| Mercaptopurine | de novo purine synthesis (antimetabolite) | **dark** (PPAT has 0 assays) | **dark** (0 interactions) | **HIT** — label explicitly: "inhibitors of de novo purine synthesis and purine nucleotide interconversions" | 368 hits anchored on purine synthesis |
| Vincristine | tubulin / microtubule | only cytotoxicity screens | **HIT** — tubulin beta class I | corroborating — "inhibition of…" microtubule text | (not needed) |
| Promazine | DRD2 (D2 receptor) | considered, wrong format/species | **HIT** — D2 + D3 receptor | no label (older drug) | (not needed) |

## What closes each gap (minimum viable lane per miss)

- **Lamotrigine, Vincristine, Promazine → GtoPdb alone.** Three of five misses are recovered
  purely by adding the GtoPdb curated-interaction lane. These are targets ChEMBL's strict
  human-IC50/Ki pool structurally could not surface, but a curated pharmacology DB names them
  directly.
- **Mercaptopurine → openFDA label mechanism-class lane** (corroborated by Europe PMC). Both
  structured target DBs are dark; the FDA label states the mechanism verbatim.
- **Phenobarbital → Europe PMC entity-anchored lane.** The *only* miss that neither structured
  source resolves. GtoPdb returns the wrong receptor (PXR), the label doesn't name GABA-A in
  its extractable MoA text, but anchored literature is overwhelming (1579 hits).

## Minimum source combination that closes all 5

**GtoPdb + openFDA drug label + Europe PMC (entity-anchored).** Three lanes.
DrugCentral (REST endpoint was unreliable in testing) and PubChem BioAssay are **not required**
to close these five — they belong in the "breadth/insurance" tier, not the critical path.

## Two architectural findings that change the v2 data model

1. **Not every mechanism is a protein target.** Mercaptopurine's mechanism is *metabolic*
   (antimetabolite / de novo purine synthesis inhibition), not binding to a discrete protein.
   PPAT — the nominal "target" — is ChEMBL-dark *and* GtoPdb-dark, and even if it weren't,
   representing this drug as `drug → protein` misrepresents how it works. **The evidence ledger's
   object must be a union type: `{protein target | pathway | mechanism-class}`.** A strictly
   target-centric ledger can never represent this class of drug, which is exactly the class
   antimetabolite repurposing depends on.

2. **The literature lane is load-bearing, not redundant insurance.** Phenobarbital proves the
   curated structured DBs are *not* a superset of the truth — GtoPdb actively returns the wrong
   receptor for it. At least 1 of 5 misses is closable *only* by the entity-anchored literature
   cascade. This justifies keeping Europe PMC in the critical path, while the anchoring
   discipline (drug entity AND mechanistic-target entity, count-gated) keeps it reproducible and
   bounded — the opposite of free-form web/Scholar search, which was correctly rejected.

## Consequences for the v2 pre-registration

- Add three generation lanes as a **union** (not intersection) with the existing ChEMBL pool:
  GtoPdb curated interactions, openFDA label mechanism-class extraction, Europe PMC
  entity-anchored cascade.
- Evidence-ledger object type must be widened to `{target | pathway | mechanism-class}` before
  any lane is wired, or mercaptopurine's evidence has nowhere to land.
- Each ledger row carries source-calibrated confidence and an independence group so a drug
  confirmed by GtoPdb + label + literature isn't triple-counted as if independent.
- Prefer bulk downloads over live APIs for the frozen benchmark run (reproducibility +
  outage-resilience — the ChEMBL outage already killed one run).
