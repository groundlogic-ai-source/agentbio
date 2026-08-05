"""
Benchmark v2 — Amendment-1 property-based case screen.

Reduces the v1 50-case list to the screened v2 list (target band n=35-40)
using ONLY disease/target properties — never v1 outcomes:

  (a) disease resolves to a specific, non-umbrella EFO/MONDO term;
  (b) >= 1 Open Targets associated target passes the pipeline's Stage-1 gate;
  (c) >= 1 considered target yields a non-empty production union pool.

Amendment 3 (2026-08-05) reinterprets (c) as the v2 multi-source union pool
under production settings (repurposing_only=True), tiered ChEMBL pools having
been superseded by the Amendment-2 union architecture.

Data-availability discipline (pre-reg item 14): a source failure NEVER counts
as a biological absence. ChEMBL/OT probes run up front; any probe failure or
per-case lookup failure marks the case INDETERMINATE and the screen exits 3
(retry later) rather than writing a partial list.

Usage:
    python3 -m validation.screen_v2_cases

Exit codes: 0 = list written; 3 = required data unavailable (retry later);
            2 = misuse.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources.open_targets import (  # noqa: E402
    search_disease_efo,
    get_ot_canonical_disease_name,
    get_disease_descendant_count,
    get_target_disease_score,
)
from data_sources.multisource_candidates import (  # noqa: E402
    collect_target_candidates,
)

_DIR = os.path.dirname(os.path.abspath(__file__))
SCREEN_VERSION = "v2-screen-1"
SOURCE_LIST = os.path.join(_DIR, "benchmark_case_list.json")
OUT_JSON = os.path.join(_DIR, "benchmark_case_list_v2.json")

# Pre-committed screen parameters (Amendment 3, 2026-08-05 — frozen BEFORE the
# screen ever ran; changing them after a written list invalidates the run).
DESCENDANT_CAP = 50          # above => umbrella "group of disorders" term
ASSOC_GATE = 0.1             # mirrors the pipeline Stage-1 OT gate (Path A)
NAME_JACCARD_MIN = 0.6       # OT canonical name vs case indication name
POOL_CHECK_MAX_TARGETS = 10  # considered-set prefix checked for pool non-emptiness
TARGET_BAND = (35, 40)       # Amendment-1 target band (disclosed, not enforced)


class ScreenDataUnavailable(Exception):
    """A source lookup failed transiently — NEVER interpreted as absence."""


def _norm(s: str) -> str:
    return " ".join(
        "".join(ch.lower() if ch.isalnum() else " " for ch in (s or "")).split()
    )


def _name_match(a: str, b: str) -> bool:
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= NAME_JACCARD_MIN


def ot_healthy() -> bool:
    """Open Targets liveness probe. A term that must resolve is used so that an
    outage is distinguishable from a genuine no-match."""
    try:
        return bool(search_disease_efo("cystic fibrosis"))
    except Exception:
        return False


def _chembl_healthy() -> bool:
    try:
        from validation.run_benchmark import _chembl_healthy as probe
    except ImportError:  # direct (non-package) execution
        from run_benchmark import _chembl_healthy as probe
    return probe()


def screen_case(case: dict[str, Any]) -> tuple[str, str]:
    """-> (verdict, reason); verdict in {"pass", "exclude"}.

    Raises ScreenDataUnavailable when a lookup fails transiently."""
    ind = case["ind_name"]
    stored_efo = case.get("efo_id") or ""

    try:
        resolved = search_disease_efo(ind)
    except Exception as e:
        raise ScreenDataUnavailable(f"OT search failed for {ind}: {e}")
    if not resolved:
        return "exclude", "efo_unresolved"
    if stored_efo and resolved != stored_efo:
        return ("exclude",
                f"efo_resolution_mismatch (stored {stored_efo}, now {resolved})")

    try:
        canonical = get_ot_canonical_disease_name(resolved)
        descendants = get_disease_descendant_count(resolved)
        targets = get_target_disease_score(resolved)
    except Exception as e:
        raise ScreenDataUnavailable(f"OT metadata failed for {ind}: {e}")
    if canonical is None or descendants is None or targets is None:
        raise ScreenDataUnavailable(f"OT metadata incomplete for {ind}")
    if not _name_match(ind, canonical):
        return "exclude", f"efo_name_mismatch (OT canonical: {canonical!r})"
    if descendants > DESCENDANT_CAP:
        return ("exclude",
                f"umbrella_term ({descendants} descendants > {DESCENDANT_CAP})")

    gated = [t for t in targets
             if (t.get("association_score") or 0) >= ASSOC_GATE
             and t.get("uniprot_id")]
    if not gated:
        return "exclude", "no_ot_genetic_target"

    for t in gated[:POOL_CHECK_MAX_TARGETS]:
        try:
            pool = collect_target_candidates(
                uniprot_id=t["uniprot_id"],
                gene=t.get("target_symbol") or "",
                disease_name=ind,
                ot_score=t.get("association_score"),
                target_discovery_method="genetic_association",
                repurposing_only=True,
            )
        except Exception as e:
            raise ScreenDataUnavailable(
                f"pool check failed for {ind}/{t.get('target_symbol')}: {e}")
        if pool.get("candidates"):
            return "pass", f"pool via {t.get('target_symbol')}"
    return "exclude", "empty_union_pool"


def main() -> int:
    if not _chembl_healthy() or not ot_healthy():
        print("[screen] FATAL: ChEMBL/OT probe failed — exit 3 "
              "(retry when sources are healthy)")
        return 3

    with open(SOURCE_LIST) as f:
        cases = json.load(f)["primary"]

    results: list[tuple[dict, str, str]] = []
    indeterminate: list[str] = []
    for c in cases:
        try:
            verdict, reason = screen_case(c)
        except ScreenDataUnavailable as e:
            indeterminate.append(f"{c['drug_name']} / {c['ind_name']}: {e}")
            continue
        results.append((c, verdict, reason))

    if indeterminate:
        print(f"[screen] {len(indeterminate)} case(s) indeterminate "
              "(data unavailable, NOT excluded):")
        for line in indeterminate:
            print("  -", line)
        print("[screen] refusing to write a partial list — exit 3")
        return 3

    primary = [c for c, v, _ in results if v == "pass"]
    excluded = [{**c, "screen_reason": r}
                for c, v, r in results if v == "exclude"]
    n = len(primary)
    payload = {
        "screen_version": SCREEN_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_list": os.path.basename(SOURCE_LIST),
        "params": {
            "descendant_cap": DESCENDANT_CAP,
            "assoc_gate": ASSOC_GATE,
            "name_jaccard_min": NAME_JACCARD_MIN,
            "pool_check_max_targets": POOL_CHECK_MAX_TARGETS,
            "pool_mode": "production union (repurposing_only=True)",
        },
        "n_source": len(cases),
        "n_pass": n,
        "screen_pass_rate": round(n / len(cases), 4) if cases else None,
        "target_band": list(TARGET_BAND),
        "band_met": TARGET_BAND[0] <= n <= TARGET_BAND[1],
        "primary": primary,
        "excluded": excluded,
    }
    tmp = OUT_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, OUT_JSON)

    print(f"[screen] {n}/{len(cases)} cases pass "
          f"({payload['screen_pass_rate']:.1%} funnel feasibility)")
    for c, v, r in results:
        if v == "exclude":
            print(f"  excluded: {c['drug_name']} / {c['ind_name']} — {r}")
    if not payload["band_met"]:
        print(f"[screen] DISCLOSURE: n={n} outside the Amendment-1 target band "
              f"{TARGET_BAND} — must be stated in the v2 report headline.")
    print(f"[screen] wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
