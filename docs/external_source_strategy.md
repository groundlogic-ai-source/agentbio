# External source reliability: live API vs. pinned local data

Which of the external biomedical sources this pipeline depends on are safe to
call live, which should be frozen to a committed local snapshot, and why the
answer differs per source.

Measurements taken 2026-08-11, during a live PubChem `PUGREST.ServerBusy`
outage that wedged a study run.

## Failure history

Provider outages have repeatedly destroyed completed work:

- A ChEMBL outage terminated the first benchmark attempt and poisoned a
  repoDB harness run. Error rows persisted and were then skipped on re-run,
  so the damage outlived the outage.
- DrugCentral's hosted API went down hard enough that it was replaced by a
  pinned local snapshot.
- GtoPdb returns 204 and 503 in ways that required per-endpoint tolerances.

The PubChem wedge was not an outage. 1,476 of 1,506 failures were BindingDB
accession IDs (`BDBM…`) sent to PubChem's *name* endpoint, 2,958 calls in a
single disease that could never resolve. The fix was identifier-shape
routing, structural (SMILES) resolution for accession IDs, and an offline
refusal when neither is available.

**Rule: never cross-resolve an unbounded candidate list through a second
service by name.** Use the source's own structure data; ChEMBL and BindingDB
both ship SMILES.

## What is available in bulk, measured rather than assumed

| Source | Bulk artifact | Size | Verdict |
|---|---|---|---|
| PubChem | `Drug-Names.tsv.gz` | 8 MB | useful for offline name→CID |
| PubChem | `CID-Identifiers.tsv.gz` | 936 MB | viable if ever needed |
| PubChem | `CID-SMILES` / `CID-Mass` / `CID-Title` / `CID-InChI-Key` | 14 / 13 / 18 / **70 GB** | disproportionate to what the pipeline reads |
| PubChem | **XLogP** | *not published in bulk* | must be harvested via PUG REST |
| ChEMBL | `chembl_37_sqlite.tar.gz` | **55 GB** compressed | feasible on disk; heavyweight migration |
| DrugCentral | local snapshot (already adopted) | 5.9 MB | the working precedent |

Feasibility was assessed against roughly 236 GB of available disk.

The decisive fact is that **the load-bearing entity set is bounded**. The
repoDB dataset contains 1,540 distinct drugs. Pinning those 1,540 rows is a
different problem from mirroring PubChem, and only the first is necessary.

## Tiering

**Tier 1, bounded static facts, pinned locally. Implemented.**
`data_sources/pubchem_snapshot.py` and `validation/build_pubchem_snapshot.py`.
One roughly 15-minute harvest over 1,540 names yields a few-MB SQLite file
that is committed, sha256-pinned, and consulted before the network.
Physicochemical properties are static, so there is no scientific reason to
re-fetch them per run. It follows the DrugCentral contract: read-only,
fail-closed on corruption, and a miss returns `None`, a coverage fact, rather
than a stamped zero. That distinction matters because a silently-dropped
scoring term under-scores a candidate against peers that all score 1.0 on it.

**Tier 2, ChEMBL local. Recommended, not implemented, requires a decision.**
ChEMBL is referenced by 10 modules and is the most study-critical source, so
the reliability payoff is the largest available. It is also a 55 GB download
and a semantics-sensitive migration: the bioactivity pool drives every frozen
result in this repository. Swapping it in requires an equivalence run against
the existing frozen results first, or comparability with the frozen benchmark
is lost.

**Tier 3, inherently live, kept as APIs.** PubTator, Europe PMC,
ClinicalTrials.gov, openFDA labels, Open Targets. These are evidence lanes
where currency is the point, and a stale local copy is a scientific liability
rather than an asset. They are hardened with health gates and honest
degradation instead: refuse to start, never cache a transient failure, and
never let a degraded 200-with-empty-payload look like a real negative.

## Why this matters beyond uptime

A study pinned to a snapshot hash re-runs byte-identically. A study that
calls a live API cannot, because the data underneath it moves, so its results
can only be checked against a record of what the API returned at the time.
Moving a source into Tier 1 changes what kind of claim a result is: from one
that rests on the original run having been performed correctly, to one that
any reader can reproduce and hash-compare independently.

That is the reason for the tiering, and the reason Tier 3 sources are
hardened rather than frozen: for an evidence lane whose value is being
current, reproducibility is bought at too high a price.
