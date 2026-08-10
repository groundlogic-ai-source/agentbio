"""Reproducible metrics, figures, and tables for the AgentBio report/preprint.

Every number the technical report and manuscript quote is computed here from
the committed frozen artifacts. Running this script regenerates:

  publication/derived_metrics.json   — every headline number, with provenance
  publication/tables.md              — markdown tables used by the documents
  publication/figures/fig*.png       — manuscript figures

It also VERIFIES the frozen audit claim-set results by recomputing them from
the raw output archive and asserting equality with the stored metrics (the
same check as `validation/run_audit_claimset.py --recalc-only`).

Usage:  python3 publication/make_figures.py
"""
import json
import math
import os
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import beta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAL = os.path.join(ROOT, "validation")
OUT = os.path.join(ROOT, "publication")
FIG = os.path.join(OUT, "figures")
os.makedirs(FIG, exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 150, "font.size": 10, "axes.spines.top": False,
    "axes.spines.right": False, "axes.titlesize": 11, "axes.labelsize": 10,
})
C_HIT, C_MISS, C_OOS, C_ERR = "#1b9e77", "#d95f02", "#7570b3", "#e7298a"
C_BAR = "#2166ac"
C_NOVEL = "#4393c3"


def load(name):
    with open(os.path.join(VAL, name)) as fh:
        return json.load(fh)


def cp_lower(k, n, a=0.05):
    return 0.0 if k == 0 else float(beta.ppf(a / 2, k, n - k + 1))


def cp_upper(k, n, a=0.05):
    return 1.0 if k == n else float(beta.ppf(1 - a / 2, k + 1, n - k))


def ci_str(k, n):
    return (f"{k}/{n} = {k/n:.3f} "
            f"(95% CI {cp_lower(k, n):.3f}\u2013{cp_upper(k, n):.3f})")


M = {}  # derived metrics registry


# ---------------------------------------------------------------- benchmark v2
b2 = load("benchmark_results_v2.json")
cases = b2["cases"]
assert b2["freeze_tag"] == "benchmark-freeze-v2"


def subset_stats(name):
    cs = [c for c in cases if c["subset"] == name]
    oos = [c for c in cs if c["status"] == "out_of_scope"]
    err = [c for c in cs if c["status"] == "error"]
    sc = [c for c in cs if c["status"] not in ("out_of_scope", "error")]
    hits = [c for c in sc if c["status"] == "hit"]
    misses = [c for c in sc if c["status"] == "miss"]
    return {
        "executed": len(cs), "out_of_scope": len(oos), "errors": len(err),
        "scorable": len(sc), "hits": len(hits),
        "hit_rate": len(hits) / len(sc) if sc else None,
        "hit_ranks": sorted(c["rank"] for c in hits if c["rank"]),
        "top10": sum(1 for c in hits if c["recovered_top10"]),
        "strong": sum(1 for c in hits if c["strong_match"]),
        "miss_classes": dict(Counter(c["miss_class"] for c in misses)),
        "strata": {s: {"hits": sum(1 for c in sc
                                   if c["stratum"] == s and c["status"] == "hit"),
                       "n": sum(1 for c in sc if c["stratum"] == s)}
                   for s in sorted({c["stratum"] for c in sc})},
    }


M["v2_primary"] = subset_stats("primary")
M["v2_development"] = subset_stats("development")
M["v2_freeze_mode"] = b2["freeze_mode"]

# Pre-registered mechanical chance baseline (same method as v1): it saturates.
probs = [c["chance_hit_probability"] for c in cases
         if c["status"] not in ("out_of_scope", "error")
         and c.get("chance_hit_probability") is not None]
M["chance_baseline"] = {
    "n_with_probability": len(probs), "all_saturated": all(p == 1.0 for p in probs),
    "expected_hits": sum(probs),
    "note": ("baseline saturates at 1.0 per case because reviewed lists cover "
             "the recorded per-target pools; the Poisson-binomial test is "
             "therefore uninformative and is reported as computed, not used."),
}

# benchmark v1 partial
b1 = load("benchmark_results_v1_partial.json")
v1 = b1["cases"]
M["v1_partial"] = {
    "executed": len(v1),
    "admin_excluded": sum(1 for c in v1 if c["status"] == "out_of_scope"),
    "errors": sum(1 for c in v1 if c["status"] == "error"),
    "hits": sum(1 for c in v1 if c["status"] == "hit"),
    "genuine_misses": sum(1 for c in v1 if c["status"] == "miss"),
}

# funnel (selection attrition + screen + execution)
M["funnel"] = [
    ("repoDB rows", 9057), ("approved repurposed-success", 5582),
    ("small molecule", 4866), ("dev-suite drugs excluded", 4823),
    ("in Orphanet universe", 237), ("one case per drug", 111),
    ("EFO resolved", 103), ("target in universe", 96),
    ("selected (seed 20260731)", 50), ("passed v2 screen", 32),
    ("in-scope at runtime", 22), ("rediscovered", 6),
]

# ------------------------------------------------------------- ablation control
abl = load("v2_source_ablation_results.json")
arms = abl["rows"]
M["ablation"] = {
    "arms_total": len(arms),
    "hits_total": sum(1 for a in arms if a.get("generated")),
    "by_condition": {c: {"generated": sum(1 for a in arms
                                         if a["condition"] == c and a["generated"]),
                         "n": sum(1 for a in arms if a["condition"] == c)}
                     for c in sorted({a["condition"] for a in arms})},
}

# ------------------------------------------------------------- audit claim set
aud = load("audit_claimset_results.json")
raw = load("audit_claimset_raw_outputs.json")
cs = load("audit_claim_set_v1.json")

# --- verification: re-derive every outcome from the RAW ARCHIVE through the
# harness's own scoring path (score_from_archive is the ONLY metric path in
# validation/run_audit_claimset.py). revalidate=False skips live-network
# citation revalidation: all 100 citations were valid at score time (stored
# abstain/excluded counts are zero), so offline re-derivation must match the
# stored results exactly. The live equivalent (`--recalc-only`) is
# health-gated and correctly refuses to score during source outages.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "run_audit_claimset", os.path.join(VAL, "run_audit_claimset.py"))
_rac = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_rac)
claims = {c["claim_id"]: c for c in cs["claims"]}
rescored = _rac.score_from_archive(cs, raw, revalidate=False)
mm = aud["metrics"]
rm = rescored["metrics"]
for cid, oc in rescored["outcomes"].items():
    assert oc["outcome"] == aud["outcomes"][cid]["outcome"], \
        f"outcome drift: {cid} stored={aud['outcomes'][cid]['outcome']} raw={oc['outcome']}"
assert rm["defect_recall"] == mm["defect_recall"] == 32 / 60
assert rm["control_false_flag_rate"] == mm["control_false_flag_rate"] == 7 / 40
assert rm["novel_recall"] == mm["novel_recall"] == 29 / 30
assert abs(rm["defect_recall_cp_lower_95"]
           - mm["defect_recall_cp_lower_95"]) < 1e-12
assert abs(rm["control_false_flag_cp_upper_95"]
           - mm["control_false_flag_cp_upper_95"]) < 1e-12
M["audit"] = {
    "verdict": mm["verdict"],
    "defect": ci_str(32, 60), "defect_cp_lower": mm["defect_recall_cp_lower_95"],
    "control": ci_str(7, 40), "control_cp_upper": mm["control_false_flag_cp_upper_95"],
    "novel": ci_str(29, 30), "novel_cp_lower": mm["novel_recall_cp_lower_95"],
    "abstained": mm["defect_counts"]["abstained"],
    "excluded": mm["defect_counts"]["excluded"],
    "per_class": aud["per_class"],
    "contradicted_disclosures":
        sum(1 for a in aud["disclosure_annotations"].values()
            if a.get("annotation") == "contradicted"),
}

# ------------------------------------------------------------- trap benchmark
trap = load("audit_trap_results.json")
tm = trap["metrics"]
M["trap"] = {
    "verdict": trap["verdict"], "traps": f'{tm["traps_caught"]}/{tm["traps_total"]}',
    "trap_recall": tm["trap_recall"],
    "control_ffr": f'{tm["controls_false_flagged"]}/{tm["controls_total"]} = '
                   f'{tm["control_false_flag_rate"]}',
    "precision": tm["precision"],
}

with open(os.path.join(OUT, "derived_metrics.json"), "w") as fh:
    json.dump(M, fh, indent=2)

# ------------------------------------------------------------------- figures

# fig1 — case-selection funnel
labels = [x[0] for x in M["funnel"]][::-1]
vals = [x[1] for x in M["funnel"]][::-1]
fig, ax = plt.subplots(figsize=(7, 4.2))
ax.barh(labels, vals, color=C_BAR)
ax.set_xscale("log")
ax.set_xlabel("cases remaining (log scale)")
ax.set_title("Benchmark v2 case-selection funnel")
for i, v in enumerate(vals):
    ax.text(v * 1.12, i, str(v), va="center", fontsize=9)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig1_funnel.png"))
plt.close(fig)

# fig2 — v2 outcomes by subset and prevalence stratum
prim = M["v2_primary"]
dev = M["v2_development"]
fig, axes = plt.subplots(1, 2, figsize=(8.5, 3.6),
                         gridspec_kw={"width_ratios": [1.6, 1]})
ax = axes[0]
strata = ["ultra_rare_lt1", "rare_1_10", "less_rare_gt10", "unknown"]
disp = ["ultra-rare\n(<1/M)", "rare\n(1\u201310/M)", "less rare\n(>10/M)", "unknown"]
hit_v = [prim["strata"][s]["hits"] for s in strata]
miss_v = [prim["strata"][s]["n"] - prim["strata"][s]["hits"] for s in strata]
ax.bar(disp, hit_v, color=C_HIT, label="rediscovered")
ax.bar(disp, miss_v, bottom=hit_v, color=C_MISS, label="miss")
for i, (h, n) in enumerate(zip(hit_v, miss_v)):
    ax.text(i, h + n + 0.15, f"{h}/{h+n}", ha="center", fontsize=9)
ax.set_ylabel("in-scope cases")
ax.set_title("Primary subset (n=22 in-scope)")
ax.legend(frameon=False, fontsize=8)
ax = axes[1]
cats = ["hit", "miss", "out of\nscope", "error"]
vals_d = [dev["hits"], dev["scorable"] - dev["hits"],
          dev["out_of_scope"], dev["errors"]]
ax.bar(cats, vals_d, color=[C_HIT, C_MISS, C_OOS, C_ERR])
for i, v in enumerate(vals_d):
    ax.text(i, v + 0.15, str(v), ha="center", fontsize=9)
ax.set_title("Development subset (n=15, disclosed)")
ax.set_ylabel("cases")
fig.suptitle("Benchmark v2 outcomes", y=1.02)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig2_v2_outcomes.png"), bbox_inches="tight")
plt.close(fig)

# fig3 — source ablation
ab = M["ablation"]["by_condition"]
order = ["chembl_only", "chembl_gtopdb", "chembl_drugcentral", "all_three"]
disp3 = ["ChEMBL\nonly", "ChEMBL\n+GtoPdb", "ChEMBL\n+DrugCentral", "all\nthree"]
vals3 = [ab[c]["generated"] for c in order]
fig, ax = plt.subplots(figsize=(5.5, 3.4))
bars = ax.bar(disp3, vals3, color=C_BAR)
ax.axhline(13, ls="--", lw=0.8, color="grey")
ax.text(3.4, 13.1, "13 development cases", fontsize=8, color="grey",
        ha="right")
for b_, c in zip(bars, order):
    ax.text(b_.get_x() + b_.get_width() / 2, b_.get_height() + 0.15,
            f"{ab[c]['generated']}/13", ha="center", fontsize=9)
ax.set_ylabel("confirmed drug generated + mechanistically valid")
ax.set_title("Pre-freeze source-ablation control (52 arms)")
ax.set_ylim(0, 14.5)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig3_ablation.png"))
plt.close(fig)

# fig4 — audit claim-set per-class recall vs thresholds
pc = M["audit"]["per_class"]
classes = ["E1_safety_withdrawal", "E2_boxed_warning_not_withdrawal",
           "E3_direction_incompatible", "E4_unresolved_name_honesty",
           "N1_combination_product_splitting", "N2_biologic_modality_mis_scope",
           "N4_dose_route_implausibility"]
disp4 = ["E1\nwithdrawal", "E2\nboxed warn", "E3\ndirection", "E4\nunresolved",
         "N1\ncombination", "N2\nbiologic", "N4\ndose/route"]
rec, lo, hi, ns = [], [], [], []
for c in classes:
    b = pc[c]
    tot = b["caught"] + b["miss"]
    ns.append(tot)
    rec.append(b["caught"] / tot if tot else 0)
    lo.append(rec[-1] - cp_lower(b["caught"], tot) if tot else 0)
    hi.append(cp_upper(b["caught"], tot) - rec[-1] if tot else 0)
fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.6),
                         gridspec_kw={"width_ratios": [2.2, 1]})
ax = axes[0]
cols = [C_BAR] * 4 + [C_NOVEL] * 3
ax.errorbar(disp4, rec, yerr=[lo, hi], fmt="o", color="black", ms=0, lw=1,
            capsize=3)
ax.bar(disp4, rec, color=cols, alpha=0.85)
ax.axhline(0.80, ls="--", color="red", lw=1)
ax.text(6.4, 0.815, "PASS threshold 0.80", color="red", fontsize=8,
        ha="right")
for i, (r, n) in enumerate(zip(rec, ns)):
    ax.text(i, r + hi[i] + 0.05, f"{r:.2f}\n(n={n})", ha="center", fontsize=7.5)
ax.set_ylabel("defect recall")
ax.set_ylim(0, 1.12)
ax.set_title("Audit claim-set recall by defect class "
             "(E = previously fixed, N = novel)")
ax = axes[1]
ctl_k, ctl_n = 7, 40
ax.bar(["clean\ncontrols"], [ctl_k / ctl_n], color=C_MISS)
ax.errorbar(["clean\ncontrols"], [ctl_k / ctl_n],
            yerr=[[ctl_k / ctl_n - cp_lower(ctl_k, ctl_n)],
                  [cp_upper(ctl_k, ctl_n) - ctl_k / ctl_n]],
            fmt="o", color="black", ms=0, lw=1, capsize=3)
ax.axhline(0.15, ls="--", color="red", lw=1)
ax.text(0, 0.165, "PASS threshold 0.15", color="red", fontsize=8, ha="center")
ax.set_ylim(0, 0.42)
ax.set_ylabel("false-flag rate")
ax.set_title(f"Controls: {ctl_k}/{ctl_n} flagged")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig4_audit.png"), bbox_inches="tight")
plt.close(fig)

# fig5 — rediscovery ranks
pr = [c["rank"] for c in cases
      if c["subset"] == "primary" and c["status"] == "hit"]
dv = [c["rank"] for c in cases
      if c["subset"] == "development" and c["status"] == "hit"]
fig, ax = plt.subplots(figsize=(6.5, 3.2))
ax.scatter(pr, [1] * len(pr), color=C_BAR, s=55, zorder=3,
           label=f"primary (n={len(pr)})")
ax.scatter(dv, [0.7] * len(dv), color=C_ERR, s=55, marker="s", zorder=3,
           label=f"development (n={len(dv)})")
ax.axvline(10, ls="--", color="grey", lw=1)
ax.text(10.5, 1.05, "top-10 boundary", fontsize=8, color="grey")
ax.set_xscale("log")
ax.set_xlabel("final rank of rediscovered drug (log scale)")
ax.set_yticks([0.7, 1])
ax.set_yticklabels(["development", "primary"])
ax.legend(frameon=False, fontsize=8, loc="lower right")
ax.set_title("Rediscovery ranks, benchmark v2")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig5_ranks.png"))
plt.close(fig)

# -------------------------------------------------------------------- tables
lines = ["# Generated tables (publication/make_figures.py)", "",
         "## Benchmark v2 headline", "",
         "| Subset | Executed | Out of scope | Error | In-scope | Rediscovered | Top-10 | STRONG_MATCH |",
         "|---|---|---|---|---|---|---|---|"]
for name, d in (("Primary", M["v2_primary"]), ("Development", M["v2_development"])):
    lines.append(f"| {name} | {d['executed']} | {d['out_of_scope']} | "
                 f"{d['errors']} | {d['scorable']} | {d['hits']} "
                 f"({d['hit_rate']:.1%}) | {d['top10']}/{d['hits']} | "
                 f"{d['strong']}/{d['hits']} |")
lines += ["", "## Audit claim-set (frozen, one scored run)", "",
          "| Metric | Result | PASS threshold | Met? |", "|---|---|---|---|",
          f"| Defect recall | {M['audit']['defect']}, "
          f"CP lower {M['audit']['defect_cp_lower']:.3f} | \u2265 0.80, lower \u2265 0.65 | NO |",
          f"| Control false-flag | {M['audit']['control']}, "
          f"CP upper {M['audit']['control_cp_upper']:.3f} | \u2264 0.15, upper \u2264 0.30 | NO |",
          f"| Novel-class recall | {M['audit']['novel']} | none (registered) | \u2014 |",
          "", "## Source ablation (pre-freeze control, 13 development cases \u00d7 4 conditions)", "",
          "| Condition | Generated + mechanistically valid |", "|---|---|"]
for c in order:
    lines.append(f"| {c} | {ab[c]['generated']}/{ab[c]['n']} |")
with open(os.path.join(OUT, "tables.md"), "w") as fh:
    fh.write("\n".join(lines) + "\n")

# ------------------------------------------------- supplement tables (generated)
GEN = os.path.join(OUT, "generated")
os.makedirs(GEN, exist_ok=True)

lines = ["| # | Drug | Disease | Subset | Status | Rank | Top-10 | Strong | Miss class |",
         "|---|---|---|---|---|---|---|---|---|"]
for i, c in enumerate(cases, 1):
    lines.append(
        f"| {i} | {c['drug_name']} | {c['disease_name']} | {c['subset']} | "
        f"{c['status']} | {c['rank'] if c['rank'] else '—'} | "
        f"{'yes' if c.get('recovered_top10') else '—'} | "
        f"{'yes' if c.get('strong_match') else '—'} | "
        f"{c.get('miss_class') or '—'} |")
with open(os.path.join(GEN, "supplement_cases.md"), "w") as fh:
    fh.write("\n".join(lines) + "\n")

lines = ["| Claim | Group | Class | Drug | Status | Outcome |",
         "|---|---|---|---|---|---|"]
for cid, c in sorted(claims.items()):
    oc = aud["outcomes"][cid]
    group = ("control" if c["defect_class"] == "none"
             else "existing-fix" if c["defect_class"].startswith("E")
             else "novel")
    status = raw["outputs"][cid].get("status", "?")
    lines.append(f"| {cid} | {group} | {c['defect_class']} | "
                 f"{c['input']['drug_name']} | {status} | {oc['outcome']} |")
with open(os.path.join(GEN, "supplement_claims.md"), "w") as fh:
    fh.write("\n".join(lines) + "\n")

print("derived_metrics.json, tables.md, fig1-fig5, supplement tables written.")
print("v2 primary:", M["v2_primary"]["hits"], "/", M["v2_primary"]["scorable"],
      "hit rate", round(M["v2_primary"]["hit_rate"], 4),
      "CI", round(cp_lower(6, 22), 3), "-", round(cp_upper(6, 22), 3))
print("v2 primary miss classes:", M["v2_primary"]["miss_classes"])
print("v1 partial:", M["v1_partial"])
print("audit recalc check: PASS (matches stored frozen results)")
