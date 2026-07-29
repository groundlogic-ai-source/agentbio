"""
One-time backfill: insert the 5 Sol-proposed domains from run-9dbfd7b5 that were
silently dropped by the lead reviewer and therefore never recorded in
bisociation_history.  Future generation runs will then see them in the exclusion
prompt and not re-propose / re-discard the same ground.

Run once from the workspace root:
    python3 -m data_prep.backfill_9dbfd7b5

Idempotent: _insert_history_row uses ON CONFLICT DO NOTHING, so running it a
second time is harmless.
"""
from __future__ import annotations

import datetime as dt
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hypothesis_registry as R

# Original run metadata (run-9dbfd7b5 was from 2025-07 based on task context).
# We use an approximate timestamp; the exact second doesn't affect any logic.
RUN_ID = "run-9dbfd7b5"
SESSION_TS = "2025-07-01T00:00:00+00:00"  # approximate

# Sol's 5 proposed domains that were silently dropped by the lead reviewer.
# Each becomes one history row with discovery_pass=NULL and an outcome_note
# explaining the discard reason (retroactively reconstructed as "dropped by lead
# reviewer").  No feature_spec or statistical test data — they never reached
# testing.
MISSING_SOL_DOMAINS = [
    "burn-in screening",
    "standard-gauge interoperability",
    "Knudsen molecular sieving",
    "adversarial concept drift",
    "Hubbert decline",
]

BACKFILL_NOTE = (
    "DISCARDED: domain silently dropped by lead reviewer "
    "(not included in consolidated output) — back-filled from run log"
)


def main() -> None:
    rows = []
    for i, domain in enumerate(MISSING_SOL_DOMAINS, start=1):
        hid = f"{RUN_ID}-BACKFILL-S{i:02d}"
        rows.append({
            "test_id": "",
            "hypothesis_id": hid,
            "run_id": RUN_ID,
            "session_timestamp": SESSION_TS,
            "domain_description": domain,
            "proposing_llm": "Sol",
            "resulting_hypothesis_text": f"[{domain}] (hypotheses not recorded — domain dropped before testing)",
            "discovery_test_type": "",
            "outcome_framing": "",
            "discovery_raw_p": None,
            "discovery_fdr_p": None,
            "discovery_pass": None,
            "confirmation_pass": None,
            "confirmation_raw_p": None,
            "confound_check_summary": None,
            "outcome_note": BACKFILL_NOTE,
            "archived": False,
            "feature_spec": None,
            "novelty_tag": None,
        })

    print(f"Inserting {len(rows)} backfill rows for {RUN_ID}...", flush=True)
    R.append_history_rows(rows)
    print("Done.", flush=True)

    # Verify: print the history blurb so we can confirm the domains appear.
    print("\n=== _history_blurb() after backfill ===")
    from run_discovery import _history_blurb
    print(_history_blurb())


if __name__ == "__main__":
    main()
