"""Study B (DESCRIPTIVE ONLY): full-configuration audit against rebuilt pools.

Rebuilds the candidate pools for the 12 in-scope primary benchmark diseases
using the production graph semantics (Amendment 2): biologist -> chemist per
top-K target, `merge_chemist_candidates` union across targets, then ONE
pooled reviewer pass — the same shape `run_v2_engineering_acceptance.py`
uses (`pooled_across_k_targets=True`). The v1 draft ran the reviewer per
target and deduplicated by best composite, which does not reproduce
production ranks; that code never produced results and was replaced before
any scoring.

Pre-registered boundaries (triage_discrimination_preregistration.md):

* **Descriptive only.** No thresholds, no pass/fail. n is small by
  construction (22 confirmed cases, 12 diseases) and the pools are NOT
  disease-blind at the composite/rank/mechanism level (reviewer derives those
  from disease-linked OT/trial data). Every row carries that caveat.
* **Non-confirmed candidates are never scored as errors.** Absence of
  approval is not evidence of a wrong hypothesis; they are context rows only.
* **Fail-closed.** Existing results are never regenerated; a disease whose
  targets did not ALL complete is never profiled from a partial pool —
  it is left for resume.
* **Freeze-verified.** The Study B freeze manifest (rule fingerprint, v2
  benchmark hash) is checked before any case runs.

This run makes LLM calls (the pipeline agents). It is the user-approved
expensive half of the study (approved 2026-08-11).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.target_selection import DiseaseNotInUniverse, select_for_disease  # noqa: E402
from agents.biologist import run_biologist  # noqa: E402
from agents.chemist import run_chemist  # noqa: E402
from agents.reviewer import run_reviewer  # noqa: E402
from data_sources.multisource_candidates import merge_chemist_candidates  # noqa: E402
from api.audit import _find_molecule_chembl_id  # noqa: E402
from api.audit_context import build_audit_context  # noqa: E402
from data_sources import holdout  # noqa: E402
from validation.evidence_profile import RULE_FINGERPRINT, build_profile  # noqa: E402

V2_RESULTS = ROOT / "validation" / "benchmark_results_v2.json"
MANIFEST_PATH = ROOT / "validation" / "triage_discrimination_studyb_freeze_manifest.json"
CKPT_PATH = ROOT / "validation" / "triage_discrimination_studyb_checkpoint.jsonl"
RESULTS_PATH = ROOT / "validation" / "triage_discrimination_studyb_results.json"

TOP_K = 3
TOP_N_AUDIT = 10  # pool candidates profiled per disease, as context rows


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _health_gate() -> None:
    # PubChem is a hard dependency of pool building (compound enrichment).
    # Its omission here is why the first attempt wedged for hours against a
    # 503 ServerBusy instead of refusing to start.
    probes = {
        "chembl": "https://www.ebi.ac.uk/chembl/api/data/status.json",
        "openfda": "https://api.fda.gov/drug/label.json?limit=1",
        "pubchem": ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/"
                    "name/aspirin/property/InChIKey/JSON"),
    }
    failed = []
    for name, url in probes.items():
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code >= 500 or resp.status_code == 429:
                failed.append(f"{name}(HTTP {resp.status_code})")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{exc.__class__.__name__}: {name}")
    if failed:
        raise SystemExit(f"[studyb] REFUSED: sources unhealthy: {failed}")


def _verify_freeze() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    problems = []
    if manifest.get("rule_fingerprint") != RULE_FINGERPRINT:
        problems.append(
            f"rule fingerprint {RULE_FINGERPRINT[:12]}… != manifest "
            f"{str(manifest.get('rule_fingerprint'))[:12]}…")
    if manifest.get("v2_results_sha256") != _sha256_file(V2_RESULTS):
        problems.append("benchmark_results_v2.json hash mismatch")
    if problems:
        raise SystemExit(f"[studyb] FREEZE VIOLATION: {problems}")
    return manifest


def _disease_cases() -> dict[str, list[str]]:
    """In-scope primary diseases -> confirmed drug names (from frozen v2)."""
    data = json.loads(V2_RESULTS.read_text())
    out: dict[str, list[str]] = {}
    for c in data["cases"]:
        if c.get("subset") != "primary" or c.get("status") == "out_of_scope":
            continue
        out.setdefault(c["disease_name"], []).append(c["drug_name"])
    return out


def _load_checkpoint() -> dict:
    """Two record kinds: per-target chemist output (LLM work, resumable) and
    finalized per-disease pools (only ever written when ALL targets ran)."""
    done = {"targets": {}, "pools": {}}
    if CKPT_PATH.exists():
        for line in CKPT_PATH.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("rule_fingerprint") != RULE_FINGERPRINT:
                raise SystemExit(
                    "[studyb] REFUSED: checkpoint record predates the current "
                    "rule fingerprint. Move the stale checkpoint aside.")
            if rec["kind"] == "target":
                done["targets"][(rec["disease_name"], rec["target_symbol"])] = rec
            elif rec["kind"] == "pool":
                done["pools"][rec["disease_name"]] = rec
    return done


def _append(rec: dict) -> None:
    rec = {**rec, "rule_fingerprint": RULE_FINGERPRINT}
    with open(CKPT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


def _row_to_target(row: dict) -> dict:
    return {
        "target_symbol": row["target_symbol"],
        "uniprot_id": row.get("uniprot_id"),
        "ensembl_id": row.get("ensembl_id"),
        "disease_name": row["disease_name"],
        "orpha_code": row.get("orpha_code"),
        "ot_association_score": row.get("ot_association_score", 0.0),
        "tractability_score": row.get("tractability_score"),
        "unmet_need_score": row.get("unmet_need_score"),
        "target_discovery_method": row.get("target_discovery_method"),
    }


def _build_pool(disease: str, drugs: list[str], targets_done: dict) -> dict | None:
    """Run one disease's pool build. Returns the finalized pool record, or
    None if any target failed (nothing is finalized from a partial build)."""
    try:
        rows = select_for_disease(disease)
    except DiseaseNotInUniverse as exc:
        print(f"[studyb] {disease}: OUT OF UNIVERSE ({exc})", flush=True)
        return None
    run_rows = rows[:TOP_K]
    all_candidates: list[dict] = []
    bio_pmids: list[str] = []
    per_target = []
    for k_idx, row in enumerate(run_rows, 1):
        symbol = row["target_symbol"]
        key = (disease, symbol)
        rec = targets_done.get(key)
        if rec is None:
            print(f"[studyb]   {disease}: target {k_idx}/{len(run_rows)} "
                  f"{symbol}", flush=True)
            try:
                bio = run_biologist(_row_to_target(row))
                chem = run_chemist(bio)
            except Exception as exc:  # noqa: BLE001
                # Partial builds are NOT usable: leave uncheckpointed so a
                # resume retries this target.
                print(f"[studyb]   {disease}/{symbol}: ERROR {exc} — "
                      "target left incomplete for resume", flush=True)
                return None
            rec = {
                "kind": "target",
                "disease_name": disease,
                "target_symbol": symbol,
                "k_idx": k_idx,
                "candidates": chem.get("candidates", []),
                "bio_pmids": [h["pmid"] for h in bio.get("literature_hits", [])
                              if isinstance(h, dict) and h.get("pmid")],
            }
            _append(rec)
            targets_done[key] = rec
        per_target.append({"target_symbol": symbol,
                           "n_candidates": len(rec["candidates"])})
        all_candidates.extend(rec["candidates"])
        bio_pmids.extend(rec["bio_pmids"])

    # Production semantics: one active-moiety union across targets, one
    # pooled reviewer pass (mirrors run_v2_engineering_acceptance.py).
    pooled = merge_chemist_candidates(all_candidates)
    pooled_output = {
        "target": _row_to_target(run_rows[0]) if run_rows else {},
        "targets": [_row_to_target(r) for r in run_rows],
        "candidates": pooled,
        "pooled_across_k_targets": True,
        "k_targets": len(run_rows),
        "repurposing_only": True,
    }
    bio_min = {"literature_hits": [{"pmid": p} for p in bio_pmids]}
    try:
        reviewed = run_reviewer(pooled_output, bio_min)
    except Exception as exc:  # noqa: BLE001
        print(f"[studyb]   {disease}: pooled reviewer failed: {exc} — "
              "left for resume (targets are checkpointed)", flush=True)
        return None
    pool = sorted(reviewed, key=lambda c: float(c.get("composite_score") or 0),
                  reverse=True)
    rec = {"kind": "pool", "disease_name": disease, "holdout_drugs": drugs,
           "per_target": per_target, "pool_size": len(pool), "pool": pool}
    _append(rec)
    return rec


def _profile_drug(disease: str, drug: str, pool: list[dict],
                  total: int) -> dict:
    """Profile one drug against one pool — the same fields run_audit emits."""
    deadline = time.monotonic() + 30.0
    chembl_id = None
    try:
        chembl_id = _find_molecule_chembl_id(drug)
    except Exception:  # noqa: BLE001
        chembl_id = None
    key = " ".join(drug.split()).casefold()
    rank, cand = None, None
    for i, c in enumerate(pool, 1):
        if " ".join(str(c.get("drug_name") or "").split()).casefold() == key:
            rank, cand = i, c
            break
    top = pool[0] if pool else None
    status = "found" if cand is not None else (
        "unresolved" if chembl_id is None else "absent")
    mechanism_symbol = str(
        (cand or {}).get("target_symbol")
        or (top or {}).get("target_symbol") or "")
    ctx = build_audit_context(drug, mechanism_symbol=mechanism_symbol,
                              deadline_monotonic=deadline)
    if not (ctx.get("holdout_redaction") or {}).get("applied"):
        raise RuntimeError(f"{drug}: unredacted audit context under holdout")
    audit = {"status": status, "rank": rank, "total_candidates": total,
             "candidate": cand, "audit_context": ctx}
    return {"drug_name": drug, "disease_name": disease, "rank": rank,
            "pool_status": status,
            "profile": build_profile(drug, audit)}


def main() -> None:
    if RESULTS_PATH.exists():
        raise SystemExit("[studyb] REFUSED: results exist. Amend, never "
                         "regenerate.")
    manifest = _verify_freeze()
    _health_gate()
    diseases = _disease_cases()
    done = _load_checkpoint()
    print(f"[studyb] {len(diseases)} diseases, {len(done['pools'])} pools "
          f"finalized, {len(done['targets'])} targets checkpointed",
          flush=True)

    table: list[dict] = []
    skipped: list[str] = []
    for disease, drugs in sorted(diseases.items()):
        pool_rec = done["pools"].get(disease)
        if pool_rec is None:
            with holdout.holdout_active(drugs):
                pool_rec = _build_pool(disease, drugs, done["targets"])
            if pool_rec is None:
                skipped.append(disease)
                continue
            print(f"[studyb] {disease}: pool={pool_rec['pool_size']}",
                  flush=True)
        pool = pool_rec["pool"]
        total = pool_rec["pool_size"]
        top_names = [str(c.get("drug_name")) for c in pool[:TOP_N_AUDIT]]
        with holdout.holdout_active(drugs):
            for drug in drugs:  # confirmed repurposings: the rows of interest
                table.append({**_profile_drug(disease, drug, pool, total),
                              "row_kind": "confirmed"})
            for name in top_names:  # context rows only, never scored
                if name.casefold() not in {d.casefold() for d in drugs}:
                    table.append({**_profile_drug(disease, name, pool, total),
                                  "row_kind": "context_top_candidate"})

    payload = {
        "contract": "triage-discrimination-studyb-v2",
        "descriptive_only": True,
        "freeze": manifest,
        "non_disease_blind_pool_caveat": (
            "rank/composite/mechanism dimensions derive from a pool built "
            "with disease-linked OpenTargets and trial data; only the "
            "disease-independent dimensions are provably blind."),
        "rule_fingerprint": RULE_FINGERPRINT,
        "v2_results_sha256": _sha256_file(V2_RESULTS),
        "n_diseases": len(diseases),
        "diseases_incomplete": skipped,
        "rows": table,
    }
    payload["results_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True,
                                       default=str) + "\n")
    print(f"[studyb] wrote {RESULTS_PATH} ({len(table)} rows, "
          f"{len(skipped)} diseases incomplete)", flush=True)


if __name__ == "__main__":
    main()
