"""
Build the feature-ready, labeled repoDB dataset.

Chronology resolution (per user decision): LEAVE-ONE-OUT, not per-indication
dates. DrugCentral is used ONLY as a sanity filter (is the drug a real,
established product?), never to sequence indications.

prior_repurposing_count for a (drug, disease) pair:
  - current pair is APPROVED : (distinct approved indications for that drug) - 1
  - current pair is FAILED   : (distinct approved indications for that drug)

status labels (4-category):
  - original-approval    : approved pair, drug has no OTHER approved indication
                           (prior_repurposing_count == 0)
  - repurposed-success   : approved pair, drug has >=1 other approved indication
                           (prior_repurposing_count >= 1)
  - genuine-failure      : failed pair whose whyStopped text classifies as
                           EFFICACY_FAILURE
  - administrative-exclude: failed pair classified ADMINISTRATIVE / UNCLEAR,
                           or with no whyStopped text (cannot confirm efficacy
                           failure -> not a usable negative)

A granular `why_stopped_classification` column is kept for audit.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
import pyreadr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_sources.clinicaltrials import HAIKU_MODEL, _anthropic_client  # noqa: E402
import extract_drugcentral  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_RDATA = os.path.join(HERE, "raw", "shiny.RData")
OUT_DIR = os.path.join(HERE, "output")
CLS_CACHE = os.path.join(OUT_DIR, "whystop_classification_cache.json")
SPLIT_SEED = 20260717
DISCOVERY_FRAC = 0.5


def load_repodb() -> pd.DataFrame:
    df = pyreadr.read_r(RAW_RDATA)["drug.fr"]
    keep = ["drug_name", "drug_id", "ind_name", "ind_id", "status", "phase", "DetailedStatus"]
    df = df[keep].copy()
    df["DetailedStatus"] = df["DetailedStatus"].fillna("").str.strip()
    # dedup on the identity of the (drug, indication, status) claim
    df = df.drop_duplicates(subset=["drug_id", "ind_id", "status"]).reset_index(drop=True)
    return df


VALID_CLS = {"EFFICACY_FAILURE", "ADMINISTRATIVE", "UNCLEAR"}
BATCH_SIZE = 40


def _classify_batch(texts: list[str], client) -> list[str]:
    """
    Classify a batch of whyStopped texts in a single LLM call, using the exact
    same taxonomy as data_sources.clinicaltrials._classify_why_stopped. Batching
    is an operational necessity (the AI proxy rate-limits per-text concurrency);
    the classification definitions are unchanged.
    """
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    prompt = (
        "Below are numbered reasons why clinical trials were stopped before "
        "completion. Classify EACH one.\n\n"
        "Categories (reply with exactly one per item):\n"
        "EFFICACY_FAILURE — the trial stopped because the treatment did not work "
        "or caused harm (lack of efficacy, safety concern, adverse events, "
        "futility, DSMB recommendation based on clinical outcome)\n"
        "ADMINISTRATIVE — the trial stopped for a non-clinical reason "
        "(post-marketing commitment fulfilled, funding ended, sponsor or business "
        "decision, low enrollment, protocol design change, study purpose already met)\n"
        "UNCLEAR — cannot determine from the available text\n\n"
        f"Items:\n{numbered}\n\n"
        "Reply with ONLY a JSON array of length "
        f"{len(texts)}, e.g. [\"ADMINISTRATIVE\", \"EFFICACY_FAILURE\", ...], "
        "in the same order as the items. No other text."
    )
    try:
        resp = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=8 * len(texts) + 50,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        start, end = raw.find("["), raw.rfind("]")
        arr = json.loads(raw[start : end + 1])
        out = [(c.strip().upper() if isinstance(c, str) else "UNCLEAR") for c in arr]
        out = [c if c in VALID_CLS else "UNCLEAR" for c in out]
        if len(out) != len(texts):
            return ["UNCLEAR"] * len(texts)
        return out
    except Exception as e:
        print(f"[classify] batch failed ({e}) -> UNCLEAR", flush=True)
        return ["UNCLEAR"] * len(texts)


def classify_failures(df: pd.DataFrame) -> dict[str, str]:
    """Classify each DISTINCT non-empty failed-pair whyStopped text once."""
    os.makedirs(OUT_DIR, exist_ok=True)
    cache: dict[str, str] = {}
    if os.path.exists(CLS_CACHE):
        with open(CLS_CACHE) as f:
            cache = json.load(f)

    failed = df[df["status"] != "Approved"]
    texts = sorted({t for t in failed["DetailedStatus"] if t})
    todo = [t for t in texts if t not in cache]
    print(f"[classify] {len(texts)} distinct failure texts; {len(todo)} need an LLM call", flush=True)

    if todo:
        client = _anthropic_client()
        if client is None:
            raise RuntimeError("Anthropic client unavailable (AI_INTEGRATIONS_ANTHROPIC_* not set)")
        for start in range(0, len(todo), BATCH_SIZE):
            chunk = todo[start : start + BATCH_SIZE]
            labels = _classify_batch(chunk, client)
            for t, lab in zip(chunk, labels):
                cache[t] = lab
            with open(CLS_CACHE, "w") as f:
                json.dump(cache, f)
            print(f"[classify]   {min(start + BATCH_SIZE, len(todo))}/{len(todo)}", flush=True)
    return cache


def qc_identity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Quarantine ambiguous-identity rows before any drug-keyed feature is built.
    repoDB has a combination-therapy cross-join artifact where a single DrugBank
    drug_id maps to several distinct compound names (and vice-versa). Since
    prior_repurposing_count and the DrugCentral join are keyed on drug identity,
    such rows would cross-contaminate counts, so they are removed and written to
    a review file rather than silently kept.
    """
    id_to_names = df.groupby("drug_id")["drug_name"].nunique()
    name_to_ids = df.groupby("drug_name")["drug_id"].nunique()
    bad_ids = set(id_to_names[id_to_names > 1].index)
    bad_names = set(name_to_ids[name_to_ids > 1].index)
    ambiguous = df["drug_id"].isin(bad_ids) | df["drug_name"].isin(bad_names)

    quarantine = df[ambiguous].copy()
    if len(quarantine):
        os.makedirs(OUT_DIR, exist_ok=True)
        quarantine.to_csv(os.path.join(OUT_DIR, "quarantined_ambiguous_identity.csv"), index=False)
    print(f"[qc] quarantined {len(quarantine)} ambiguous-identity rows "
          f"({len(bad_ids)} drug_ids, {len(bad_names)} drug_names)", flush=True)
    return df[~ambiguous].reset_index(drop=True)


def build() -> pd.DataFrame:
    df = load_repodb()
    df = qc_identity(df)
    dc = extract_drugcentral.load()
    db2struct = dc["drugbank_to_struct"]
    struct_min = dc["struct_min_date"]

    # distinct approved indications per drug (leave-one-out base count)
    approved = df[df["status"] == "Approved"]
    n_approved = approved.groupby("drug_id")["ind_id"].nunique().to_dict()

    def prior_count(row) -> int:
        n = n_approved.get(row["drug_id"], 0)
        return max(n - 1, 0) if row["status"] == "Approved" else n

    df["prior_repurposing_count"] = df.apply(prior_count, axis=1)

    # DrugCentral sanity filter (drug-level; NOT used to sequence indications)
    def dc_date(drug_id):
        s = db2struct.get(drug_id)
        return struct_min.get(s) if s is not None else None

    df["dc_struct_id"] = df["drug_id"].map(lambda d: db2struct.get(d))
    df["dc_approval_date"] = df["drug_id"].map(dc_date)
    df["established_product"] = df["dc_approval_date"].notna()

    # classify failures
    cls_cache = classify_failures(df)

    def classify_row(row) -> str:
        if row["status"] != "Approved":
            if not row["DetailedStatus"]:
                return "NO_REASON"
            return cls_cache.get(row["DetailedStatus"], "UNCLEAR")
        return ""

    df["why_stopped_classification"] = df.apply(classify_row, axis=1)

    def status_label(row) -> str:
        if row["status"] == "Approved":
            return "original-approval" if row["prior_repurposing_count"] == 0 else "repurposed-success"
        return "genuine-failure" if row["why_stopped_classification"] == "EFFICACY_FAILURE" else "administrative-exclude"

    df["label"] = df.apply(status_label, axis=1)

    df = stratified_split(df)
    return df


_LABEL_PRIORITY = ["genuine-failure", "original-approval", "repurposed-success", "administrative-exclude"]


def stratified_split(df: pd.DataFrame) -> pd.DataFrame:
    """
    GROUP-AWARE 50/50 discovery/confirmation split. Every row of a given drug_id
    is assigned to the SAME half (no entity leakage between discovery and
    confirmation). Balance is preserved by stratifying drugs on their rarest
    label present, then alternately assigning drugs within each stratum.
    """
    rng = np.random.default_rng(SPLIT_SEED)

    # each drug's stratum = rarest label it contributes (priority order)
    def drug_stratum(labels: set) -> str:
        for lbl in _LABEL_PRIORITY:
            if lbl in labels:
                return lbl
        return "administrative-exclude"

    drug_labels = df.groupby("drug_id")["label"].agg(set)
    drug_rows = df.groupby("drug_id").size().to_dict()
    drug_to_split: dict = {}
    load = {"discovery": 0, "confirmation": 0}
    for stratum in _LABEL_PRIORITY:
        drugs = [d for d, labs in drug_labels.items() if drug_stratum(labs) == stratum]
        rng.shuffle(drugs)
        # greedy: assign the largest drugs first to the currently-lighter half,
        # balancing both row counts and (correlated) prior_repurposing_count
        drugs.sort(key=lambda d: drug_rows[d], reverse=True)
        for d in drugs:
            side = "discovery" if load["discovery"] <= load["confirmation"] else "confirmation"
            drug_to_split[d] = side
            load[side] += drug_rows[d]

    df["split"] = df["drug_id"].map(drug_to_split)
    return df


def report(df: pd.DataFrame) -> None:
    line = "=" * 66
    print(f"\n{line}\nREPO DB LABELED DATASET — SUMMARY\n{line}")
    print(f"total rows (deduped pairs): {len(df)}")

    print("\n-- repoDB raw status --")
    print(df["status"].value_counts().to_string())

    print("\n-- 4-category label --")
    print(df["label"].value_counts().to_string())

    print("\n-- failed-pair whyStopped classification (audit) --")
    print(df[df["status"] != "Approved"]["why_stopped_classification"].value_counts().to_string())

    print("\n-- DrugCentral sanity filter (established_product) --")
    print(df["established_product"].value_counts().to_string())
    print("established among APPROVED drugs' pairs:",
          int(df[df["status"] == "Approved"]["established_product"].sum()), "/",
          int((df["status"] == "Approved").sum()))

    print(f"\n{line}\nprior_repurposing_count DISTRIBUTION (the gated deliverable)\n{line}")
    prc = df["prior_repurposing_count"]
    print("overall describe:")
    print(prc.describe().to_string())
    print("\noverall value counts (capped display at 0..10, then 11+):")
    binned = prc.clip(upper=11)
    vc = binned.value_counts().sort_index()
    for k, v in vc.items():
        key = "11+" if k == 11 else str(int(k))
        print(f"  {key:>3} : {v}")

    print("\nby label (mean / median / max):")
    g = df.groupby("label")["prior_repurposing_count"].agg(["count", "mean", "median", "max"])
    print(g.to_string())

    print("\nby label × prior_repurposing_count (0 vs >=1):")
    ct = pd.crosstab(df["label"], (df["prior_repurposing_count"] >= 1))
    ct.columns = ["prc==0", "prc>=1"]
    print(ct.to_string())

    print(f"\n{line}\nGROUP-AWARE STRATIFIED SPLIT (seed={SPLIT_SEED})\n{line}")
    print(pd.crosstab(df["label"], df["split"], margins=True).to_string())
    spans = df.groupby("drug_id")["split"].nunique()
    print(f"\nleakage check — drugs appearing in BOTH halves: {int((spans > 1).sum())} (must be 0)")

    print("\nprior_repurposing_count mean by split:")
    print(df.groupby("split")["prior_repurposing_count"].mean().to_string())


def main() -> None:
    df = build()
    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, "labeled_dataset.csv")
    df.to_csv(out_csv, index=False)
    report(df)
    print(f"\nwrote {out_csv} ({len(df)} rows, {len(df.columns)} cols)")
    print("columns:", list(df.columns))


if __name__ == "__main__":
    main()
