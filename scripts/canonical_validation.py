"""
Canonical validation — all 5 cases, full pipeline.
Writes to /tmp/canonical_validation.log and /tmp/canonical_validation.json.

Settings: REPURPOSING_ONLY=True, K=TOP_TARGETS_PER_DISEASE=5,
          pathway-neighbor lazy trigger, parent-umbrella supplement,
          PARENT_MAX_DESCENDANTS=100, two-layer safety check.
Run: python scripts/canonical_validation.py
"""
import sys, json, os, time, traceback
sys.path.insert(0, "/home/runner/workspace")

from concurrent.futures import ThreadPoolExecutor, as_completed

LOG  = "/tmp/canonical_validation.log"
JOUT = "/tmp/canonical_validation.json"

# Wipe old log at startup
with open(LOG, "w") as _f:
    _f.write("")

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

from agents.target_selection import (
    select_for_disease, DiseaseNotInUniverse,
    TOP_TARGETS_PER_DISEASE, PARENT_MAX_DESCENDANTS,
)
from agents.biologist import run_biologist
from agents.chemist import run_chemist
from agents.reviewer import run_reviewer, STRONG_MATCH_THRESHOLD, SAFETY_CAP, MAX_SAFETY_LAYER2_CANDIDATES

REPURPOSING_ONLY = True
RAPALOGS = {"SIROLIMUS", "TEMSIROLIMUS", "RIDAFOROLIMUS"}
IMIDS    = {"THALIDOMIDE", "LENALIDOMIDE", "POMALIDOMIDE"}
PDE5_INH = {"SILDENAFIL", "SILDENAFIL CITRATE", "TADALAFIL", "VARDENAFIL", "VARDENAFIL HYDROCHLORIDE"}


def run_bio_chem_k5(targets):
    """Run biologist + chemist in parallel for up to K=5 targets. Returns pooled chemist output."""
    top_k = targets[:TOP_TARGETS_PER_DISEASE]

    bio_outputs = [None] * len(top_k)
    def bio_worker(idx_target):
        idx, target = idx_target
        sym = target.get("target_symbol", "?")
        t0 = time.time()
        result = run_biologist(target)
        log(f"    bio {sym} {time.time()-t0:.1f}s")
        return idx, result

    with ThreadPoolExecutor(max_workers=len(top_k)) as ex:
        for fut in as_completed({ex.submit(bio_worker, (i, t)): i for i, t in enumerate(top_k)}):
            idx, bio = fut.result()
            bio_outputs[idx] = bio

    chem_outputs = [None] * len(top_k)
    def chem_worker(idx_bio):
        idx, bio = idx_bio
        if not bio:
            return idx, {"candidates": []}
        sym = bio.get("target_symbol", "?")
        t0 = time.time()
        result = run_chemist(bio, repurposing_only=REPURPOSING_ONLY)
        ncands = len(result.get("candidates", []))
        log(f"    chem {sym} {time.time()-t0:.1f}s candidates={ncands}")
        return idx, result

    with ThreadPoolExecutor(max_workers=len(top_k)) as ex:
        for fut in as_completed({ex.submit(chem_worker, (i, b)): i for i, b in enumerate(bio_outputs)}):
            idx, chem = fut.result()
            chem_outputs[idx] = chem

    all_candidates = []
    for co in chem_outputs:
        if co:
            all_candidates.extend(co.get("candidates", []))

    return {
        "target": top_k[0],
        "targets": top_k,
        "candidates": all_candidates,
        "pooled_across_k_targets": True,
        "repurposing_only": REPURPOSING_ONLY,
    }


def classify_status(reviewed, expected_drug_up, class_set=None):
    """Return (status_str, hit_record_or_None)."""
    drug_hits = [r for r in reviewed if expected_drug_up in (r.get("drug_name") or "").upper()]
    if drug_hits:
        h = drug_hits[0]
        sm  = h.get("strong_match", False)
        cap = h.get("safety_cap_applied", False)
        if sm and not cap:
            return "HIT", h
        if sm and cap:
            return "HIT_SAFETY_CAPPED", h
        return "FOUND_BELOW_THRESHOLD", h
    # class match?
    if class_set:
        class_hits = [r for r in reviewed if (r.get("drug_name") or "").upper() in class_set]
        if class_hits:
            return "MISS_MECHANISM_CLASS_MATCH", class_hits[0]
    return "MISS", None


def print_reviewed_table(reviewed, label):
    log(f"\n  {label} — top candidates:")
    log(f"  {'Drug':<34} {'Score':>7}  St  Cap  L1  L2  badge")
    log(f"  {'-'*95}")
    for r in reviewed[:15]:
        l1  = r.get("safety_layer1") or {}
        l2  = r.get("safety_layer2") or {}
        l1c = "Y" if l1.get("confirmed") else ("E" if l1.get("api_error") else "n")
        l2c = "Y" if l2.get("confirmed") else ("n" if l2 else "-")
        cap = "Y" if r.get("safety_cap_applied") else "n"
        sm  = "Y" if r.get("strong_match") else "n"
        name  = (r.get("drug_name") or "")[:34]
        score = r.get("composite_score", 0)
        badge = (r.get("status_badge") or "")[:40]
        log(f"  {name:<34} {score:>7.4f}  {sm}   {cap}    {l1c}   {l2c}   {badge}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
log("=" * 70)
log(f"CANONICAL VALIDATION — {time.strftime('%Y-%m-%d %H:%M')}")
log(f"K={TOP_TARGETS_PER_DISEASE}  REPURPOSING_ONLY={REPURPOSING_ONLY}")
log(f"PARENT_MAX_DESCENDANTS={PARENT_MAX_DESCENDANTS}")
log(f"STRONG_MATCH_THRESHOLD={STRONG_MATCH_THRESHOLD}  SAFETY_CAP={SAFETY_CAP}  "
    f"MAX_SAFETY_LAYER2_CANDIDATES={MAX_SAFETY_LAYER2_CANDIDATES}")
log("")

CASES = [
    # (query, expected_drug_upper, class_set, excluded)
    ("idiopathic pulmonary arterial hypertension", "SILDENAFIL",  PDE5_INH, False),
    ("multiple myeloma",                            "THALIDOMIDE", IMIDS,    False),
    ("tuberous sclerosis complex",                  "EVEROLIMUS",  RAPALOGS, False),
    ("polycystic ovary syndrome",                   "METFORMIN",   None,     True),
    ("infantile hemangioma",                        "PROPRANOLOL", None,     True),
]

all_results = []
t_global = time.time()

for query, drug, class_set, expected_excluded in CASES:
    log("=" * 70)
    log(f"CASE: {query!r}  drug={drug}")
    case = {"query": query, "expected_drug": drug}
    t_case = time.time()

    # ── Stage 1 ──────────────────────────────────────────────────────────────
    log(f"  Stage 1: select_for_disease …")
    try:
        targets = select_for_disease(query)
    except DiseaseNotInUniverse as e:
        case["status"] = "EXCLUDED_NOT_IN_UNIVERSE"
        case["notes"]  = str(e)
        log(f"  → DiseaseNotInUniverse: {e}")
        all_results.append(case)
        log("")
        continue
    except Exception as e:
        case["status"] = "ERROR_STAGE1"
        case["notes"]  = traceback.format_exc()
        log(f"  Stage 1 ERROR: {e}")
        all_results.append(case)
        log("")
        continue

    top_k = targets[:TOP_TARGETS_PER_DISEASE]
    log(f"  Stage 1 done: K=5 targets = {[t['target_symbol'] for t in top_k]}")
    case["stage1_targets"] = [
        {"rank": i+1, "symbol": t.get("target_symbol"), "uniprot": t.get("uniprot_id"),
         "score": round(t.get("total_score", 0), 5),
         "method": t.get("target_discovery_method")}
        for i, t in enumerate(top_k)
    ]

    # Record all K targets as explored so canonical-validation runs are
    # visible in the explored_targets accounting, preventing blank auto-explore
    # from silently re-selecting pairs that have already been substantively run.
    # Uses disease_name from the row (canonical form) not the query string.
    try:
        from api import jobs_db as _jdb
        _jdb.init_db()
        for t in top_k:
            _jdb.record_explored(
                t.get("disease_name", query),
                t.get("target_symbol", "?"),
            )
        log(f"  explored_targets: recorded {len(top_k)} pairs")
    except Exception as _e:
        log(f"  WARN: could not record explored pairs: {_e}")

    # For myeloma: also record where CRBN sits in the FULL list
    if "myeloma" in query:
        crbn_rank = None
        for i, t in enumerate(targets):
            if t.get("target_symbol") == "CRBN":
                crbn_rank = i + 1
                break
        case["crbn_full_rank"] = crbn_rank
        case["full_ranked_list_top15"] = [
            {"rank": i+1, "symbol": t.get("target_symbol"),
             "score": round(t.get("total_score", 0), 5),
             "method": t.get("target_discovery_method")}
            for i, t in enumerate(targets[:15])
        ]
        log(f"  CRBN full rank: #{crbn_rank}  (in K=5: {crbn_rank <= 5 if crbn_rank else False})")
        for i, t in enumerate(targets[:15], 1):
            flag = " ← CRBN" if t.get("target_symbol") == "CRBN" else ""
            log(f"    #{i:>2} {t.get('target_symbol'):<12} score={t.get('total_score',0):.5f}  "
                f"{t.get('target_discovery_method','?')}{flag}")

    # PCOS: in-universe but off-label → still run to confirm metformin absent
    if expected_excluded and "pcos" not in query.lower() and "ovary" not in query.lower():
        pass  # hemangioma handled by DiseaseNotInUniverse above

    # ── Stages 2+3: biologist + chemist ──────────────────────────────────────
    # Use PAH checkpoint if available
    pah_checkpoint = "/tmp/pah_pooled.json"
    if "arterial" in query and os.path.exists(pah_checkpoint):
        log(f"  Stages 2+3: loading PAH checkpoint from {pah_checkpoint}")
        with open(pah_checkpoint) as f:
            pooled = json.load(f)
        log(f"  Checkpoint: {len(pooled['candidates'])} pooled candidates")
    else:
        log(f"  Stages 2+3: running biologist + chemist for {len(top_k)} targets (parallel) …")
        t0 = time.time()
        pooled = run_bio_chem_k5(targets)
        log(f"  Stages 2+3 done in {time.time()-t0:.1f}s  pooled candidates={len(pooled['candidates'])}")

    case["pooled_candidate_count"] = len(pooled["candidates"])

    # ── Stage 4: reviewer ─────────────────────────────────────────────────────
    log(f"  Stage 4: reviewer …")
    t0 = time.time()
    try:
        reviewed = run_reviewer(pooled)
    except Exception as e:
        case["status"] = "ERROR_REVIEWER"
        case["notes"]  = traceback.format_exc()
        log(f"  Reviewer ERROR: {e}")
        all_results.append(case)
        log("")
        continue
    log(f"  Reviewer done in {time.time()-t0:.1f}s  scored={len(reviewed)}")

    print_reviewed_table(reviewed, query)

    # ── Classify ──────────────────────────────────────────────────────────────
    status, hit_rec = classify_status(reviewed, drug, class_set)

    if expected_excluded and "ovary" in query:
        # PCOS: disease IS in universe but metformin is off-label
        # the pipeline may still find it; record honestly
        if drug.upper() in [r.get("drug_name","").upper() for r in reviewed]:
            status = "FOUND_OFF_LABEL_UNEXPECTED"
        else:
            status = "EXCLUDED_OFF_LABEL_CONFIRMED"

    case["status"] = status
    case["top15"] = [
        {"drug_name": r.get("drug_name"),
         "score": round(r.get("composite_score", 0), 4),
         "strong_match": r.get("strong_match"),
         "safety_cap_applied": r.get("safety_cap_applied"),
         "status_badge": r.get("status_badge"),
         "target_symbol": r.get("target_symbol"),
         "target_discovery_method": r.get("target_discovery_method"),
         "l1_confirmed":  (r.get("safety_layer1") or {}).get("confirmed"),
         "l1_api_error":  (r.get("safety_layer1") or {}).get("api_error"),
         "l1_flag_type":  (r.get("safety_layer1") or {}).get("flag_type"),
         "l2_confirmed":  (r.get("safety_layer2") or {}).get("confirmed") if r.get("safety_layer2") else None,
         "l2_verdict":    (r.get("safety_layer2") or {}).get("verdict") if r.get("safety_layer2") else None}
        for r in reviewed[:15]
    ]
    if hit_rec:
        case["hit_record"] = {k: v for k, v in hit_rec.items()
                              if k in ("drug_name","composite_score","strong_match",
                                       "safety_cap_applied","status_badge","target_symbol",
                                       "target_discovery_method","safety_layer1","safety_layer2")}

    log(f"\n  >> STATUS: {status}"
        + (f"  hit={hit_rec.get('drug_name')}  score={hit_rec.get('composite_score',0):.4f}" if hit_rec else ""))
    log(f"  Case elapsed: {time.time()-t_case:.0f}s")
    all_results.append(case)
    log("")

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────
log("=" * 70)
log("FINAL SUMMARY")
log(f"  {'Case':<52} {'Status'}")
log(f"  {'-'*75}")
for r in all_results:
    log(f"  {r['query'][:52]:<52} {r.get('status','?')}")

log("")
log(f"Total elapsed: {time.time()-t_global:.0f}s")
log("DONE")

with open(JOUT, "w") as f:
    json.dump(all_results, f, indent=2, default=str)
log(f"JSON written → {JOUT}")
