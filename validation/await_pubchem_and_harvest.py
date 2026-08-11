"""Wait for PubChem to recover, then harvest the pinned snapshot once.

Runs unattended and costs nothing but a probe every few minutes: the harvest
is pure REST (no LLM), so there is no billed work to guard here. It
deliberately does NOT start Study B -- metered pipeline runs stay
explicitly user-started.

    python3 -m validation.await_pubchem_and_harvest [--max-hours 12]

Exits 0 once the snapshot is built (or already complete), 1 on timeout.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data_sources import pubchem_snapshot as snap  # noqa: E402

PROBE = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
         "aspirin/property/InChIKey/JSON")
POLL_S = 300


def healthy() -> bool:
    try:
        return requests.get(PROBE, timeout=20).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=12.0)
    args = ap.parse_args()
    deadline = time.monotonic() + args.max_hours * 3600
    waited = 0

    while time.monotonic() < deadline:
        if healthy():
            print(f"[await] PubChem healthy after {waited // 60} min — "
                  "starting harvest", flush=True)
            proc = subprocess.run(
                [sys.executable, "-m", "validation.build_pubchem_snapshot"],
                cwd=str(ROOT))
            if proc.returncode == 0:
                print(f"[await] harvest complete: {snap.coverage()}",
                      flush=True)
                print("[await] Study B is NOT auto-started (metered run) — "
                      "start it explicitly.", flush=True)
                return
            # A recovery that flaps mid-harvest leaves partial rows; the
            # harvester is resumable, so just wait and retry.
            print("[await] harvest exited non-zero — retrying after poll",
                  flush=True)
        else:
            if waited % 1800 == 0:
                print(f"[await] PubChem still down ({waited // 60} min "
                      "waited)", flush=True)
        time.sleep(POLL_S)
        waited += POLL_S

    raise SystemExit(f"[await] gave up after {args.max_hours}h — PubChem "
                     "never recovered")


if __name__ == "__main__":
    main()
