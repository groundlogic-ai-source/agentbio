"""
Enrich the labeled repoDB dataset with real chemical and biological features
from PubChem (molecular weight, LogP) and ChEMBL (molecule type, max phase,
oral flag).

Usage:
    python3 data_prep/enrich_dataset.py [--concurrency N]

Output:
    data_prep/output/enriched_dataset.csv  — original columns + 5 new ones

New columns added (drug-keyed join):
  pubchem_mw          float | NaN  — molecular weight from PubChem
  pubchem_xlogp       float | NaN  — XLogP from PubChem
  chembl_molecule_type str  | NaN  — "Small molecule" / "Antibody" / etc.
  chembl_max_phase    float | NaN  — highest global phase (0-4) in ChEMBL
  chembl_oral         float | NaN  — 1.0 if ChEMBL oral==True, 0.0 if False

Progress is written to /tmp/enrich_log.txt (and stdout).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_sources.pubchem import get_compound_data
from data_sources.chembl import get_molecule_data

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "output")
INPUT_CSV = os.path.join(OUT_DIR, "labeled_dataset.csv")
OUTPUT_CSV = os.path.join(OUT_DIR, "enriched_dataset.csv")
LOG_FILE = "/tmp/enrich_log.txt"

_DEFAULT_CONCURRENCY = 4


def _log(msg: str, fh=None) -> None:
    line = f"[enrich] {msg}"
    print(line, flush=True)
    if fh is not None:
        fh.write(line + "\n")
        fh.flush()


def enrich_drug(drug_name: str) -> dict:
    """Fetch PubChem + ChEMBL data for one drug. Returns a flat dict."""
    row: dict = {"drug_name": drug_name}
    try:
        pc = get_compound_data(drug_name)
        row["pubchem_mw"] = pc.get("molecular_weight")
        row["pubchem_xlogp"] = pc.get("xlogp")
    except Exception as e:
        row["pubchem_mw"] = None
        row["pubchem_xlogp"] = None
        print(f"[enrich] WARNING pubchem failed for '{drug_name}': {e}", flush=True)

    try:
        cm = get_molecule_data(drug_name)
        row["chembl_molecule_type"] = cm.get("molecule_type")
        mp = cm.get("max_phase")
        row["chembl_max_phase"] = float(mp) if mp is not None else None
        oral = cm.get("oral")
        row["chembl_oral"] = (1.0 if oral else 0.0) if oral is not None else None
    except Exception as e:
        row["chembl_molecule_type"] = None
        row["chembl_max_phase"] = None
        row["chembl_oral"] = None
        print(f"[enrich] WARNING chembl failed for '{drug_name}': {e}", flush=True)

    return row


def run(concurrency: int = _DEFAULT_CONCURRENCY) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as fh:
        _log(f"loading {INPUT_CSV}", fh)
        df = pd.read_csv(INPUT_CSV)
        _log(f"loaded {len(df)} rows, {len(df.columns)} cols", fh)

        drugs = df["drug_name"].dropna().unique().tolist()
        total = len(drugs)
        _log(f"unique drugs to enrich: {total}", fh)

        results: list[dict] = []
        done = 0
        t0 = time.time()

        _log(f"starting enrichment with concurrency={concurrency}", fh)
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(enrich_drug, d): d for d in drugs}
            for fut in as_completed(futures):
                results.append(fut.result())
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    rate = done / max(elapsed, 1)
                    eta = (total - done) / max(rate, 0.01)
                    _log(
                        f"  {done}/{total} done  "
                        f"({elapsed:.0f}s elapsed, ~{eta:.0f}s remaining)",
                        fh,
                    )

        enrich_df = pd.DataFrame(results)
        _log(
            f"enrichment complete in {time.time()-t0:.0f}s — "
            f"joining back to labeled dataset",
            fh,
        )

        merged = df.merge(enrich_df, on="drug_name", how="left")

        for col in ("pubchem_mw", "pubchem_xlogp", "chembl_max_phase", "chembl_oral"):
            filled = merged[col].notna().sum()
            pct = 100 * filled / len(merged)
            _log(f"  {col}: {filled}/{len(merged)} rows filled ({pct:.1f}%)", fh)

        sm_count = (merged["chembl_molecule_type"] == "Small molecule").sum()
        _log(
            f"  chembl_molecule_type: {merged['chembl_molecule_type'].notna().sum()} resolved "
            f"({sm_count} Small molecule)",
            fh,
        )

        merged.to_csv(OUTPUT_CSV, index=False)
        _log(
            f"wrote {OUTPUT_CSV} ({len(merged)} rows, {len(merged.columns)} cols)",
            fh,
        )
        _log("DONE", fh)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CONCURRENCY,
        help="number of parallel API threads (default: 4)",
    )
    args = parser.parse_args()
    run(args.concurrency)


if __name__ == "__main__":
    main()
