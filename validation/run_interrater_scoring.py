"""
Inter-rater study scoring harness — scores one participant's verdict sheet
against the frozen ground truth in validation/interrater_lists.json.

Pre-registered in validation/interrater_study_protocol.md (v1, 2026-08-04).
This is an EXTERNAL study instrument: results go to
validation/interrater_results/ and are never merged with internal
engineering acceptance artifacts.

LABEL GUARD: runs ONLY under --label interrater_scoring.

Verdict sheet format (one JSON file per participant per list):
{
  "participant_id": "P03",
  "arm": "assisted" | "unassisted",
  "list_id": "A1",
  "minutes": 14.5,
  "responses": [
    {"drug_name": "Asprin", "flagged": true,
     "flag_reason": "name not resolvable / possible typo"},
    {"drug_name": "Dexamethasone", "flagged": false}
  ]
}

Usage:
    python3 -m validation.run_interrater_scoring --label interrater_scoring \
        --sheet path/to/sheet.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED_LABEL = "interrater_scoring"
FORBIDDEN_LABELS = {"benchmark_v2", "audit_trap_benchmark", "engineering_acceptance"}

_HERE = os.path.dirname(os.path.abspath(__file__))
LISTS_JSON = os.path.join(_HERE, "interrater_lists.json")
RESULTS_DIR = os.path.join(_HERE, "interrater_results")

# Verdicts in the frozen ground truth that count as "this drug must be
# flagged" for trap-recall scoring.
FLAG_STATUSES = {"unresolved", "absent"}


def _load_lists() -> dict:
    with open(LISTS_JSON, encoding="utf-8") as fh:
        return json.load(fh)


def score_sheet(sheet: dict) -> dict:
    lists_doc = _load_lists()
    lst = next((l for l in lists_doc["lists"]
                if l["list_id"] == sheet["list_id"]), None)
    if lst is None:
        raise SystemExit(f"unknown list_id {sheet['list_id']!r} — not in frozen lists")
    if not lst.get("ground_truth"):
        raise SystemExit(
            f"list {lst['list_id']} has no frozen ground_truth — run the freeze "
            "step in interrater_lists.json before scoring participants"
        )

    truth = {v["drug_name"]: v for v in lst["ground_truth"]["verdicts"]}
    responses = {r["drug_name"]: r for r in sheet.get("responses", [])}

    seeded = {s["drug_name"] for s in lst.get("seeded_items", [])
              if s["role"] == "seeded_trap"}
    controls = {c["drug_name"] for c in lst.get("controls", [])}

    rows = []
    traps_hit = 0
    for name in sorted(seeded):
        truth_status = (truth.get(name) or {}).get("status")
        # A seeded item only counts against the participant if the frozen
        # ground truth confirms the trap was actually live at freeze time.
        live = truth_status in FLAG_STATUSES
        flagged = bool((responses.get(name) or {}).get("flagged"))
        hit = live and flagged
        traps_hit += 1 if hit else 0
        rows.append({"drug_name": name, "role": "seeded_trap",
                     "truth_status": truth_status, "trap_live": live,
                     "participant_flagged": flagged, "hit": hit})

    false_flags = 0
    for name in sorted(controls):
        truth_status = (truth.get(name) or {}).get("status")
        truth_clean = truth_status == "found" and not (
            truth.get(name) or {}).get("flags")
        flagged = bool((responses.get(name) or {}).get("flagged"))
        ff = truth_clean and flagged
        false_flags += 1 if ff else 0
        rows.append({"drug_name": name, "role": "control",
                     "truth_status": truth_status, "truth_clean": truth_clean,
                     "participant_flagged": flagged, "false_flag": ff})

    live_traps = sum(1 for r in rows
                     if r["role"] == "seeded_trap" and r["trap_live"])
    clean_controls = sum(1 for r in rows
                         if r["role"] == "control" and r.get("truth_clean"))

    return {
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "label": REQUIRED_LABEL,
        "participant_id": sheet.get("participant_id"),
        "arm": sheet.get("arm"),
        "list_id": sheet["list_id"],
        "minutes": sheet.get("minutes"),
        "metrics": {
            "live_seeded_traps": live_traps,
            "traps_flagged": traps_hit,
            "trap_recall": (traps_hit / live_traps) if live_traps else None,
            "clean_controls": clean_controls,
            "controls_false_flagged": false_flags,
            "false_flag_rate": (false_flags / clean_controls) if clean_controls else None,
        },
        "rows": rows,
    }


_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_component(value, field: str) -> str:
    """Filename-safe sheet field: allowlisted chars only, never empty.

    Sheet fields are untrusted input; dots/slashes/separators are stripped so
    a crafted value cannot become a path segment.
    """
    cleaned = _SAFE_COMPONENT_RE.sub("_", str(value or ""))[:64].strip("._-")
    if not cleaned:
        raise SystemExit(
            f"sheet field {field!r} is not usable in an output filename"
        )
    return cleaned


def _output_path(result: dict) -> str:
    """Resolve the artifact path and PROVE it stays inside RESULTS_DIR.

    A traversal payload in any sheet field must never let a study artifact
    escape into validation/ engineering files (the isolation the protocol
    promises is enforced here, at write time, not by convention).
    """
    name = "{}_{}_{}.json".format(
        _safe_component(result.get("participant_id"), "participant_id"),
        _safe_component(result.get("arm"), "arm"),
        _safe_component(result.get("list_id"), "list_id"),
    )
    base = os.path.realpath(RESULTS_DIR)
    resolved = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([base, resolved]) != base:
        raise SystemExit("output path escapes interrater_results/ — refused")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--sheet", required=True,
                        help="path to one participant verdict sheet JSON")
    args = parser.parse_args()

    if args.label in FORBIDDEN_LABELS or args.label != REQUIRED_LABEL:
        print(
            f"[interrater] REFUSED: scoring runs ONLY under label "
            f"{REQUIRED_LABEL!r}; got {args.label!r}. Study results are "
            "external evidence and never merge with engineering artifacts.",
            file=sys.stderr,
        )
        return 2

    with open(args.sheet, encoding="utf-8") as fh:
        sheet = json.load(fh)
    result = score_sheet(sheet)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = _output_path(result)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    m = result["metrics"]
    print(f"[interrater] {result['participant_id']} {result['arm']} "
          f"{result['list_id']}: trap_recall={m['trap_recall']} "
          f"false_flags={m['controls_false_flagged']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
