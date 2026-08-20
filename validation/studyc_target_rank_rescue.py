"""Study C v1 rescue analysis: where did each missed drug's target rank?

For every v1 positive classified `target_not_selected` by the miss autopsy,
re-run select_for_disease (post-hoc forensic analysis over a COMPLETED study;
no pipeline stage, no frozen file touched) and find the rank of the drug's
ChEMBL mechanism targets in the full ranked target list. Answers the upgrade
question precisely: what top-K would have admitted the drug's target?

Per-disease selection results cache to validation/.studyc_rescue_cache.json,
so the script is resumable and re-runs are free.

Output: validation/studyc_target_rank_rescue.json + console summary.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.target_selection import select_for_disease  # noqa: E402

AUTOPSY = ROOT / "validation" / "studyc_miss_autopsy.json"
CACHE = ROOT / "validation" / ".studyc_rescue_cache.json"
OUT = ROOT / "validation" / "studyc_target_rank_rescue.json"


def _selected_rows(disease: str, cache: dict) -> list[str]:
    """Ordered target symbols for the disease (cached; select is expensive
    cold)."""
    if disease in cache:
        return cache[disease]
    rows = select_for_disease(disease)
    symbols = [str(r.get("target_symbol") or "") for r in rows]
    cache[disease] = symbols
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))
    return symbols


def main() -> None:
    autopsy = json.loads(AUTOPSY.read_text())
    misses = [r for r in autopsy["rows"]
              if r["row_kind"] == "confirmed_positive"
              and r["miss_class"] == "target_not_selected"]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    rows = []
    for m in misses:
        disease = m["disease_name"]
        symbols = _selected_rows(disease, cache)
        upper = [s.upper() for s in symbols]
        ranks = {}
        for t in m.get("mechanism_targets") or []:
            t = t.upper()
            if t in upper:
                ranks[t] = upper.index(t) + 1
        best = min(ranks.values()) if ranks else None
        rows.append({
            "disease_name": disease,
            "drug_name": m["drug_name"],
            "mechanism_targets": m.get("mechanism_targets"),
            "n_targets_considered": len(symbols),
            "target_ranks": ranks,
            "best_rank": best,
            "in_universe_at_all": best is not None,
        })
        print(f"{disease[:24]:24s} {m['drug_name']:22s} "
              f"best_rank={best} of {len(symbols)}", flush=True)
        time.sleep(0.2)

    rescued = {k: sum(1 for r in rows
                      if r["best_rank"] is not None and r["best_rank"] <= k)
               for k in (3, 5, 10, 25)}
    payload = {
        "contract": "studyc-target-rank-rescue-v1",
        "note": ("Post-hoc: rank of each missed positive's mechanism target "
                 "in the disease's full ranked target list. rescued_at_k = "
                 "misses whose best target rank <= K."),
        "misses": len(rows),
        "rescued_at_k": rescued,
        "never_in_universe": sum(1 for r in rows if not r["in_universe_at_all"]),
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              default=str) + "\n")
    print("\n== rescue-at-K ==")
    print(json.dumps(payload["rescued_at_k"], indent=2))
    print("never_in_universe:", payload["never_in_universe"])


if __name__ == "__main__":
    main()
