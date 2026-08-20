"""Machine-v2 acceptance test against the Study C v1 misses.

Post-hoc measurement (no pipeline re-run, no frozen artifact touched): with
the machine-v2 lanes ON (Path D pathway-neighbor universe expansion +
chemist mechanism-only pool supplement), how many of v1's 15 absent/unresolved
confirmed positives would now be reachable?

Two rescue tests per missed positive:
  universe_rescued  — the drug's ChEMBL mechanism target now appears in the
                      disease's select_for_disease universe (Path D), with
                      its rank and discovery method recorded
  pool_rescued      — for biologics: the drug's mechanism target is among the
                      disease's top-5 selected rows AND the mechanism-only
                      lane returns the drug for that target's UniProt ID

Also asserts the v2 universe is a SUPERSET of the v1 universe (lanes only
add) per disease, using validation/.studyc_rescue_cache.json as the v1
baseline. Per-disease row caches make the script resumable.

Output: validation/machine_v2_acceptance.json + console table.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.target_selection import select_for_disease  # noqa: E402
from data_sources.chembl import get_mechanism_only_approved_drugs  # noqa: E402

AUTOPSY = ROOT / "validation" / "studyc_miss_autopsy.json"
V1_CACHE = ROOT / "validation" / ".studyc_rescue_cache.json"
CACHE = ROOT / "validation" / ".machine_v2_acceptance_cache.json"
OUT = ROOT / "validation" / "machine_v2_acceptance.json"

K_PRODUCTION = 5  # main_graph TOP_K_TARGETS default

# Bump whenever Path D lane semantics change — stale per-disease rows are
# discarded rather than silently compared across machine versions.
_CACHE_CONFIG = {"lanes": "v2", "sources": "genetic+literature",
                 "max_added": 10}


def _disease_rows(disease: str, cache: dict) -> list[dict]:
    if disease in cache:
        return cache[disease]
    rows = select_for_disease(disease)
    slim = [{
        "target_symbol": r.get("target_symbol"),
        "uniprot_id": r.get("uniprot_id"),
        "target_discovery_method": r.get("target_discovery_method"),
        "rank": i,
    } for i, r in enumerate(rows, 1)]
    cache[disease] = slim
    cache["_config"] = _CACHE_CONFIG
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True, default=str))
    return slim


def main() -> None:
    autopsy = json.loads(AUTOPSY.read_text())
    v1_universe = (json.loads(V1_CACHE.read_text())
                   if V1_CACHE.exists() else {})
    misses = [r for r in autopsy["rows"]
              if r["row_kind"] == "confirmed_positive"
              and r["pool_status"] != "found"]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    if cache.get("_config") != _CACHE_CONFIG:
        if cache:
            print("acceptance cache config mismatch — rebuilding from scratch")
        cache = {}

    diseases = sorted({m["disease_name"] for m in misses})
    rows_by_disease = {d: _disease_rows(d, cache) for d in diseases}

    print("\n== v1 vs v2 universe sizes ==")
    superset_ok = True
    for d in diseases:
        v2_syms = {str(r["target_symbol"]).upper() for r in rows_by_disease[d]}
        v1_syms = {str(s).upper() for s in v1_universe.get(d, [])}
        n_new = len(v2_syms - v1_syms)
        missing = v1_syms - v2_syms
        if missing:
            superset_ok = False
        n_neighbors = sum(1 for r in rows_by_disease[d]
                          if r["target_discovery_method"] == "pathway_neighbor")
        print(f"{d[:30]:30s} v1={len(v1_syms):3d} v2={len(v2_syms):3d} "
              f"(+{n_new}, {n_neighbors} pathway-neighbor) "
              f"{'LOST: ' + str(sorted(missing)) if missing else 'superset OK'}")

    print("\n== per-miss rescue ==")
    out_rows = []
    for m in misses:
        disease = m["disease_name"]
        drug = m["drug_name"]
        rows = rows_by_disease[disease]
        by_symbol = {str(r["target_symbol"]).upper(): r for r in rows}
        drug_targets = [str(t).upper() for t in (m.get("mechanism_targets") or [])]

        hit = next((by_symbol[t] for t in drug_targets if t in by_symbol), None)
        universe_rescued = hit is not None

        pool_rescued = False
        pool_detail = ""
        if m["miss_class"] == "biologic_structural" and m.get("chembl_id"):
            top_rows = rows[:K_PRODUCTION]
            top_hit = next(
                (r for r in top_rows
                 if str(r["target_symbol"]).upper() in drug_targets), None)
            if top_hit and top_hit.get("uniprot_id"):
                try:
                    mech = get_mechanism_only_approved_drugs(
                        top_hit["uniprot_id"])
                    ids = {c.get("molecule_chembl_id") for c in mech}
                    pool_rescued = m["chembl_id"] in ids
                    pool_detail = (f"target {top_hit['target_symbol']} rank "
                                   f"{top_hit['rank']}; mechanism lane "
                                   f"{'CONTAINS' if pool_rescued else 'lacks'} "
                                   f"{drug}")
                except Exception as exc:  # noqa: BLE001
                    pool_detail = f"mechanism lane error: {exc}"
            else:
                pool_detail = ("drug target not among top-"
                               f"{K_PRODUCTION} selected rows")
            time.sleep(0.2)

        out_rows.append({
            "disease_name": disease, "drug_name": drug,
            "miss_class": m["miss_class"],
            "universe_rescued": universe_rescued,
            "universe_rank": hit["rank"] if hit else None,
            "universe_method": hit["target_discovery_method"] if hit else None,
            "pool_rescued": pool_rescued,
            "pool_detail": pool_detail,
        })
        print(f"{disease[:24]:24s} {drug:22s} {m['miss_class']:20s} "
              f"universe={'YES rank ' + str(hit['rank']) + ' (' + str(hit['target_discovery_method']) + ')' if hit else 'no':42s} "
              f"pool={'YES' if pool_rescued else ('-' if not pool_detail else 'no')}",
              flush=True)

    missing_baseline = [d for d in diseases if d not in v1_universe]
    n_uni = sum(1 for r in out_rows if r["universe_rescued"])
    n_pool = sum(1 for r in out_rows if r["pool_rescued"])
    payload = {
        "contract": "machine-v2-acceptance-v1",
        "based_on_autopsy": "validation/studyc_miss_autopsy.json",
        "universe_superset_of_v1": superset_ok,
        "misses_evaluated": len(out_rows),
        "universe_rescued": n_uni,
        "pool_rescued": n_pool,
        "either_rescued": sum(1 for r in out_rows
                              if r["universe_rescued"] or r["pool_rescued"]),
        "missing_v1_baseline": missing_baseline,
        "rows": out_rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              default=str) + "\n")
    print("\n== summary ==")
    print(f"universe superset of v1: {superset_ok}")
    print(f"universe-rescued: {n_uni}/{len(out_rows)}")
    print(f"pool-rescued (biologics): {n_pool}")
    print(f"either: {payload['either_rescued']}/{len(out_rows)}")
    if missing_baseline or not superset_ok:
        print("ACCEPTANCE INTEGRITY FAILURE: missing v1 baseline for "
              f"{missing_baseline or '—'}; superset_ok={superset_ok}")
        sys.exit(1)


if __name__ == "__main__":
    main()
