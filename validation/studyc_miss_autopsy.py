"""Study C v1 miss autopsy — WHY is each absent/unresolved drug not in its pool?

Post-hoc forensic analysis over the completed, frozen v1 artifacts. Runs no
pipeline stages and touches no frozen file: it reads the results rows and the
checkpoint pools, then classifies every non-found positive/negative against
live-but-cached ChEMBL lookups:

  name_resolution_gap   ChEMBL cannot resolve the drug name (even with a
                        curated alternate)
  biologic_structural   molecule_type is a biologic class — activity-data
                        pools structurally exclude it
  assay_pool_gap        drug HAS a ChEMBL mechanism against one of the
                        disease's selected top-3 targets, but no qualifying
                        activity row put it in the pool (Sapropterin class)
  target_not_selected   drug's mechanism targets exist, but none is among the
                        disease's selected top-3 targets
  no_mechanism_data     resolvable small molecule with no ChEMBL mechanism
  found                 pool-present (reported for completeness)

Output: validation/studyc_miss_autopsy.json + console summary. Re-runnable;
per-drug lookups cache to validation/.studyc_autopsy_cache.json.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_sources.chembl import (  # noqa: E402
    BASE_URL, _find_molecule_chembl_id, _get_json, get_molecule_data)

RESULTS = ROOT / "validation" / "triage_discrimination_studyc_results.json"
CKPT_GZ = (ROOT / "validation"
           / "triage_discrimination_studyc_checkpoint.jsonl.gz")
CACHE = ROOT / "validation" / ".studyc_autopsy_cache.json"
OUT = ROOT / "validation" / "studyc_miss_autopsy.json"

_BIOLOGIC_TYPES = {
    "antibody", "protein", "enzyme", "oligonucleotide", "oligosaccharide",
    "cell therapy", "gene therapy", "atc protein",
}

# Curated alternates for drugs the production resolver could not name-match.
_ALTERNATES = {
    "pentosan polysulfate": ["Pentosan polysulfate sodium"],
    "l-carnitine": ["Levocarnitine", "Carnitine"],
}


def _load_cache() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text())
    return {}


def _save_cache(c: dict) -> None:
    CACHE.write_text(json.dumps(c, indent=2, sort_keys=True, default=str))


def _resolve(name: str) -> str | None:
    mid = _find_molecule_chembl_id(name)
    if mid:
        return mid
    for alt in _ALTERNATES.get(name.casefold(), []):
        mid = _find_molecule_chembl_id(alt)
        if mid:
            return mid
    return None


def _mechanism_target_symbols(chembl_id: str) -> list[str]:
    """Gene symbols of this molecule's ChEMBL mechanism targets."""
    try:
        data = _get_json(f"{BASE_URL}/mechanism.json",
                         {"molecule_chembl_id": chembl_id, "limit": 50})
        mechs = data.get("mechanisms", [])
        if not mechs:
            data = _get_json(
                f"{BASE_URL}/mechanism.json",
                {"parent_molecule_chembl_id": chembl_id, "limit": 50})
            mechs = data.get("mechanisms", [])
    except Exception:
        return []
    symbols: set[str] = set()
    for m in mechs:
        tid = m.get("target_chembl_id")
        if not tid:
            continue
        try:
            t = _get_json(f"{BASE_URL}/target/{tid}.json")
        except Exception:
            continue
        for comp in t.get("target_components", []) or []:
            for syn in comp.get("target_component_synonyms", []) or []:
                if syn.get("synonym_type") == "GENE_SYMBOL" and syn.get("component_synonym"):
                    symbols.add(str(syn["component_synonym"]).upper())
        label = t.get("pref_name")
        if label and not symbols:
            symbols.add(str(label).upper())
    return sorted(symbols)


def _drug_facts(name: str, cache: dict) -> dict:
    key = name.casefold()
    if key in cache:
        return cache[key]
    chembl_id = _resolve(name)
    facts = {"chembl_id": chembl_id, "molecule_type": None,
             "max_phase": None, "targets": []}
    if chembl_id:
        md = get_molecule_data(name)
        facts["molecule_type"] = md.get("molecule_type")
        facts["max_phase"] = md.get("max_phase")
        facts["targets"] = _mechanism_target_symbols(chembl_id)
    cache[key] = facts
    _save_cache(cache)
    time.sleep(0.3)  # politeness; the codebase throttle handles the rest
    return facts


def _classify(status: str, facts: dict, selected: list[str]) -> tuple[str, str]:
    """-> (class, detail)"""
    if status == "found":
        return "found", ""
    if not facts["chembl_id"]:
        return "name_resolution_gap", "ChEMBL cannot resolve the name"
    mt = (facts["molecule_type"] or "").strip()
    if mt and mt.casefold() in _BIOLOGIC_TYPES:
        return "biologic_structural", f"molecule_type={mt}"
    targets = set(facts["targets"])
    if not targets:
        return "no_mechanism_data", f"molecule_type={mt or '?'}"
    overlap = sorted(targets & {s.upper() for s in selected})
    if overlap:
        return "assay_pool_gap", (
            f"mechanism targets selected target(s) {overlap} "
            f"but no qualifying activity row ({mt or '?'})")
    return "target_not_selected", (
        f"targets {sorted(targets)[:6]} not among selected {selected} "
        f"({mt or '?'})")


def main() -> None:
    results = json.loads(RESULTS.read_text())
    selected: dict[str, list[str]] = {}
    with gzip.open(CKPT_GZ, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("kind") == "pool":
                selected[rec["disease_name"]] = [
                    t["target_symbol"] for t in rec.get("per_target", [])]

    cache = _load_cache()
    rows = [r for r in results["rows"]
            if r["row_kind"] in ("confirmed_positive", "genuine_negative")]

    out_rows = []
    for r in rows:
        disease, drug = r["disease_name"], r["drug_name"]
        status = r["pool_status"]
        facts = _drug_facts(drug, cache) if status != "found" else {
            "chembl_id": None, "molecule_type": None, "targets": []}
        cls, detail = _classify(status, facts, selected.get(disease, []))
        out_rows.append({
            "disease_name": disease, "drug_name": drug,
            "row_kind": r["row_kind"], "pool_status": status,
            "rank": r["rank"], "miss_class": cls, "detail": detail,
            "chembl_id": facts.get("chembl_id"),
            "molecule_type": facts.get("molecule_type"),
            "mechanism_targets": facts.get("targets"),
            "selected_targets": selected.get(disease, []),
        })
        print(f"{disease[:24]:24s} {r['row_kind'][:4]} {status:10s} "
              f"{cls:20s} {drug}", flush=True)

    summary: dict[str, dict[str, int]] = {}
    for o in out_rows:
        bucket = summary.setdefault(o["row_kind"], {})
        bucket[o["miss_class"]] = bucket.get(o["miss_class"], 0) + 1

    payload = {
        "contract": "studyc-miss-autopsy-v1",
        "based_on_results_sha256": results["results_sha256"],
        "note": ("Post-hoc forensic analysis over frozen v1 artifacts; no "
                 "pipeline stage re-run, no frozen file modified."),
        "summary": summary,
        "rows": out_rows,
    }
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              default=str) + "\n")
    print("\n== summary ==")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
