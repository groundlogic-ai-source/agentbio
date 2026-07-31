"""
Miss classifier for the repoDB retrospective results.

Reads the per-case records produced by run_repodb_cases.py /
run_repodb_cases_smallmol.py and computes a machine-checkable miss class for
every case, so the breakdown stays true as results change (no hand-written
verdicts). Classes:

  hit                        — confirmed drug recovered in the reviewed list
  right_target_pool_gap      — the pipeline tried a target the drug is known to
                               act on (ChEMBL mechanism endpoint), but the drug
                               has NO qualifying Homo sapiens IC50/Ki assay at
                               confidence >= 8 → invisible to the activity pool
  pool_truncation            — drug HAS qualifying assays vs a tried target but
                               was absent from the returned pool (unexpected
                               given the approved-drug append rule; flagged)
  wrong_target               — none of the drug's ChEMBL mechanism targets was
                               among the tried targets
  biologic_not_addressable   — drug is not a small molecule; the ChEMBL
                               IC50/Ki activity pool can never contain it
  unresolved_no_mechanism    — drug has no ChEMBL mechanism records at all, so
                               target correctness cannot be checked from ChEMBL
  unverified_lookup_failure  — a ChEMBL lookup failed transiently during
                               classification; recoverability flags are NEVER
                               set from failed lookups and the case is NOT
                               version-stamped (retried on the next run)

Recoverability flags (used for the projected ceiling):
  recoverable_by_pool_fix       — right_target_pool_gap cases where the
                                  qualifying-assay check SUCCEEDED and found no
                                  qualifying assay
  recoverable_by_target_fix     — wrong_target cases where the drug's true
                                  mechanism target appears ANYWHERE in
                                  candidate_targets_considered (better ranking
                                  / larger K would have tried it)

Cache policy (per repo cache-poisoning convention): only SUCCESSFUL lookups are
cached (30d). Any API exception raises _TransientLookupError and is NOT cached.
Cache-key versions are bumped whenever lookup semantics change.

Entry points:
  classify_cases(cases)  — enrich a list of case dicts in place (used by both
                           harness main()s before flush/markdown); skips misses
                           already classified at the current CLASSIFIER_VERSION
  breakdown_lines(cases) — markdown lines for the per-file breakdown section
  python -m validation.miss_classifier — force re-classify both results JSONs
                           on disk and write validation/rediscovery_summary.md
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from typing import Any, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from cache.cache import get as cache_get, set as cache_set, make_key
from data_sources import chembl as _chembl
from data_sources.chembl import get_molecule_data

VALIDATION_DIR = os.path.dirname(os.path.abspath(__file__))
ENRICHED_CSV = os.path.join(_REPO_ROOT, "data_prep", "output", "enriched_dataset.csv")

# Bump when classification logic or any lookup helper changes semantics.
CLASSIFIER_VERSION = 2

POOL_GAP = "right_target_pool_gap"
POOL_TRUNC = "pool_truncation"
WRONG_TARGET = "wrong_target"
BIOLOGIC = "biologic_not_addressable"
UNRESOLVED = "unresolved_no_mechanism"
LOOKUP_FAIL = "unverified_lookup_failure"
HIT = "hit"


class _TransientLookupError(Exception):
    """A ChEMBL/API call failed; results must not be cached or acted upon."""


def _log(msg: str) -> None:
    print(f"[miss-classifier] {msg}", flush=True)


# ── Drug molecule-type fallback from the enriched repoDB CSV ─────────────────

_CSV_TYPES: Optional[dict[str, str]] = None


def _csv_molecule_types() -> dict[str, str]:
    global _CSV_TYPES
    if _CSV_TYPES is None:
        _CSV_TYPES = {}
        try:
            with open(ENRICHED_CSV, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = (row.get("drug_name") or "").strip().lower()
                    mtype = (row.get("chembl_molecule_type") or "").strip()
                    if name and mtype and name not in _CSV_TYPES:
                        _CSV_TYPES[name] = mtype
        except OSError:
            pass
    return _CSV_TYPES


def _molecule_type(drug_name: str) -> Optional[str]:
    """ChEMBL molecule_type, falling back to the enriched repoDB CSV column."""
    try:
        mol = get_molecule_data(drug_name)
        mtype = (mol.get("molecule_type") or "").strip()
        if mtype:
            return mtype
    except Exception:
        pass
    return _csv_molecule_types().get(drug_name.strip().lower()) or None


# ── ChEMBL mechanism helpers (success-only disk cache) ───────────────────────

def _target_gene_symbols(target_chembl_id: str) -> list[str]:
    """
    Resolve a ChEMBL target id to ALL gene symbols across its components
    (protein families/complexes have several). ChEMBL stores the synonym type
    as "GENE_SYMBOL" (underscore); falls back to pref_name when no gene-symbol
    synonym exists. Cached 30d on success only.
    """
    key = make_key("missclass_target_symbols_v2", target_chembl_id)
    cached = cache_get(key)
    if cached is not None:
        return list(cached)
    symbols: list[str] = []
    try:
        data = _chembl._get_json(f"{_chembl.BASE_URL}/target/{target_chembl_id}.json")
    except Exception as e:
        raise _TransientLookupError(f"target lookup {target_chembl_id}: {e}")
    for comp in data.get("target_components", []) or []:
        for syn in comp.get("target_component_synonyms", []) or []:
            if (syn.get("syn_type") or "").upper() in ("GENE_SYMBOL", "GENE SYMBOL"):
                val = syn.get("component_synonym")
                if val and val not in symbols:
                    symbols.append(val)
    if not symbols:
        pref = data.get("pref_name")
        if pref:
            symbols.append(pref)
    cache_set(key, symbols, ttl_days=30)
    return symbols


def _drug_mechanism_map(drug_name: str) -> dict[str, dict[str, Any]]:
    """
    {target_chembl_id: {symbols: [...], moa, action_type}} from the ChEMBL
    mechanism endpoint (molecule + parent-molecule fallback). Cached 30d on
    success only. Empty dict = drug legitimately has no mechanism records.
    """
    key = make_key("missclass_mech_map_v3", drug_name)
    cached = cache_get(key)
    if cached is not None:
        return cached
    out: dict[str, dict[str, Any]] = {}
    try:
        mid = _chembl._find_molecule_chembl_id(drug_name)
    except Exception as e:
        raise _TransientLookupError(f"molecule resolve '{drug_name}': {e}")
    if mid:
        try:
            data = _chembl._get_json(f"{_chembl.BASE_URL}/mechanism.json",
                                     {"molecule_chembl_id": mid, "limit": 100})
            mechs = data.get("mechanisms", [])
            if not mechs:
                data2 = _chembl._get_json(f"{_chembl.BASE_URL}/mechanism.json",
                                          {"parent_molecule_chembl_id": mid, "limit": 100})
                mechs = data2.get("mechanisms", [])
        except Exception as e:
            raise _TransientLookupError(f"mechanism lookup '{drug_name}': {e}")
        for m in mechs:
            tid = m.get("target_chembl_id")
            if not tid or tid in out:
                continue
            out[tid] = {
                "symbols": _target_gene_symbols(tid),
                "moa": m.get("mechanism_of_action"),
                "action_type": m.get("action_type"),
            }
    cache_set(key, out, ttl_days=30)
    return out


def _drug_molecule_ids(drug_name: str) -> set[str]:
    """The drug's ChEMBL molecule id plus its parent (salt-form tolerance).
    Cached 30d on success only."""
    key = make_key("missclass_mol_ids_v2", drug_name)
    cached = cache_get(key)
    if cached is not None:
        return set(cached)
    ids: set[str] = set()
    try:
        mid = _chembl._find_molecule_chembl_id(drug_name)
    except Exception as e:
        raise _TransientLookupError(f"molecule resolve '{drug_name}': {e}")
    if mid:
        ids.add(mid)
        try:
            data = _chembl._get_json(f"{_chembl.BASE_URL}/molecule/{mid}.json")
        except Exception as e:
            raise _TransientLookupError(f"molecule hierarchy '{drug_name}': {e}")
        parent = (data.get("molecule_hierarchy") or {}).get("parent_chembl_id") \
            or data.get("parent_molecule_chembl_id")
        if parent:
            ids.add(parent)
    cache_set(key, sorted(ids), ttl_days=30)
    return ids


def _has_qualifying_assay(drug_name: str, target_chembl_id: str) -> bool:
    """
    True if the drug (or its parent form) has a Homo sapiens IC50/Ki activity
    at assay confidence >= 8 against this ChEMBL target — i.e. whether the
    activity pool COULD have contained it. Raises _TransientLookupError on any
    lookup failure — callers must never treat failure as a negative result.
    """
    mol_ids = _drug_molecule_ids(drug_name)
    if not mol_ids:
        # Drug unresolvable to any ChEMBL molecule: cannot verify — fail loudly.
        raise _TransientLookupError(f"no ChEMBL molecule ids for '{drug_name}'")
    try:
        acts = _chembl._fetch_activities_full(target_chembl_id)
    except _TransientLookupError:
        raise
    except Exception as e:
        raise _TransientLookupError(f"activities fetch {target_chembl_id}: {e}")
    return any(a.get("molecule_chembl_id") in mol_ids for a in acts)


# ── Classification ───────────────────────────────────────────────────────────

def _tried_symbols(case: dict[str, Any]) -> list[str]:
    per = case.get("per_target_results") or []
    if per:
        return [r.get("target_symbol") for r in per if r.get("target_symbol")]
    st = case.get("selected_target") or {}
    return [st["target_symbol"]] if st.get("target_symbol") else []


def classify_case(case: dict[str, Any]) -> dict[str, Any]:
    """Compute miss-classification fields on one case record (mutates + returns it)."""
    case["miss_class"] = None
    case["drug_mechanism_targets"] = []
    case["target_was_right"] = None
    case["recoverable_by_pool_fix"] = False
    case["recoverable_by_target_fix"] = False
    case["true_target_rank_in_considered"] = None
    case["class_detail"] = None

    if case.get("status") != "miss":
        if case.get("status") == "hit":
            case["miss_class"] = HIT
        return case

    drug = case["drug_name"]
    tried = _tried_symbols(case)
    considered = [c.get("target_symbol") for c in (case.get("candidate_targets_considered") or [])
                  if c.get("target_symbol")]

    try:
        # Biologic check first — the small-molecule activity pool can never
        # contain these, so assay lookups would be wasted work.
        mtype = (_molecule_type(drug) or "").strip()
        if mtype and mtype.lower() != "small molecule":
            mech_map = _drug_mechanism_map(drug)  # only to report whether target was right
            mech_syms = sorted({s for v in mech_map.values() for s in v.get("symbols", [])})
            case["drug_mechanism_targets"] = mech_syms
            case["target_was_right"] = bool(set(mech_syms) & set(tried))
            case["miss_class"] = BIOLOGIC
            case["class_detail"] = (
                f"molecule_type={mtype}; small-molecule IC50/Ki pool cannot contain it."
                + (" Its mechanism target WAS among tried targets." if case["target_was_right"] else "")
            )
            return case

        mech_map = _drug_mechanism_map(drug)
        mech_syms = sorted({s for v in mech_map.values() for s in v.get("symbols", [])})
        case["drug_mechanism_targets"] = mech_syms

        overlap_tids = [tid for tid, v in mech_map.items()
                        if set(v.get("symbols", [])) & set(tried)]
        case["target_was_right"] = bool(overlap_tids)

        if not mech_map:
            case["miss_class"] = UNRESOLVED
            case["class_detail"] = "No ChEMBL mechanism records for this drug."
            return case

        if overlap_tids:
            tid = overlap_tids[0]
            sym = "/".join(mech_map[tid]["symbols"]) or tid
            qualifies = _has_qualifying_assay(drug, tid)  # raises on failure
            if qualifies:
                case["miss_class"] = POOL_TRUNC
                case["class_detail"] = (
                    f"Drug has qualifying IC50/Ki (conf>=8) vs {sym} ({tid}) yet was "
                    f"absent from the pool — unexpected given the approved-drug append rule."
                )
            else:
                case["miss_class"] = POOL_GAP
                case["recoverable_by_pool_fix"] = True
                case["class_detail"] = (
                    f"Mechanism endpoint links drug to {sym} ({mech_map[tid]['moa']}) but no "
                    f"qualifying Homo sapiens IC50/Ki assay exists — activity pool cannot see it."
                )
            return case

        case["miss_class"] = WRONG_TARGET
        ranks = [i for i, s in enumerate(considered, 1) if s in mech_syms]
        if ranks:
            case["recoverable_by_target_fix"] = True
            case["true_target_rank_in_considered"] = ranks[0]
            case["class_detail"] = (
                f"Drug's mechanism target(s) {mech_syms} were NOT tried; true target sits at "
                f"selection rank {ranks[0]} (tried top-{len(tried)})."
            )
        else:
            case["class_detail"] = (
                f"Drug's mechanism target(s) {mech_syms or ['(none resolvable)']} absent from "
                f"the entire considered-target list — discovery problem, not ranking."
            )
        return case

    except _TransientLookupError as e:
        # Never derive recoverability (or a durable verdict) from a failed lookup.
        case["miss_class"] = LOOKUP_FAIL
        case["recoverable_by_pool_fix"] = False
        case["recoverable_by_target_fix"] = False
        case["class_detail"] = f"ChEMBL lookup failed during classification: {e}"
        return case


def classify_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for c in cases:
        already = (
            c.get("miss_class")
            and c.get("miss_class") != LOOKUP_FAIL
            and c.get("classifier_version") == CLASSIFIER_VERSION
        )
        if already:
            continue  # current-version classification is still valid
        try:
            classify_case(c)
            if c.get("miss_class") != LOOKUP_FAIL:
                c["classifier_version"] = CLASSIFIER_VERSION
        except Exception as e:
            _log(f"  ERROR classifying {c.get('drug_name')}: {e}")
            c["miss_class"] = c.get("miss_class") or "classification_error"
    return cases


# ── Reporting ────────────────────────────────────────────────────────────────

def _counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in cases:
        if not c.get("in_universe"):
            continue
        cls = c.get("miss_class") or ("hit" if c.get("status") == "hit" else "unclassified")
        out[cls] = out.get(cls, 0) + 1
    return out


def breakdown_lines(cases: list[dict[str, Any]]) -> list[str]:
    in_uni = [c for c in cases if c.get("in_universe")]
    n = len(in_uni)
    cnt = _counts(cases)
    hits = cnt.get(HIT, 0)
    pool_fix = sum(1 for c in in_uni if c.get("recoverable_by_pool_fix"))
    tgt_fix = sum(1 for c in in_uni if c.get("recoverable_by_target_fix"))
    ceiling = hits + pool_fix + tgt_fix
    lines = [
        "## Miss-reason breakdown (computed)\n",
        f"- Recovered (hit): **{hits}/{n}**",
        f"- Right target, drug absent from activity pool (mechanism-endpoint gap): "
        f"**{cnt.get(POOL_GAP, 0)}** — recoverable by the candidate-pool fix",
        f"- Right target but pool truncation: **{cnt.get(POOL_TRUNC, 0)}**",
        f"- Wrong target selected: **{cnt.get(WRONG_TARGET, 0)}** "
        f"(true target was in the considered list for {tgt_fix} → recoverable by target-selection fix)",
        f"- Biologic / non-small-molecule (structurally outside the pool): **{cnt.get(BIOLOGIC, 0)}**",
        f"- No ChEMBL mechanism record for drug: **{cnt.get(UNRESOLVED, 0)}**",
    ]
    if cnt.get(LOOKUP_FAIL):
        lines.append(f"- ⚠ Unverified (transient ChEMBL lookup failure — rerun to retry): "
                     f"**{cnt.get(LOOKUP_FAIL, 0)}**")
    lines += [
        "",
        f"**Projected ceiling under both fixes: {ceiling}/{n}** "
        f"(hits + pool-fix recoverable + target-fix recoverable).",
        "",
        "| Drug | Disease | Class | Detail |",
        "|---|---|---|---|",
    ]
    for c in in_uni:
        if c.get("status") == "hit":
            cls, det = "hit", f"rank {c.get('rank')}, composite {c.get('composite_score')}"
        else:
            cls = c.get("miss_class") or "—"
            det = (c.get("class_detail") or "").replace("|", "/")
        lines.append(f"| {c['drug_name']} | {c['disease_name']} | {cls} | {det} |")
    return lines


def write_combined_summary() -> str:
    """Re-read both results JSONs and write validation/rediscovery_summary.md."""
    files = [
        ("repodb_results_topk.json", "repoDB first-10 (biologic-leaning, top-3 targets)"),
        ("repodb_results_smallmol.json", "repoDB small-molecule set (top-1 target)"),
    ]
    lines: list[str] = [
        "# Rediscovery-rate summary — repoDB retrospective\n",
        f"_Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} — computed by validation/miss_classifier.py_\n",
    ]
    all_cases: list[dict[str, Any]] = []
    for fname, label in files:
        path = os.path.join(VALIDATION_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            cases = json.load(f).get("cases", [])
        all_cases.extend(cases)
        lines += [f"## {label}\n"] + breakdown_lines(cases) + [""]
    if all_cases:
        lines += ["## Combined\n"] + breakdown_lines(all_cases)
    out = os.path.join(VALIDATION_DIR, "rediscovery_summary.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    _log(f"wrote {out}")
    return out


def main() -> None:
    """Force re-classify both results JSONs on disk (in place) + combined summary."""
    for fname in ("repodb_results_topk.json", "repodb_results_smallmol.json"):
        path = os.path.join(VALIDATION_DIR, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        cases = payload.get("cases", [])
        # force re-classification (standalone entry = explicit recompute)
        for c in cases:
            c.pop("miss_class", None)
        cases = classify_cases(cases)
        payload["cases"] = cases
        payload["classified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        _log(f"classified {len(cases)} cases in {fname}")
    write_combined_summary()


if __name__ == "__main__":
    main()
