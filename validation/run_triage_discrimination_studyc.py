"""Study C: POWERED triage discrimination — confirmed repurposings vs
genuine-failure negatives against rebuilt pools.

Same production pool semantics as Study B (biologist -> chemist per top-K
target, merge_chemist_candidates union, ONE pooled reviewer pass), but the
case set (validation/triage_discrimination_studyc_cases.json, contract
triage-discrimination-studyc-cases-v1) carries 27 diseases each anchored by
>=1 genuine-failure negative AND >=1 confirmed positive, so a per-disease
ranking contrast is always defined.

The discrimination metric: per-disease rank AUC — P(a confirmed positive
ranks ahead of a genuine-failure negative) over pool-present drugs — plus
pool-presence rates per class (a negative that never enters the pool is
already discrimination evidence; absence is reported, never scored as a
rank). Significance thresholds belong to the report layer, not this runner.

Discipline (same as Study B):

* **Sequencing guard.** Study C must never run concurrently with Study B on
  prod (2.3x LLM cost). The runner REFUSES while Study B results do not
  exist, unless --force is passed deliberately.
* **Freeze-verified.** --freeze writes a manifest (cases hash, rule
  fingerprint, commit); the run refuses on any mismatch.
* **Health-gated.** ChEMBL, openFDA, PubChem must be healthy.
* **Blindness-asserted.** Pool build and profiling run under holdout over
  the disease's confirmed positives; _profile_drug asserts redaction.
* **Checkpointed.** Per-target and per-pool records bound to the cases hash
  and rule fingerprint; stale records refuse the run.
* **Fail-closed.** Existing results are never regenerated; a disease whose
  targets did not ALL complete is never profiled from a partial pool.
* **Disclosed exclusion.** A disease with no genetically-associated targets
  AND no approved-drug MOA targets (deterministically unscorable) is
  recorded as a hash-bound disease_excluded checkpoint record and disclosed
  in results under diseases_excluded — never retried, never crash-looped.

This run makes LLM calls (the pipeline agents). Approved 2026-08-12 as the
powered follow-up to Study B.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.target_selection import DiseaseNotInUniverse, select_for_disease  # noqa: E402
from agents.biologist import run_biologist  # noqa: E402
from agents.chemist import run_chemist  # noqa: E402
from agents.reviewer import run_reviewer  # noqa: E402
from data_sources.multisource_candidates import merge_chemist_candidates  # noqa: E402
from data_sources import holdout  # noqa: E402
from validation.evidence_profile import RULE_FINGERPRINT  # noqa: E402
from validation.run_triage_discrimination_studyb import (  # noqa: E402
    TOP_K, TOP_N_AUDIT, _health_gate, _profile_drug, _row_to_target)

CASES_PATH = ROOT / "validation" / "triage_discrimination_studyc_cases.json"
MANIFEST_PATH = (ROOT / "validation"
                 / "triage_discrimination_studyc_freeze_manifest.json")
CKPT_PATH = (ROOT / "validation"
             / "triage_discrimination_studyc_checkpoint.jsonl")
RESULTS_PATH = (ROOT / "validation"
                / "triage_discrimination_studyc_results.json")
STUDYB_RESULTS = (ROOT / "validation"
                  / "triage_discrimination_studyb_results.json")
_LOCK_PATH = ROOT / "validation" / ".studyc_runner.lock"

RESULTS_CONTRACT = "triage-discrimination-studyc-v1"

_UNSCORABLE_MARKER = "nothing to score"


class DiseaseUnscorable(Exception):
    """select_for_disease proved there is nothing to score: no genetically-
    associated targets AND no approved-drug MOA targets. For a resolved
    disease this is deterministic — retrying can never succeed — so the
    runner records a permanent, disclosed exclusion instead of crash-looping.
    The marker requires BOTH lanes empty, so a single-API blip (e.g. an
    Open Targets degraded-200) cannot trigger a false exclusion while
    ChEMBL is healthy."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_freeze() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text())
    problems = []
    if manifest.get("cases_sha256") != _sha256_file(CASES_PATH):
        problems.append("case set hash mismatch")
    if manifest.get("rule_fingerprint") != RULE_FINGERPRINT:
        problems.append("rule fingerprint mismatch")
    # The manifest pins the code commit; running or resuming on different
    # code with old checkpoint records would publish an irreproducible
    # "frozen" result. This check runs before the checkpoint is loaded, so
    # stale target records from another code version can never be consumed.
    # Fail-open only when git itself is unavailable.
    frozen_head = manifest.get("frozen_at_commit")
    if frozen_head:
        import subprocess
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True,
                text=True, timeout=10, cwd=str(ROOT)).stdout.strip()
        except Exception:  # noqa: BLE001
            head = ""
        if head and head != frozen_head:
            problems.append(
                f"HEAD {head[:12]}… != frozen commit {frozen_head[:12]}…")
    if problems:
        raise SystemExit(f"[studyc] FREEZE VIOLATION: {problems}")
    return manifest


def _load_checkpoint() -> dict:
    done = {"targets": {}, "pools": {}, "excluded": {}}
    if CKPT_PATH.exists():
        for line in CKPT_PATH.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if (rec.get("rule_fingerprint") != RULE_FINGERPRINT
                    or rec.get("cases_sha256") != _sha256_file(CASES_PATH)):
                raise SystemExit(
                    "[studyc] REFUSED: checkpoint record predates the "
                    "current case set or rule fingerprint. Move the stale "
                    "checkpoint aside or amend deliberately.")
            if rec["kind"] == "target":
                done["targets"][(rec["disease_name"], rec["target_symbol"])] = rec
            elif rec["kind"] == "pool":
                done["pools"][rec["disease_name"]] = rec
            elif rec["kind"] == "disease_excluded":
                done["excluded"][rec["disease_name"]] = rec.get("reason", "")
    return done


def _append(rec: dict) -> None:
    rec = {**rec,
           "rule_fingerprint": RULE_FINGERPRINT,
           "cases_sha256": _sha256_file(CASES_PATH)}
    with open(CKPT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


def _acquire_run_lease():
    """Exclusive same-host lease on the runner (not just the supervisor).
    Workflow restarts can leave the old process alive; without this, two
    runners load the same checkpoint and double-spend LLM calls."""
    import fcntl
    fh = open(_LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit("[studyc] another runner instance holds the lease — "
                         "exiting (no duplicate LLM spend)")
    return fh


def _build_pool(disease: str, targets_done: dict) -> dict | None:
    """One disease's pool build — identical semantics to Study B. Returns
    None (nothing finalized) if any target or the reviewer pass failed."""
    try:
        rows = select_for_disease(disease)
    except DiseaseNotInUniverse as exc:
        print(f"[studyc] {disease}: OUT OF UNIVERSE ({exc})", flush=True)
        return None
    except RuntimeError as exc:
        if _UNSCORABLE_MARKER in str(exc):
            raise DiseaseUnscorable(str(exc)) from exc
        raise
    run_rows = rows[:TOP_K]
    all_candidates: list[dict] = []
    bio_pmids: list[str] = []
    per_target = []
    for k_idx, row in enumerate(run_rows, 1):
        symbol = row["target_symbol"]
        key = (disease, symbol)
        rec = targets_done.get(key)
        if rec is None:
            print(f"[studyc]   {disease}: target {k_idx}/{len(run_rows)} "
                  f"{symbol}", flush=True)
            try:
                bio = run_biologist(_row_to_target(row))
                chem = run_chemist(bio)
            except Exception as exc:  # noqa: BLE001
                print(f"[studyc]   {disease}/{symbol}: ERROR {exc} — "
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
    print(f"[studyc]   {disease}: pooled reviewer over {len(pooled)} pooled "
          f"candidates ({len(all_candidates)} raw across "
          f"{len(run_rows)} targets)", flush=True)
    try:
        reviewed = run_reviewer(pooled_output, bio_min)
    except Exception as exc:  # noqa: BLE001
        print(f"[studyc]   {disease}: pooled reviewer failed: {exc} — "
              "left for resume (targets are checkpointed)", flush=True)
        return None
    pool = sorted(reviewed, key=lambda c: float(c.get("composite_score") or 0),
                  reverse=True)
    rec = {"kind": "pool", "disease_name": disease,
           "per_target": per_target, "pool_size": len(pool), "pool": pool}
    _append(rec)
    return rec


def _pool_score(pool: list[dict], drug: str) -> float | None:
    """The drug's composite score in the pool, or None if absent."""
    key = " ".join(drug.split()).casefold()
    for c in pool:
        if " ".join(str(c.get("drug_name") or "").split()).casefold() == key:
            try:
                return float(c.get("composite_score") or 0.0)
            except (TypeError, ValueError):
                return None
    return None


def _score_auc(pos_scores: list[float],
               neg_scores: list[float]) -> float | None:
    """P(positive scores HIGHER than negative), exact ties at 0.5.

    Computed from composite SCORES, not list positions: sorted ranks are
    unique positions, so equal scores would otherwise be broken by input
    order and counted as full wins/losses (architect review 2026-08-13).
    """
    n = len(pos_scores) * len(neg_scores)
    if n == 0:
        return None
    wins = sum(1.0 if p > g else 0.5 if p == g else 0.0
               for p in pos_scores for g in neg_scores)
    return wins / n


def _presence(rows: list[dict]) -> dict:
    out = {"found": 0, "absent": 0, "unresolved": 0}
    for r in rows:
        out[r["pool_status"]] = out.get(r["pool_status"], 0) + 1
    return out


def main() -> None:
    if "--freeze" in sys.argv:
        if RESULTS_PATH.exists():
            raise SystemExit("[freeze] REFUSED: scored results exist.")
        import os
        manifest = {
            "contract": "triage-discrimination-studyc-freeze-v1",
            "frozen_at_commit": os.popen("git rev-parse HEAD").read().strip(),
            "cases_sha256": _sha256_file(CASES_PATH),
            "rule_fingerprint": RULE_FINGERPRINT,
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"[freeze] wrote {MANIFEST_PATH}")
        print(json.dumps(manifest, indent=2))
        return

    _lease = _acquire_run_lease()  # noqa: F841 — held until process exit
    if RESULTS_PATH.exists():
        raise SystemExit("[studyc] REFUSED: results exist. Amend, never "
                         "regenerate.")
    if "--force" not in sys.argv:
        if not STUDYB_RESULTS.exists():
            raise SystemExit(
                "[studyc] REFUSED: Study B results do not exist yet. Study C "
                "must not run concurrently with Study B (LLM cost). Pass "
                "--force to override deliberately.")
        # Existence is not completion: a stale/corrupt/partial Study B file
        # must not unlock an expensive Study C run.
        try:
            sb = json.loads(STUDYB_RESULTS.read_text())
            studyb_complete = (
                sb.get("contract") == "triage-discrimination-studyb-v2"
                and sb.get("diseases_incomplete") == []
                and bool(sb.get("rows")))
        except Exception:  # noqa: BLE001
            studyb_complete = False
        if not studyb_complete:
            raise SystemExit(
                "[studyc] REFUSED: Study B results exist but are not a "
                "complete v2 artifact (stale or partial file). Pass "
                "--force to override deliberately.")
    manifest = _verify_freeze()
    _health_gate()

    caseset = json.loads(CASES_PATH.read_text())
    diseases = caseset["diseases"]
    done = _load_checkpoint()
    print(f"[studyc] {len(diseases)} diseases, {len(done['pools'])} pools "
          f"finalized, {len(done['targets'])} targets checkpointed",
          flush=True)

    table: list[dict] = []
    per_disease: list[dict] = []
    skipped: list[str] = []
    excluded: list[dict] = []
    for d in diseases:
        disease = d["disease_name"]
        positives, negatives = d["positives"], d["negatives"]
        if disease in done["excluded"]:
            excluded.append({"disease_name": disease,
                             "reason": done["excluded"][disease]})
            print(f"[studyc] {disease}: EXCLUDED (recorded unscorable) — "
                  "skipping", flush=True)
            continue
        pool_rec = done["pools"].get(disease)
        if pool_rec is None:
            with holdout.holdout_active(positives):
                try:
                    pool_rec = _build_pool(disease, done["targets"])
                except DiseaseUnscorable as exc:
                    _append({"kind": "disease_excluded",
                             "disease_name": disease,
                             "reason": str(exc)})
                    done["excluded"][disease] = str(exc)
                    pool_rec = None
            if pool_rec is None:
                if disease in done["excluded"]:
                    excluded.append({"disease_name": disease,
                                     "reason": done["excluded"][disease]})
                    print(f"[studyc] {disease}: EXCLUDED — "
                          f"{done['excluded'][disease]}", flush=True)
                else:
                    skipped.append(disease)
                continue
            print(f"[studyc] {disease}: pool={pool_rec['pool_size']}",
                  flush=True)
        pool = pool_rec["pool"]
        total = pool_rec["pool_size"]
        pos_rows: list[dict] = []
        neg_rows: list[dict] = []
        with holdout.holdout_active(positives):
            for drug in positives:
                rec = {**_profile_drug(disease, drug, pool, total),
                       "composite_score": _pool_score(pool, drug),
                       "row_kind": "confirmed_positive"}
                pos_rows.append(rec)
                table.append(rec)
            for drug in negatives:
                rec = {**_profile_drug(disease, drug, pool, total),
                       "composite_score": _pool_score(pool, drug),
                       "row_kind": "genuine_negative"}
                neg_rows.append(rec)
                table.append(rec)
            scored_names = {p.casefold() for p in positives + negatives}
            for c in pool[:TOP_N_AUDIT]:  # context rows only, never scored
                name = str(c.get("drug_name"))
                if name.casefold() not in scored_names:
                    table.append({**_profile_drug(disease, name, pool, total),
                                  "row_kind": "context_top_candidate"})
        pos_ranks = [r["rank"] for r in pos_rows if r["rank"] is not None]
        neg_ranks = [r["rank"] for r in neg_rows if r["rank"] is not None]
        pos_scores = [r["composite_score"] for r in pos_rows
                      if r["composite_score"] is not None]
        neg_scores = [r["composite_score"] for r in neg_rows
                      if r["composite_score"] is not None]
        per_disease.append({
            "disease_name": disease,
            "pool_size": total,
            "positives": _presence(pos_rows),
            "negatives": _presence(neg_rows),
            "pos_ranks": pos_ranks,
            "neg_ranks": neg_ranks,
            "score_auc": _score_auc(pos_scores, neg_scores),
        })

    if skipped:
        print(f"[studyc] INCOMPLETE: {len(skipped)} disease(s) not finalized "
              f"({skipped}) — NOT writing results; resume to continue",
              flush=True)
        raise SystemExit(1)

    aucs = [d["score_auc"] for d in per_disease if d["score_auc"] is not None]
    pooled_metrics = {
        "n_diseases": len(per_disease),
        "n_diseases_with_contrast": len(aucs),
        "median_score_auc": sorted(aucs)[len(aucs) // 2] if aucs else None,
        "n_auc_above_chance": sum(1 for a in aucs if a > 0.5),
        "n_auc_below_chance": sum(1 for a in aucs if a < 0.5),
        "positive_pool_presence": {
            k: sum(d["positives"].get(k, 0) for d in per_disease)
            for k in ("found", "absent", "unresolved")},
        "negative_pool_presence": {
            k: sum(d["negatives"].get(k, 0) for d in per_disease)
            for k in ("found", "absent", "unresolved")},
    }

    payload = {
        "contract": RESULTS_CONTRACT,
        "freeze": manifest,
        "cases_sha256": _sha256_file(CASES_PATH),
        "rule_fingerprint": RULE_FINGERPRINT,
        "non_disease_blind_pool_caveat": (
            "rank/composite/mechanism dimensions derive from a pool built "
            "with disease-linked OpenTargets and trial data; only the "
            "disease-independent dimensions are provably blind."),
        "metric_note": (
            "score_auc = P(a confirmed positive's composite_score exceeds a "
            "genuine-failure negative's), exact ties at 0.5, among "
            "pool-present drugs; pool absence is reported per class, "
            "never scored."),
        "pooled": pooled_metrics,
        "per_disease": per_disease,
        "diseases_excluded": excluded,
        "rows": table,
    }
    payload["results_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    tmp = RESULTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True,
                              default=str) + "\n")
    tmp.replace(RESULTS_PATH)
    if excluded:
        print(f"[studyc] {len(excluded)} disease(s) excluded as unscorable "
              "(disclosed in results under diseases_excluded)", flush=True)
    print(f"[studyc] wrote {RESULTS_PATH} ({len(table)} rows, "
          f"{len(per_disease)} diseases complete)", flush=True)
    print(json.dumps(pooled_metrics, indent=2), flush=True)


if __name__ == "__main__":
    main()
