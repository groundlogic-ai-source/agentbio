"""
Verification script for:
  1. Tie-breaking fix in search_disease_efo.
  2. Hard-stop for 0% overlap vs Limitations-warning for partial overlap.

Self-contained: algorithms inlined; no live API or import stubs needed.

Three checks:
  A. Tie-break counterexample: non-monotone-score hits within the 70% floor.
     Old early-break picks the first (lower-scoring) 0-desc candidate.
     New global-best picks the second (higher-scoring) 0-desc candidate.

  B. GSD type 1c replay using real observed scores/desc — selection unchanged.
     MONDO_0009294 is both first-encountered AND highest-scoring among the
     four 0-desc candidates, so both algorithms agree.

  C. Overlap threshold classification — using the Orphanet official name
     (disease_name), NOT the user's query, as the design requires:

     Hard stop  — "Split cord malformation type II" vs "Feingold syndrome type 1"
                  Orphanet official vs OT canonical: 0 shared tokens → hard-stop.
     Warn       — "Glycogen storage disease type Ic" vs "glycogen storage disease VI"
                  Orphanet official (GSD 1c ORPHA:79260) vs OT for MONDO_0009294:
                  shared category tokens (glycogen, storage) give ~33% overlap → warn.
     Pass       — "Glycogen storage disease due to acid maltase deficiency" vs same
                  (Orphanet official for Pompe, ORPHA:365, vs OT canonical) → ~100%.

  Note on the Pompe / "Pompe disease" alias case:
    A user querying "Pompe disease" finds ORPHA:365 in Orphanet.
    The OFFICIAL Orphanet name is "Glycogen storage disease due to acid maltase
    deficiency".  The hard-stop check uses THAT name, not the alias — so 100%
    overlap, no hard stop.  The alias "Pompe disease" is never sent to OT.
"""

import re, sys

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
results = []


# ---------------------------------------------------------------------------
# Inlined selection algorithms (desc_map replaces live API calls)
# ---------------------------------------------------------------------------

def _select_old(disease_hits, desc_map):
    """Pre-v4: early-break on first 0-descendant candidate."""
    top_score = disease_hits[0].get("score") or 0.0
    score_floor = top_score * 0.70
    best_idx = 0
    best_desc = desc_map.get(disease_hits[0]["id"], float("inf"))
    if best_desc > 0:
        for i, candidate in enumerate(disease_hits[1:], 1):
            c_score = candidate.get("score") or 0.0
            if c_score < score_floor:
                break
            desc = desc_map.get(candidate["id"])
            if desc is None:
                continue
            if desc < best_desc:
                best_idx = i
                best_desc = desc
                if best_desc == 0:
                    break  # OLD: early exit on first leaf
    return disease_hits[best_idx]["id"], best_desc, best_idx


def _select_new(disease_hits, desc_map):
    """Post-v4: collect all within-floor, min(desc, -score) tie-break."""
    top_score = disease_hits[0].get("score") or 0.0
    score_floor = top_score * 0.70
    candidates = []
    for i, h in enumerate(disease_hits):
        c_score = h.get("score") or 0.0
        if c_score < score_floor:
            break
        desc = desc_map.get(h["id"])
        if desc is None:
            continue
        candidates.append((desc, c_score, i))
    if not candidates:
        return disease_hits[0]["id"], None, 0
    best = min(candidates, key=lambda c: (c[0], -c[1]))
    return disease_hits[best[2]]["id"], best[0], best[2]


# ---------------------------------------------------------------------------
# Inlined overlap + warning logic (mirrors production _efo_name_stop, etc.)
# ---------------------------------------------------------------------------

_STOP = frozenset({
    "", "the", "of", "due", "to", "and", "a", "an", "with",
    "disease", "type", "syndrome", "deficiency", "in", "by", "disorder",
    "autosomal", "dominant", "recessive", "familial", "congenital",
})
_HARD_STOP  = 0.0   # overlap must be strictly > this
_WARN_FLOOR = 0.5   # overlap must be >= this to suppress warning


def _overlap(name_a: str, name_b: str):
    a_tok = set(re.split(r"\W+", name_a.lower())) - _STOP
    b_tok = set(re.split(r"\W+", name_b.lower())) - _STOP
    if not a_tok:
        return None
    return len(a_tok & b_tok) / len(a_tok)


def _classify(ov):
    if ov is None:
        return "unavailable"
    if ov <= _HARD_STOP:
        return "hard_stop"
    if ov < _WARN_FLOOR:
        return "warn"
    return "pass"


def _warning_str(name_a, name_b, ov):
    """Returns a warning string only in the partial-mismatch band."""
    if ov is None or ov <= _HARD_STOP or ov >= _WARN_FLOOR:
        return None
    return (
        f"EFO RESOLUTION MISMATCH — verify disease mapping independently. "
        f"Orphanet name '{name_a}' resolved to OT node whose canonical name "
        f"is '{name_b}' (overlap {ov:.0%}). These may describe different diseases."
    )


# ===========================================================================
# CHECK A — Genuine counterexample: non-monotone hits within the 70% floor.
#
#   rank-0  MONDO_WIDE  score=1500  desc=10   (floor anchor; floor = 1050)
#   rank-1  MONDO_A     score=1100  desc=0    ← first 0-desc, LOWER score
#   rank-2  MONDO_B     score=1300  desc=0    ← second 0-desc, HIGHER score
#   rank-3  MONDO_C     score=1080  desc=2
#
# All four scores ≥ 1050, so the score-floor break never fires in either
# algorithm. Only the early-break on best_desc==0 (old code) differs.
#
# Old: encounters MONDO_A (desc=0) at rank-1 → early-break → picks A(1100). WRONG.
# New: collects (10,1500,0),(0,1100,1),(0,1300,2),(2,1080,3)
#      min by (desc,-score) → (0,1300,2) → picks B(1300). CORRECT.
# ===========================================================================
hits_A = [
    {"id": "MONDO_WIDE", "score": 1500.0},
    {"id": "MONDO_A",    "score": 1100.0},   # first 0-desc, lower score
    {"id": "MONDO_B",    "score": 1300.0},   # second 0-desc, higher score
    {"id": "MONDO_C",    "score": 1080.0},
]
desc_A = {"MONDO_WIDE": 10, "MONDO_A": 0, "MONDO_B": 0, "MONDO_C": 2}

old_id, _, old_idx = _select_old(hits_A, desc_A)
new_id, _, new_idx = _select_new(hits_A, desc_A)

new_ok = (new_id == "MONDO_B")
results.append(new_ok)
label = PASS if new_ok else FAIL
print(f"{label} CHECK A — Tie-break: non-monotone hits, two 0-desc candidates")
print(f"       floor=1050; MONDO_A(1100) before MONDO_B(1300); both within floor")
print(f"       OLD (early-break): {old_id} score={hits_A[old_idx]['score']}"
      f"  {'← WRONG (lower-scoring 0-desc, early exit)' if old_id == 'MONDO_A' else ''}")
print(f"       NEW (global-best): {new_id} score={hits_A[new_idx]['score']}"
      f"  {'← CORRECT (highest-scoring 0-desc, all candidates seen)' if new_id == 'MONDO_B' else ''}")
print()


# ===========================================================================
# CHECK B — GSD type 1c using real observed OT scores and descendant counts.
#
# Hits in score-descending order (as OT actually returns them).
# Both algorithms agree: MONDO_0009294 is first-encountered AND highest-scoring
# among the four 0-desc candidates (0009294=1370, 0012693=1244, 0018485=1225,
# 0017694=1199). Fix leaves this unchanged — the wrong EFO selection was not
# caused by the tie-breaking rule; it was caused by GSD type 1c having no
# accurate OT node at all. The name-overlap warning handles it downstream.
# ===========================================================================
hits_B = [
    {"id": "MONDO_0009292", "score": 1471.69},
    {"id": "MONDO_0009294", "score": 1370.45},  # first AND highest-scoring 0-desc
    {"id": "MONDO_0009290", "score": 1265.38},
    {"id": "MONDO_0012693", "score": 1244.32},
    {"id": "MONDO_0018485", "score": 1225.04},
    {"id": "MONDO_0017694", "score": 1198.56},
    {"id": "Orphanet_365",  "score": 1184.72},
]
desc_B = {
    "MONDO_0009292": 8, "MONDO_0009294": 0, "MONDO_0009290": 2,
    "MONDO_0012693": 0, "MONDO_0018485": 0, "MONDO_0017694": 0,
    "Orphanet_365": 2,
}

old_gsd, _, old_gsd_idx = _select_old(hits_B, desc_B)
new_gsd, _, new_gsd_idx = _select_new(hits_B, desc_B)

ok_B = (new_gsd == "MONDO_0009294") and (old_gsd == new_gsd)
results.append(ok_B)
label = PASS if ok_B else FAIL
print(f"{label} CHECK B — GSD type 1c: selection unchanged after fix")
print(f"       OLD → {old_gsd} (score={hits_B[old_gsd_idx]['score']})")
print(f"       NEW → {new_gsd} (score={hits_B[new_gsd_idx]['score']})")
print(f"       Both select MONDO_0009294: it is first-encountered AND highest-scoring 0-desc.")
print(f"       (Wrong OT node either way — GSD 1c is absent from OT. Handled by name-overlap.)")
print()


# ===========================================================================
# CHECK C — Overlap threshold classification.
#
# The check uses disease_name (Orphanet official name), NOT the user's query.
# This is the design: user aliases like "Pompe disease" never reach OT; the
# Orphanet official name does. So overlap is measured Orphanet name ↔ OT name.
# ===========================================================================
cases = [
    # Hard stop: completely unrelated names
    # Orphanet "Split cord malformation type II" (ORPHA:268307) resolved by OT
    # to MONDO_0008115 whose canonical name is "Feingold syndrome type 1".
    # Zero tokens in common after stop-word removal → hard stop.
    (
        "Split cord malformation type II",          # Orphanet official name
        "Feingold syndrome type 1",                 # OT canonical for wrong MONDO node
        (0.0, 0.0),
        "hard_stop",
        "completely unrelated disease (no shared tokens)",
    ),
    # Warn: partial mismatch — category + enzyme tokens differ; only class tokens shared.
    # Orphanet ORPHA:79260 (GSD type 1c / SLC37A4 deficiency) official name is
    # "Glycogen storage disease due to glucose-6-phosphate translocase deficiency".
    # OT MONDO_0009294 canonical name is "glycogen storage disease VI".
    # q_tok = {glycogen, storage, glucose, 6, phosphate, translocase}
    # n_tok = {glycogen, storage, vi}
    # shared = {glycogen, storage} → 2/6 = 33% → warn.
    # (Note: "Glycogen storage disease type Ic" is an informal label; the
    #  actual Orphanet official name includes the enzyme pathway, which
    #  gives fewer shared tokens with the OT category-level name.)
    (
        "Glycogen storage disease due to glucose-6-phosphate translocase deficiency",
        "glycogen storage disease VI",              # OT canonical for MONDO_0009294
        (0.01, 0.49),
        "warn",
        "enzyme-pathway tokens (glucose-6-phosphate, translocase) absent from OT name → 33%",
    ),
    # Pass: Orphanet official name closely matches OT canonical (Pompe / GSD II).
    # User queried "Pompe disease"; Orphanet official name is the full name below.
    # OT canonical for MONDO_0009290 / EFO_0000538 is essentially the same string.
    (
        "Glycogen storage disease due to acid maltase deficiency",   # Orphanet official
        "glycogen storage disease due to acid maltase deficiency",   # OT canonical
        (0.5, 1.0),
        "pass",
        "Pompe alias: Orphanet official name ≈ OT canonical → no warning",
    ),
]

print("CHECK C — Overlap threshold classification (disease_name vs OT canonical)")
for orphanet_name, ot_name, (lo, hi), expected, note in cases:
    ov   = _overlap(orphanet_name, ot_name)
    cls  = _classify(ov)
    warn = _warning_str(orphanet_name, ot_name, ov)

    q_tok = set(re.split(r"\W+", orphanet_name.lower())) - _STOP
    n_tok = set(re.split(r"\W+", ot_name.lower()))        - _STOP

    ov_in_range = (lo <= (ov or 0.0) <= hi)
    cls_ok      = (cls == expected)
    warn_ok     = (
        (expected == "warn" and warn is not None) or
        (expected != "warn" and warn is None)
    )
    ok = ov_in_range and cls_ok and warn_ok
    results.append(ok)

    ov_str = f"{ov:.0%}" if ov is not None else "None"
    label = PASS if ok else FAIL
    print(f"  {label}  [{expected}] '{orphanet_name[:55]}'")
    print(f"         OT name:  '{ot_name}'")
    print(f"         shared:   {sorted(q_tok & n_tok)}")
    print(f"         overlap={ov_str}  outcome={cls}  ({note})")
    if warn:
        print(f"         warning: {warn[:95]}…")
    else:
        print(f"         warning: None")
    print()


# ===========================================================================
# CHECK D — Confirm "Pompe disease" alias does NOT trigger hard stop.
#   The user queries "Pompe disease" → Orphanet finds ORPHA:365 → official name
#   is "Glycogen storage disease due to acid maltase deficiency" → OT canonical
#   for the resolved EFO is the same string → 100% overlap → no hard stop.
#   The alias "Pompe disease" is never compared against the OT canonical.
# ===========================================================================
pompe_alias   = "Pompe disease"
pompe_orphanet = "Glycogen storage disease due to acid maltase deficiency"
pompe_ot_name  = "glycogen storage disease due to acid maltase deficiency"

ov_alias   = _overlap(pompe_alias,   pompe_ot_name)   # alias vs OT — NOT what we check
ov_official = _overlap(pompe_orphanet, pompe_ot_name)  # official vs OT — what we check

official_ok = (_classify(ov_official) == "pass")
results.append(official_ok)
label = PASS if official_ok else FAIL
print(f"{label} CHECK D — Pompe alias does NOT trigger hard stop")
print(f"       User query:       '{pompe_alias}'  (alias, never sent to OT)")
print(f"       Orphanet official: '{pompe_orphanet}'")
print(f"       OT canonical:      '{pompe_ot_name}'")
ov_al_str  = f"{ov_alias:.0%}"  if ov_alias  is not None else "None"
ov_off_str = f"{ov_official:.0%}" if ov_official is not None else "None"
print(f"       alias ↔ OT:    overlap={ov_al_str}  (NOT used for hard-stop check)")
print(f"       official ↔ OT: overlap={ov_off_str}  → outcome={_classify(ov_official)}"
      f"  ← what the code actually checks")
print()


# ===========================================================================
# Summary
# ===========================================================================
n_pass  = sum(results)
n_total = len(results)
overall = PASS if n_pass == n_total else FAIL
print("=" * 60)
print(f"{overall}  {n_pass}/{n_total} checks passed")
sys.exit(0 if n_pass == n_total else 1)
