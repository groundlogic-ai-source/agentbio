"""Harvest the pinned PubChem snapshot for the bounded drug universe.

Run once (when PubChem is healthy); the output is committed and pinned:

    python3 -m validation.build_pubchem_snapshot            # harvest/resume
    python3 -m validation.build_pubchem_snapshot --coverage # inspect only

Design constraints, all learned the hard way in this project:

* **Health-gated.** Refuses to start against a degraded PubChem, because a
  503 storm would otherwise be frozen into the snapshot as thousands of
  bogus "unresolved" rows.
* **Failures are never written.** Only genuine API answers are persisted --
  a resolved record, or an authoritative 404-style non-resolution. Transient
  errors leave no row so a later resume retries them.
* **Resumable and idempotent.** Re-running only fetches missing names.
* **Polite.** Serial with a small delay: this tool exists because we once
  fired ~3k doomed lookups and pushed PubChem into ServerBusy.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_sources import pubchem_snapshot as snap  # noqa: E402
from data_sources.pubchem import (  # noqa: E402
    get_compound_data, get_drug_classification, is_resolvable_name)

CSV_PATH = ROOT / "data_prep" / "output" / "enriched_dataset.csv"
PROBE = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
         "aspirin/property/InChIKey/JSON")
DELAY_S = 0.25


def _universe() -> list[str]:
    """Distinct drug names in the repoDB dataset -- the bounded set every
    study draws from."""
    names: dict[str, str] = {}
    with open(CSV_PATH, newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("drug_name") or "").strip()
            if raw and is_resolvable_name(raw):
                names.setdefault(" ".join(raw.split()).casefold(), raw)
    return [names[k] for k in sorted(names)]


def _health_gate() -> None:
    try:
        resp = requests.get(PROBE, timeout=20)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"[snapshot] REFUSED: PubChem probe failed: {exc}")
    if resp.status_code != 200:
        raise SystemExit(
            f"[snapshot] REFUSED: PubChem unhealthy (HTTP "
            f"{resp.status_code}). Harvesting now would freeze an outage "
            "into the snapshot. Retry when it recovers.")


def build() -> None:
    _health_gate()
    names = _universe()
    conn = snap._connect(readonly=False)
    conn.executescript(snap.SCHEMA)
    have = {r[0] for r in conn.execute("SELECT query_name FROM compound")}
    todo = [n for n in names if snap._norm(n) not in have]
    print(f"[snapshot] universe={len(names)} present={len(have)} "
          f"todo={len(todo)}", flush=True)

    written = failed = 0
    for i, name in enumerate(todo, 1):
        pc = get_compound_data(name)
        # Distinguish "PubChem says no such compound" (authoritative, worth
        # persisting) from "the call failed" (transient, must not persist).
        transient = (not pc.get("resolved")
                     and (pc.get("error") or "").lower().find(
                         "could not resolve") == -1)
        if transient:
            failed += 1
            continue
        atc: list[str] = []
        known = False
        if pc.get("inchikey"):
            cls = get_drug_classification(pc["inchikey"])
            known = bool(cls.get("is_known_drug"))
            atc = list(cls.get("atc_codes") or [])
        conn.execute(
            "INSERT OR REPLACE INTO compound (query_name, display_name, "
            "inchikey, canonical_smiles, molecular_weight, xlogp, "
            "is_known_drug, atc_codes, resolved, harvested_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (snap._norm(name), name, pc.get("inchikey"),
             pc.get("canonical_smiles"), pc.get("molecular_weight"),
             pc.get("xlogp"), int(known), ",".join(atc),
             int(bool(pc.get("resolved"))),
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        written += 1
        if i % 50 == 0:
            print(f"[snapshot]   {i}/{len(todo)} written={written} "
                  f"transient_skipped={failed}", flush=True)
        time.sleep(DELAY_S)

    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("source", "PubChem PUG REST"))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("universe", "repoDB enriched_dataset distinct drug_name"))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                 ("last_harvest_utc",
                  datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    print(f"[snapshot] done: wrote {written}, skipped {failed} transient",
          flush=True)
    print(f"[snapshot] coverage: {snap.coverage()}", flush=True)
    if failed:
        print("[snapshot] NOTE: re-run to retry transiently failed names.",
              flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", action="store_true",
                    help="print snapshot coverage and exit")
    args = ap.parse_args()
    if args.coverage:
        if not snap.available():
            raise SystemExit("[snapshot] no snapshot built yet")
        print(snap.coverage())
        return
    build()


if __name__ == "__main__":
    main()
