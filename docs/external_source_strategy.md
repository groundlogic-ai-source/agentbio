# External source reliability: API vs. pinned local data

Measured 2026-08-11, during a live PubChem `PUGREST.ServerBusy` outage that
wedged a study run. Disk available in this workspace: **236 GB**.

## The problem is real, and partly self-inflicted

Outages have repeatedly cost this project real work: a ChEMBL outage
terminated the first benchmark attempt and poisoned a repoDB harness run
(error rows persisted, then skipped on re-run); DrugCentral's hosted API went
down hard enough that we replaced it with a pinned local snapshot; GtoPdb
returns 204/503 in ways that needed per-endpoint tolerances.

But the PubChem wedge was **mostly our own doing**: 1,476 of 1,506 failures
were BindingDB accession IDs (`BDBM…`) sent to PubChem's *name* endpoint —
2,958 calls in one disease that could never resolve. Fixed at the source:
identifier-shape routing, structural (SMILES) resolution for accession IDs,
and an offline refusal when neither is available. **Rule: never cross-resolve
an unbounded candidate list through a second service by name.** Use the
source's own structure data (ChEMBL and BindingDB both ship SMILES).

## What is actually available in bulk (measured, not assumed)

| Source | Bulk artifact | Size | Verdict |
|---|---|---|---|
| PubChem | `Drug-Names.tsv.gz` | 8 MB | useful for offline name→CID |
| PubChem | `CID-Identifiers.tsv.gz` | 936 MB | viable if ever needed |
| PubChem | `CID-SMILES` / `CID-Mass` / `CID-Title` / `CID-InChI-Key` | 14 / 13 / 18 / **70 GB** | impractical for our needs |
| PubChem | **XLogP** | *not published in bulk* | must be harvested via PUG REST |
| ChEMBL | `chembl_37_sqlite.tar.gz` | **55 GB** compressed | feasible on disk; heavyweight migration |
| DrugCentral | local snapshot (already adopted) | 5.9 MB | done — the working precedent |

The decisive fact: **the load-bearing entity set is bounded.** The repoDB
dataset has **1,540 distinct drugs**. We do not need a mirror of PubChem; we
need those 1,540 rows, frozen.

## Tiering

**Tier 1 — bounded, static facts → pin locally. (Implemented.)**
`data_sources/pubchem_snapshot.py` + `validation/build_pubchem_snapshot.py`.
One ~15-minute harvest over 1,540 names yields a few-MB SQLite that is
committed, sha256-pinned, and consulted before the network. Physicochemical
properties are static; there is no scientific reason to re-fetch them per
run. Follows the DrugCentral contract: read-only, **fail-closed on
corruption**, and a miss returns `None` (a coverage fact) rather than a
stamped zero — which matters, because a silently-dropped scoring term
under-scores a candidate against peers who all score 1.0 on that term.

**Tier 2 — ChEMBL local (recommended, not yet done; needs a decision).**
Highest reliability payoff: ChEMBL is referenced by 10 modules and is the
single most study-critical source. But it is a **55 GB download and a
semantics-sensitive migration** — the bioactivity pool drives every frozen
result we have. It must not be swapped in casually: it requires an
equivalence run against existing frozen results before any study uses it,
otherwise comparability with the frozen benchmark is lost.

**Tier 3 — inherently live → keep as APIs.** PubTator, Europe PMC,
ClinicalTrials.gov, openFDA labels, Open Targets. These are evidence lanes
where *currency* is the point and a stale local copy is a scientific
liability, not an asset. Harden them with health gates and honest degradation
instead: refuse to start, never cache a transient failure, and never let a
degraded 200-with-empty-payload look like a real negative.

## The reproducibility argument (why this matters beyond uptime)

A frozen study pinned to a snapshot hash re-runs **byte-identically**. A study
that calls a live API cannot promise that to an external reviewer — the data
underneath it moves. Every source we move into Tier 1 converts a
"trust our run" claim into a "re-run it yourself and hash-compare" claim,
which is exactly what an outside partner will ask for.
