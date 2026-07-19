"""
Sandboxed AgentBio cross-reference investigation (Feature 2).

Fully separate from the live API. Calls scoring feature functions directly in
an isolated context. Never creates rows in jobs.db, explored_targets, or any
production table.

Output: data_prep/output/sandbox_note.txt

⚠️  ANECDOTAL NOTE — NOT STATISTICAL EVIDENCE ⚠️
The AgentBio candidate pool is a filtered, non-random population (high-tractability
rare-disease / NTD targets that cleared OT, ChEMBL, and AlphaFold gates). It cannot
serve as an independent replication sample for repoDB-derived statistical findings.
Every observation below is anecdotal. Nothing here confirms any hypothesis from the
bisociation pipeline.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.join(HERE, "..")
DATA_CSV = os.path.join(HERE, "output", "labeled_dataset.csv")
TOP_JSON = os.path.join(WORKSPACE, "output", "top_candidates.json")
OUT_TXT = os.path.join(HERE, "output", "sandbox_note.txt")

# features.py + hypothesis_registry live in data_prep/
sys.path.insert(0, HERE)
import features as F  # noqa: E402
import hypothesis_registry as R  # noqa: E402


def _load_fdr_passing_hypotheses() -> list[dict]:
    """
    Return bisociation_history rows for hypotheses that passed discovery FDR.
    We use history rather than log so we have feature_spec-adjacent data like
    domain_description and the mechanistic note (outcome_note).
    """
    hist = R.load_history()
    if hist.empty or "discovery_pass" not in hist.columns:
        return []
    passing = hist[hist["discovery_pass"].astype(str).str.lower() == "true"]
    return passing.to_dict("records")


def _load_log_specs() -> dict[str, dict]:
    """
    Build a map of hypothesis_id → feature_spec by reading the log.
    feature_spec is not stored directly in the log (it's a code artefact), but we
    can reconstruct enough from the hypothesis_text and discovery_test_type to look
    up the features module's computed series. Since we can't recover the original
    DSL op from the log alone, we record what we know.
    """
    log = R.load_log()
    if log.empty:
        return {}
    specs: dict[str, dict] = {}
    for _, row in log.iterrows():
        hid = str(row.get("hypothesis_id", ""))
        if hid and hid not in specs:
            specs[hid] = {
                "test_type": str(row.get("test_type", "")),
                "hypothesis_text": str(row.get("hypothesis_text", "")),
                "significance_threshold": row.get("significance_threshold", 0.05),
                "correction_method": row.get("correction_method", "benjamini_hochberg"),
            }
    return specs


def _load_top_candidates() -> list[dict]:
    if not os.path.exists(TOP_JSON):
        return []
    with open(TOP_JSON) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "candidates" in data:
        return data["candidates"]
    return []


def _load_dataset() -> pd.DataFrame | None:
    if not os.path.exists(DATA_CSV):
        return None
    return pd.read_csv(DATA_CSV)


def _framed(df: pd.DataFrame, framing: str) -> pd.DataFrame:
    """Mirror the run_discovery._framed() helper — no import needed."""
    if framing == "narrow":
        return df[df["label"].isin(["repurposed-success", "genuine-failure"])].copy()
    return df[df["label"].isin(
        ["repurposed-success", "genuine-failure", "administrative-exclude"]
    )].copy()


def _outcome(sub: pd.DataFrame) -> pd.Series:
    return (sub["label"] == "repurposed-success").astype(int).rename("y")


def _investigate_hypothesis(
    hyp: dict,
    df: pd.DataFrame,
    top_candidates: list[dict],
    framing: str = "narrow",
) -> str | None:
    """
    Cross-reference one FDR-passing hypothesis against AgentBio top_candidates.
    Returns a text observation, or None if no interesting overlap is found.

    We cannot reconstruct the original feature_spec from the CSV log alone.
    Instead we look for keyword-level overlaps between the hypothesis text /
    domain and top_candidates' drug/indication names, and flag them.
    This is explicitly anecdotal — a keyword match is NOT a statistical test.
    """
    if not top_candidates:
        return None

    hyp_text = str(hyp.get("resulting_hypothesis_text", ""))
    domain = str(hyp.get("domain_description", hyp.get("domain", "")))
    test_type = str(hyp.get("discovery_test_type", ""))
    fdr_p = hyp.get("discovery_fdr_p", "")
    conf_pass = str(hyp.get("confirmation_pass", "")).lower()

    # Pull keywords from the hypothesis text (naïve but sufficient for anecdote)
    words = set(
        w.lower().strip("(),.;:")
        for w in hyp_text.split()
        if len(w) > 4
    )
    # Remove very common words
    stopwords = {
        "drugs", "higher", "lower", "repurposing", "success", "show", "with",
        "their", "that", "than", "these", "those", "which", "from", "have",
        "been", "more", "drug", "disease", "target", "would", "could", "will",
    }
    keywords = words - stopwords

    matches = []
    for cand in top_candidates[:30]:  # top 30 only
        cand_drug = str(cand.get("drug_name", cand.get("drug", ""))).lower()
        cand_ind = str(cand.get("ind_name", cand.get("disease", cand.get("indication", "")))).lower()
        cand_target = str(cand.get("gene_symbol", cand.get("target", ""))).lower()
        combined = f"{cand_drug} {cand_ind} {cand_target}"
        hits = [kw for kw in keywords if kw in combined]
        if hits:
            matches.append({
                "candidate": f"{cand_drug} / {cand_ind} / {cand_target}",
                "keywords_matched": hits[:4],
                "tractability_score": cand.get("tractability_score", "—"),
                "unmet_need_score": cand.get("unmet_need_score", "—"),
            })

    if not matches:
        return None

    lines = [
        f"  Hypothesis [{domain}]: \"{hyp_text[:100]}...\"" if len(hyp_text) > 100 else f"  Hypothesis [{domain}]: \"{hyp_text}\"",
        f"    Discovery FDR q={fdr_p}   confirmation_pass={conf_pass}   test={test_type}",
        f"    AgentBio candidates with overlapping keywords (anecdotal):",
    ]
    for m in matches[:5]:
        lines.append(
            f"      • {m['candidate']} "
            f"(T={m['tractability_score']}, U={m['unmet_need_score']}) "
            f"keywords: {m['keywords_matched']}"
        )

    return "\n".join(lines)


def run_sandbox() -> None:
    ts = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.join(HERE, "output"), exist_ok=True)

    passing = _load_fdr_passing_hypotheses()
    top_candidates = _load_top_candidates()
    df = _load_dataset()

    header = textwrap.dedent(f"""
    ╔══════════════════════════════════════════════════════════════════════════╗
    ║          AGENTBIO SANDBOX — ANECDOTAL RESEARCH NOTE                    ║
    ║          Generated: {ts[:19]} UTC                         ║
    ╠══════════════════════════════════════════════════════════════════════════╣
    ║  ⚠️  THIS FILE IS ANECDOTAL — NOT STATISTICAL EVIDENCE  ⚠️             ║
    ║                                                                          ║
    ║  AgentBio's candidate pool is a filtered, non-random population.         ║
    ║  It cannot replicate repoDB-derived statistical findings.                ║
    ║  Every observation below is anecdotal only. Nothing here confirms        ║
    ║  any hypothesis from the bisociation pipeline.                           ║
    ╚══════════════════════════════════════════════════════════════════════════╝

    Source files:
      hypothesis_log:     (Postgres table hypothesis_log)
      bisociation_history:(Postgres table bisociation_history)
      top_candidates:     {TOP_JSON}
      labeled_dataset:    {DATA_CSV}

    FDR-passing hypotheses found: {len(passing)}
    AgentBio top candidates loaded: {len(top_candidates)}
    Dataset rows: {len(df) if df is not None else "NOT FOUND"}
    """).lstrip()

    observations: list[str] = []
    for hyp in passing:
        obs = _investigate_hypothesis(hyp, df if df is not None else pd.DataFrame(), top_candidates)
        if obs:
            observations.append(obs)

    body_lines = []
    if not passing:
        body_lines.append(
            "No FDR-passing hypotheses found yet — run run_discovery.py first."
        )
    elif not observations:
        body_lines.append(
            "No keyword overlap found between FDR-passing hypotheses and top candidates.\n"
            "This is not surprising — AgentBio ranks by tractability/unmet-need, not by\n"
            "the bisociation features."
        )
    else:
        body_lines.append(
            f"Found {len(observations)} anecdotal overlap(s) for human review:\n"
            "(Keyword overlap only — NOT a statistical test.  Requires wet-lab validation.)\n"
        )
        body_lines.extend(observations)

    footer = textwrap.dedent("""

    ── END OF ANECDOTAL NOTE ─────────────────────────────────────────────────
    This note is produced by data_prep/agentbio_sandbox.py and is explicitly
    labeled anecdotal.  It is never presented as statistical confirmation of
    any repoDB-derived finding.  For statistical results, see:
      data_prep/registry/hypothesis_log.csv
      data_prep/registry/bisociation_history.csv
    """)

    content = header + "\n".join(body_lines) + footer

    with open(OUT_TXT, "w") as f:
        f.write(content)

    print(content)
    print(f"\n→ Written to {OUT_TXT}")


if __name__ == "__main__":
    run_sandbox()
