#!/usr/bin/env bash
# One-off: fetch the official DrugCentral 11/01/2023 Postgres dump from the
# Internet Archive Wayback snapshot (origin host unmtid-dbs.net is down).
# The raw dump stays in /tmp (never committed); a later step derives the small
# two-table snapshot that IS committed.
#
# Wayback honors HTTP Range (verified 206), so resume is safe — but ONLY with
# a single writer. Exactly one instance of this script must run at a time.
set -u
OUT=/tmp/drugcentral_dump_11012023.sql.gz
URL="https://web.archive.org/web/20260301100338id_/https://unmtid-dbs.net/download/drugcentral.dump.11012023.sql.gz"
# Pinned at first retrieval (2026-08-08); the download is rejected if the
# bytes ever differ.
EXPECTED_SHA256="055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f"
EXPECTED=1400714190
rm -f /tmp/drugcentral_download.done

# Single-writer lock (best-effort; the workflow manager restarts cleanly).
exec 9>/tmp/drugcentral_download.lock
if ! flock -n 9; then
  echo "[dl] another instance holds the lock — exiting"
  exit 0
fi

for i in $(seq 1 30); do
  size=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
  if [ "$size" -ge "$EXPECTED" ]; then
    echo "[dl] complete: $size bytes"
    break
  fi
  echo "[dl] attempt $i $(date -u +%Y-%m-%dT%H:%M:%SZ) have=$size want=$EXPECTED"
  # 40-min per-attempt ceiling: at observed ~1.5MB/s the full file needs ~16 min.
  curl -sS -C - -o "$OUT" --retry 3 --retry-delay 10 -m 2400 "$URL" || true
  sleep 5
done
size=$(stat -c%s "$OUT" 2>/dev/null || echo 0)
if [ "$size" -ge "$EXPECTED" ]; then
  if ! echo "$EXPECTED_SHA256  $OUT" | sha256sum -c -; then
    echo "FAILED sha256-mismatch" > /tmp/drugcentral_download.done
    exit 1
  fi
  sha256sum "$OUT" | tee /tmp/drugcentral_dump.sha256
  echo "DONE" > /tmp/drugcentral_download.done
else
  echo "FAILED size=$size" > /tmp/drugcentral_download.done
fi
