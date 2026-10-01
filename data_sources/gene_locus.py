"""Is this target associated with the disease, or merely next door to it?

WHY THIS EXISTS
---------------
Genetic association is the pipeline's strongest target-selection signal, and it
cannot distinguish a gene that causes a disease from a gene that happens to sit
beside one. When the common mutation is a large deletion, every gene inside the
deleted interval inherits a real, statistically genuine association -- for
reasons of geography, not biology.

Cystinosis is the worked example. The common Northern European mutation is a
57-kb deletion that removes CTNS and extends into neighbouring sequence. Target
selection returned CTNS, SHPK and TRPV1. Their coordinates on chromosome 17:

    TRPV1   3,565,444 - 3,609,880
    SHPK    3,607,433 - 3,636,637
    CTNS    3,636,446 - 3,663,103

All three lie inside roughly 98 kb of contiguous sequence. TRPV1 looked like a
strong, well-drugged target with a real association; it is a capsaicin receptor
whose loss produces a sensory phenotype and has nothing to do with the renal
failure that defines the disease. A TRPV1 agonist proposed for a kidney disease
is the kind of claim that ends a conversation with a biologist.

The controls behave: Niemann-Pick type C returned NPC1 (chr18) and UGCG (chr9)
-- different chromosomes, so no positional explanation is available and the
associations must be functional.

WHAT THIS IS AND IS NOT
-----------------------
This is a DISCLOSURE, not a verdict on biology. Neighbouring genes are
sometimes genuinely related -- gene clusters, shared regulatory elements,
co-regulated operonic-like arrangements. Proximity raises a question a human
should answer; it does not settle one. The module therefore reports the
distance and the neighbour, and never asserts that an association is false.
"""

import re
import requests
from typing import Any, Optional

from cache.cache import get, set as cache_set, make_key
from data_sources.provider_request_policy import request as provider_request

BASE_URL = "https://rest.ensembl.org"

SCHEMA_VERSION = "gene-locus-v1"

_CACHE_VERSION = "gene_locus_v1"
_TTL_DAYS = 90
_TIMEOUT_SECONDS = 20
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

#: Distance within which a positional explanation is worth disclosing.
#:
#: Contiguous gene deletion syndromes typically span tens of kb to a few Mb.
#: 1 Mb is wide enough to catch the cystinosis interval (~98 kb) with margin,
#: and narrow enough that a flag still means something: the human genome
#: averages far more than one gene per megabase, so this does not flag
#: everything.
DEFAULT_PROXIMITY_BP = 1_000_000

#: Human nuclear chromosomes. An unplaced scaffold or patch name is not a
#: basis for a proximity claim.
_CANONICAL_CHROMOSOMES = {str(n) for n in range(1, 23)} | {"X", "Y", "MT"}


class LocusUnavailable(RuntimeError):
    """Coordinates could not be retrieved. Never cached, never a finding."""


def _canonical_chromosome(value: Any) -> Optional[str]:
    name = str(value or "").strip().upper()
    name = re.sub(r"^CHR", "", name)
    return name if name in _CANONICAL_CHROMOSOMES else None


def get_gene_locus(symbol: str) -> Optional[dict[str, Any]]:
    """Chromosome and span for an HGNC symbol, or None when unavailable.

    None means unknown. It never means "not nearby" -- a missing locus removes
    the ability to make a proximity claim rather than refuting one.
    """
    gene = str(symbol or "").strip()
    if not gene:
        return None

    cache_key = make_key(_CACHE_VERSION, gene.upper())
    cached = get(cache_key)
    if cached is not None:
        return cached or None

    try:
        # Through the shared throttle: Ensembl answers a breach of its ~15/s
        # ceiling with 429 + Retry-After, and an unthrottled burst made a whole
        # disease's coordinates "unavailable", which then read as no positional
        # risk. 404 passes through unraised because "Ensembl does not know this
        # symbol" is an answer, not an outage.
        resp = provider_request(
            "ensembl",
            requests.get,
            f"{BASE_URL}/lookup/symbol/homo_sapiens/{gene}",
            pass_through_statuses=frozenset({404}),
            headers={"Content-Type": "application/json"},
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 — unknown locus, never a positional claim
        return None

    if resp.status_code == 404:
        # A definitive "Ensembl does not know this symbol" is cacheable; an
        # outage is not. Distinguishing them is the same discipline applied to
        # ChEMBL absence-vs-outage elsewhere in this codebase.
        cache_set(cache_key, {}, ttl_days=_TTL_DAYS)
        return None
    if resp.status_code in _TRANSIENT_STATUSES or resp.status_code != 200:
        return None

    try:
        payload = resp.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    chromosome = _canonical_chromosome(payload.get("seq_region_name"))
    start, end = payload.get("start"), payload.get("end")
    if chromosome is None or not isinstance(start, int) or not isinstance(end, int):
        return None

    locus = {
        "symbol": gene.upper(),
        "chromosome": chromosome,
        "start": min(start, end),
        "end": max(start, end),
        "ensembl_id": payload.get("id"),
    }
    cache_set(cache_key, locus, ttl_days=_TTL_DAYS)
    return locus


def span_distance(a: dict[str, Any], b: dict[str, Any]) -> Optional[int]:
    """Base pairs between two gene spans, 0 if they overlap, None if unrelated.

    None when either locus is missing or they sit on different chromosomes --
    in which case no positional explanation exists and none is offered.
    """
    if not a or not b:
        return None
    if a.get("chromosome") != b.get("chromosome"):
        return None
    if a["start"] <= b["end"] and b["start"] <= a["end"]:
        return 0
    return (b["start"] - a["end"]) if b["start"] > a["end"] else (a["start"] - b["end"])


def positional_association_risk(
    targets: list[dict[str, Any]],
    *,
    proximity_bp: int = DEFAULT_PROXIMITY_BP,
) -> dict[str, dict[str, Any]]:
    """Flag targets whose association may be explained by chromosomal position.

    ``targets`` are dicts carrying ``target_symbol`` and, where known, an
    ``ot_association_score``. A target is flagged when it sits within
    ``proximity_bp`` of a HIGHER-scoring target on the same chromosome: the
    higher-scoring neighbour is the better candidate for the causal gene, and a
    large deletion removing it would confer association on everything alongside.

    Returns ``{symbol: disclosure}``. Every target gets an entry, so an absent
    flag is visibly "checked and not flagged" rather than "never looked at".
    """
    ranked = sorted(
        [t for t in (targets or []) if str(t.get("target_symbol") or "").strip()],
        key=lambda t: (t.get("ot_association_score") or 0.0),
        reverse=True,
    )
    loci = {
        str(t["target_symbol"]).upper(): get_gene_locus(str(t["target_symbol"]))
        for t in ranked
    }

    out: dict[str, dict[str, Any]] = {}
    for index, target in enumerate(ranked):
        symbol = str(target["target_symbol"]).upper()
        locus = loci.get(symbol)
        if locus is None:
            out[symbol] = {
                "schema_version": SCHEMA_VERSION,
                "assessed": False,
                "flagged": False,
                "reason": ("Genomic coordinates were unavailable, so no "
                           "positional check was possible. This is unknown, "
                           "not a clean result."),
            }
            continue

        nearest: Optional[tuple[str, int]] = None
        for higher in ranked[:index]:
            other = str(higher["target_symbol"]).upper()
            distance = span_distance(locus, loci.get(other) or {})
            if distance is None:
                continue
            if nearest is None or distance < nearest[1]:
                nearest = (other, distance)

        flagged = nearest is not None and nearest[1] <= proximity_bp
        out[symbol] = {
            "schema_version": SCHEMA_VERSION,
            "assessed": True,
            "flagged": flagged,
            "chromosome": locus["chromosome"],
            "nearest_higher_ranked_target": nearest[0] if nearest else None,
            "distance_bp": nearest[1] if nearest else None,
            "reason": (
                f"{symbol} lies {nearest[1]:,} bp from {nearest[0]} on "
                f"chromosome {locus['chromosome']}, which ranks higher for this "
                f"disease. A deletion removing {nearest[0]} could confer "
                f"association on {symbol} by proximity rather than function. "
                f"This is a disclosure for human judgement, not a finding that "
                f"the association is false."
                if flagged else
                "No higher-ranked target lies within the proximity window, so "
                "no positional explanation for this association is available."
            ),
        }
    return out
