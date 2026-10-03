"""Screen DISEASES before spending a pipeline run on one.

WHY THIS EXISTS
---------------
Five flagship attempts died, and every one was knowable before the expensive
step:

  RTH-beta       resmetirom x RTHb was already published (3 Europe PMC records)
  Achondroplasia infigratinib was already in phase 3 for it
  AKT2           independent convergence on an idea we already knew
  Cystinosis     3 of 5 targets were co-deletion artifacts on chromosome 17
  Niemann-Pick C eliglustat cannot cross the blood-brain barrier

A full run costs hours, can fail on any of ~12 providers, and in one case took
eight attempts. The gates built for those failures compute most of the same
signal from Stage-1 context alone, in seconds, with no LLM spend. This module
runs them as a pre-filter.

WHAT IT DOES AND DOES NOT DO
----------------------------
It screens DISEASES. It does not name, rank or evaluate drugs -- that is the
pipeline's job, and doing it here would mean the run was no longer blind and
the credit for any hit would not belong to AgentBio.

It deliberately emits no composite score. This codebase has just finished
removing an uncalibrated 0.50 constant that was competing with measured
evidence; inventing a second uncalibrated number to rank diseases would repeat
exactly that mistake. Findings are reported as counts and named conditions, and
the ordering key is a lexicographic tuple of those counts, not a magic weight.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional

from agents.target_selection import DiseaseNotInUniverse, preflight_for_disease
from data_sources.chembl import (
    _fetch_molecule_meta,
    get_approved_drugs_for_target,
)
from data_sources.tissue_exposure import requires_cns_exposure

#: Discovery methods whose association is MEASURED rather than the provisional
#: PROCESS_EVIDENCE_ASSOC_SCORE placeholder. Mirrors
#: agents.reviewer._MEASURED_ASSOCIATION_METHODS deliberately: a target this
#: screen counts as real must be one the Reviewer would also promote on.
MEASURED_METHODS = {"genetic_association"}

VERDICT_NOT_IN_UNIVERSE = "NOT_IN_UNIVERSE"
VERDICT_RESOLUTION_FAILED = "RESOLUTION_FAILED"
VERDICT_NO_MEASURED_TARGET = "NO_MEASURED_TARGET"
VERDICT_ALL_MEASURED_POSITIONAL = "ALL_MEASURED_TARGETS_POSITIONAL"
#: Distinct from the above on purpose. A first version reported both as
#: ALL_MEASURED_TARGETS_POSITIONAL, so a batch screen showed that verdict
#: beside a positional-flag count of ZERO -- Ensembl had returned a transient
#: 500 on one gene, the shared circuit breaker opened for 30s, and every
#: subsequent lookup was rejected unattempted. A provider outage was being
#: reported as a finding about the biology, which is the precise error this
#: codebase keeps removing.
VERDICT_POSITIONAL_UNKNOWN = "POSITIONAL_RISK_UNKNOWN"
VERDICT_CAUSAL_TARGET_ONLY = "CAUSAL_TARGET_ONLY"
VERDICT_VIABLE = "VIABLE"


def _target_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        symbol = str(row.get("target_symbol") or "").strip()
        if not symbol:
            continue
        risk = row.get("positional_association_risk") or {}
        out.append({
            "target_symbol": symbol.upper(),
            "discovery_method": str(row.get("target_discovery_method") or ""),
            "ot_association_score": row.get("ot_association_score"),
            "uniprot_id": str(row.get("uniprot_id") or "").strip(),
            "measured": str(row.get("target_discovery_method") or "").strip().lower()
            in MEASURED_METHODS,
            "positional_flagged": bool(risk.get("flagged")),
            "positional_assessed": bool(risk.get("assessed")),
            "positional_neighbour": risk.get("nearest_higher_ranked_target"),
            "positional_distance_bp": risk.get("distance_bp"),
        })
    return out


def _count_systemic(approved_drugs: list[dict[str, Any]]) -> Optional[int]:
    """How many of a target's approved ligands can actually reach the body.

    The raw ligand count overstates the shelf. NR3C1 carries 65 approved drugs,
    which is why Duchenne muscular dystrophy was picked -- but most are topical
    or inhaled corticosteroids, and the run promoted four of them for a
    systemic muscle disease. Fluticasone propionate has ~1% oral
    bioavailability by design.

    Counting route-adjusted gives the shelf that exists for a systemic disease,
    using the same ChEMBL flags the reviewer's route gate uses, at screen time
    instead of after a run.
    """
    ids = [str(d.get("molecule_chembl_id") or "").strip()
           for d in approved_drugs]
    ids = [i for i in ids if i]
    if not ids:
        return None
    meta = _fetch_molecule_meta(ids)
    if not meta:
        return None
    systemic = 0
    for row in meta.values():
        if bool(row.get("route_oral")) or bool(row.get("route_parenteral")):
            systemic += 1
    return systemic


def screen_disease(disease_name: str) -> dict[str, Any]:
    """Run the cheap Stage-1 checks for one disease.

    Returns a findings envelope. ``verdict`` is a named condition, never a
    score, and ``usable_targets`` is the count that actually matters: targets
    with a measured disease association that are not explained by chromosomal
    position.
    """
    name = str(disease_name or "").strip()
    base: dict[str, Any] = {
        "disease_name": name,
        "verdict": VERDICT_RESOLUTION_FAILED,
        "in_universe": False,
        "targets": [],
        "n_targets": 0,
        "n_measured": 0,
        "n_positional_flagged": 0,
        "usable_targets": [],
        "n_usable": 0,
        "requires_cns_exposure": requires_cns_exposure(name),
        "notes": [],
    }
    if not name:
        base["notes"].append("No disease name supplied.")
        return base

    try:
        rows = preflight_for_disease(name)
    except DiseaseNotInUniverse:
        base["verdict"] = VERDICT_NOT_IN_UNIVERSE
        base["notes"].append(
            "Not in the Orphanet / WHO-NTD universe this system covers, so "
            "select_for_disease would raise before any run started.")
        return base
    except Exception as e:  # noqa: BLE001 — a screen must survive one bad row
        base["notes"].append(f"Preflight failed: {type(e).__name__}: {e}")
        return base

    targets = _target_rows(rows)
    measured = [t for t in targets if t["measured"]]
    flagged = [t for t in targets if t["positional_flagged"]]
    # A target whose coordinates could not be retrieved is NOT cleared. A first
    # version counted it as usable, and a rate-limited Wilson disease screen
    # reported five clean targets when nothing had been checked.
    usable = [t for t in measured
              if t["positional_assessed"] and not t["positional_flagged"]]

    # The question criterion 1 actually asks: is there a usable intervention
    # point OTHER than the broken gene, carrying real evidence?
    #
    # Counting usable targets alone does not answer it. Cystinosis reports CTNS
    # (0.890, the causal transporter) plus SLC66A1 at 0.122 -- barely above the
    # 0.1 admission floor -- and two usable targets looks identical to a disease
    # with two strong ones. Rather than invent a cutoff, the strongest
    # non-causal association is reported as a number and used for ordering, so
    # the margin is visible instead of buried in a verdict.
    causal = measured[0] if measured else None
    alternatives = [t for t in usable
                    if not causal or t["target_symbol"] != causal["target_symbol"]]
    alternatives.sort(key=lambda t: -(t["ot_association_score"] or 0.0))

    # A target with almost no approved ligands cannot yield a NOVEL pair --
    # there is nothing untried to find. This is what actually killed the last
    # three hunts, and association strength alone does not show it:
    #
    #   Friedreich ataxia  NFE2L2  2 candidates, both already FA drugs
    #   RTH-beta           THRB    thyroid analogues only, all standard or legacy
    #   AATD               ELANE   a handful of elastase inhibitors
    #
    # versus DRD2, which carried 168. Repurposing needs somewhere to look.
    for alt in alternatives:
        try:
            env = get_approved_drugs_for_target(alt["uniprot_id"] or "")
            alt["approved_drug_count"] = env.get("approved_drug_count")
            alt["systemic_drug_count"] = _count_systemic(
                env.get("approved_drugs") or [])
        except Exception:  # noqa: BLE001 — unknown count, never a claim
            alt["approved_drug_count"] = None
            alt["systemic_drug_count"] = None

    best_alt = alternatives[0] if alternatives else None
    # Richest usable alternative: the one with somewhere to actually look.
    with_drugs = [a for a in alternatives if (a.get("approved_drug_count") or 0) > 0]
    with_drugs.sort(key=lambda t: -(t.get("approved_drug_count") or 0))
    richest_alt = with_drugs[0] if with_drugs else None

    base.update({
        "in_universe": True,
        "targets": targets,
        "n_targets": len(targets),
        "n_measured": len(measured),
        "n_positional_flagged": len(flagged),
        "n_positional_unknown": len(
            [t for t in measured if not t["positional_assessed"]]),
        "usable_targets": [t["target_symbol"] for t in usable],
        "n_usable": len(usable),
        "causal_target": causal["target_symbol"] if causal else None,
        "causal_assoc": causal["ot_association_score"] if causal else None,
        "best_alternative_target": best_alt["target_symbol"] if best_alt else None,
        "best_alternative_assoc": (
            best_alt["ot_association_score"] if best_alt else None),
        "richest_alternative_target": (
            richest_alt["target_symbol"] if richest_alt else None),
        "richest_alternative_drugs": (
            richest_alt.get("approved_drug_count") if richest_alt else None),
        "richest_alternative_assoc": (
            richest_alt.get("ot_association_score") if richest_alt else None),
        "richest_alternative_systemic": (
            richest_alt.get("systemic_drug_count") if richest_alt else None),
        # Whether the disease has an APPROVED therapy, as distinct from an
        # off-label standard of care. The two were conflated through five
        # hunts and behave differently: an approved drug means pharma is
        # already there and prior-art density is high (DMD had vamorolone, NPC
        # had arimoclomol), whereas off-label-only means thin attention, and
        # thin attention is the only place an unclaimed pair survives.
        "has_approved_treatment": (rows[0].get("has_approved_treatment")
                                   if rows else None),
        "approved_drug_names": (rows[0].get("approved_drug_names") or []
                                if rows else []),
    })

    if not measured:
        base["verdict"] = VERDICT_NO_MEASURED_TARGET
        base["notes"].append(
            "Every target was admitted by pharmacological precedent or pathway "
            "neighbourhood, so each carries the provisional 0.50 placeholder "
            "rather than a measured association. A pool built on these fills "
            "with drugs that treat the disease's symptoms -- the reason 37 of "
            "40 top Niemann-Pick candidates were antipsychotics.")
    elif not usable and not [t for t in measured if t["positional_flagged"]]:
        base["verdict"] = VERDICT_POSITIONAL_UNKNOWN
        base["notes"].append(
            "No measured target could be positionally checked — coordinates "
            "were unavailable, not clean. Re-run this disease on its own "
            "before drawing any conclusion: the shared circuit breaker rejects "
            "lookups unattempted for 30s after three failures, so one transient "
            "Ensembl error can blank an entire disease in a batch.")
    elif not usable:
        base["verdict"] = VERDICT_ALL_MEASURED_POSITIONAL
        base["notes"].append(
            "Every measured target sits within the proximity window of a "
            "higher-ranked one, so the associations may be explained by "
            "chromosomal position rather than biology -- the cystinosis shape, "
            "where CTNS, SHPK and TRPV1 all lie inside ~98 kb.")
    elif len(usable) == 1 and measured and usable[0]["target_symbol"] == \
            measured[0]["target_symbol"]:
        # The RTH-beta trap. When the only usable target IS the causal gene,
        # the rational pharmacology is "supply a better ligand for the broken
        # protein" -- and every such ligand is already that disease's drug.
        # RTHb returned liothyronine, levothyroxine, tiratricol, dextrothyroxine
        # and resmetirom: standard care, legacy, withdrawn, or already
        # published. There was never a non-obvious hit to find.
        base["verdict"] = VERDICT_CAUSAL_TARGET_ONLY
        base["notes"].append(
            f"The only usable target ({usable[0]['target_symbol']}) is also the "
            "highest-ranked, i.e. the presumed causal gene. Diseases whose "
            "defect IS the drug target have a candidate space already "
            "exhausted by that target's known ligands.")
    else:
        base["verdict"] = VERDICT_VIABLE

    if flagged:
        detail = ", ".join(
            f"{t['target_symbol']} ({t['positional_distance_bp']:,} bp from "
            f"{t['positional_neighbour']})"
            for t in flagged if t["positional_distance_bp"] is not None)
        if detail:
            base["notes"].append(f"Positional risk: {detail}.")

    unassessed = [t["target_symbol"] for t in targets
                  if not t["positional_assessed"]]
    if unassessed:
        base["notes"].append(
            "Coordinates unavailable for " + ", ".join(unassessed) +
            " — positional risk is unknown for those, not cleared.")

    if base["requires_cns_exposure"]:
        base["notes"].append(
            "Neurological component identified: compartment exposure will gate "
            "promotion, so a candidate that cannot cross the blood-brain "
            "barrier is excluded no matter how potent. This is a constraint on "
            "the candidate space, not a reason to skip the disease.")

    return base


def rank_key(result: dict[str, Any]) -> tuple:
    """Order screens best-first. A tuple of counts, deliberately not a score.

    More usable targets first; then fewer positional artifacts; then diseases
    without a blood-brain-barrier constraint, since that constraint narrows the
    candidate space rather than disqualifying the disease.
    """
    return (
        # Off-label-only diseases first. An approved therapy means pharma is
        # already working the space and prior art is dense; off-label-only
        # means thin attention, which is the only condition under which an
        # unclaimed pair survives long enough to be found.
        0 if result.get("has_approved_treatment") is False else 1,
        # Then the ROUTE-ADJUSTED shelf. The raw ligand count overstates it:
        # NR3C1's 65 approved drugs are mostly topical or inhaled steroids,
        # and counting them whole is what put Duchenne at the top and promoted
        # four drugs that cannot reach muscle.
        -int(result.get("richest_alternative_systemic")
             or result.get("richest_alternative_drugs") or 0),
        -float(result.get("best_alternative_assoc") or 0.0),
        -int(result.get("n_usable") or 0),
        int(result.get("n_positional_flagged") or 0),
        1 if result.get("requires_cns_exposure") else 0,
        str(result.get("disease_name") or ""),
    )


def screen_many(disease_names: list[str]) -> list[dict[str, Any]]:
    results = [screen_disease(n) for n in disease_names]
    return sorted(results, key=rank_key)


def format_table(results: list[dict[str, Any]]) -> str:
    def num(value: Any) -> str:
        return f"{value:.3f}" if isinstance(value, (int, float)) else "—"

    lines = [
        f"{'disease':38s} {'verdict':26s} {'causal':>14s} "
        f"{'best alternative':>22s} {'richest alt (sys/all)':>22s} {'therapy':>9s} {'CNS':>4s}",
        "-" * 130,
    ]
    for r in results:
        causal = f"{r.get('causal_target') or '—'} {num(r.get('causal_assoc'))}"
        alt = (f"{r.get('best_alternative_target') or '—'} "
               f"{num(r.get('best_alternative_assoc'))}")
        n_drugs = r.get("richest_alternative_drugs")
        n_sys = r.get("richest_alternative_systemic")
        rich = (f"{r.get('richest_alternative_target') or '—'} "
                f"({n_sys if n_sys is not None else '—'}"
                f"/{n_drugs if n_drugs is not None else '—'})")
        approved = r.get("has_approved_treatment")
        label = "off-label" if approved is False else (
            "approved" if approved is True else "?")
        lines.append(
            f"{str(r['disease_name'])[:38]:38s} {r['verdict'][:26]:26s} "
            f"{causal:>14s} {alt:>22s} "
            f"{rich:>22s} {label:>9s} "
            f"{'yes' if r['requires_cns_exposure'] else 'no':>4s}")
    return "\n".join(lines)


def universe_sample(limit: int, offset: int = 0) -> list[str]:
    """Disease names straight from the Orphanet/NTD universe.

    Hand-picking was the weakest part of the method: roughly 25 diseases chosen
    from recall, which selects for the ones well-studied enough to be
    memorable -- and therefore most likely already claimed. The universe holds
    ~11,645, the screen costs seconds each and makes no LLM call, so there is
    no reason to trust recall over enumeration.
    """
    from agents.target_selection import _matchable_universe
    names = [str(d.get("name") or "").strip()
             for d in _matchable_universe()]
    names = [n for n in names if n]
    return names[offset:offset + limit]


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] == "--universe":
        limit = int(argv[1]) if len(argv) > 1 else 100
        offset = int(argv[2]) if len(argv) > 2 else 0
        names = universe_sample(limit, offset)
        print(f"# screening {len(names)} diseases from the universe "
              f"(offset {offset})", flush=True)
    else:
        names = argv
    if not names:
        print("usage: python disease_screen.py 'Disease A' 'Disease B' ...")
        print("       python disease_screen.py --universe N [OFFSET]")
        raise SystemExit(2)
    screened = screen_many(names)
    print(format_table(screened))
    print()
    for r in screened:
        if r["notes"]:
            print(f"== {r['disease_name']} ({r['verdict']})")
            for note in r["notes"]:
                print(f"   - {note}")
    if "--json" in names:
        print(json.dumps(screened, indent=2))
