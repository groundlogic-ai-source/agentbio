#!/usr/bin/env bash
# export_public_repo.sh — build the sanitized public mirror of this repo.
#
# Strips internal-only paths and oversized raw archives from ALL history while
# preserving commit dates, messages, authors, and tags. File contents are
# untouched (blob hashes verify identically in the private archive), except
# that expired third-party presigned-URL credentials are redacted by
# replace-text so GitHub push protection does not reject the push.
#
# Omitted large files are listed, with SHA-256, in
# validation/private_archive_manifest.md.
#
# Usage:  scripts/export_public_repo.sh [SRC_DIR] [DST_DIR]
set -euo pipefail

SRC="${1:-/home/runner/workspace}"
DST="${2:-/tmp/agentbio-public}"

RULES="$(mktemp)"
cat > "$RULES" <<'EOF'
regex:ASIA[A-Z0-9]{16}==>***REDACTED-AWS-KEY***
regex:X-Amz-Signature=[0-9a-f]+==>X-Amz-Signature=REDACTED
regex:X-Amz-Credential=REDACTED&\s"']+==>X-Amz-Credential=REDACTED
regex:X-Amz-Security-Token=REDACTED&\s"']+==>X-Amz-Security-Token=REDACTED
EOF

rm -rf "$DST"
git clone --no-local "$SRC" "$DST"
cd "$DST"

git filter-repo --force \
  --path .agents --path outreach --path artifacts/mockup-sandbox \
  --path cache/cache.db --path checkpoints.db \
  --path data_prep/raw/dc_dump.sql.gz \
  --path validation/triage_discrimination_studyb_checkpoint.jsonl \
  --path .gitattributes \
  --invert-paths \
  --strip-blobs-bigger-than 20M \
  --replace-text "$RULES"
rm -f "$RULES"

echo "== export verification =="
echo "commits: $(git rev-list --count HEAD)"
echo "tags: $(git tag -l | tr '\n' ' ')"
echo "internal paths remaining in history: $(git rev-list --objects --all | grep -cE ' (\.agents|outreach|artifacts/mockup-sandbox)(/|$)' || true)"
echo "LFS pointers remaining in all refs: $(git lfs ls-files --all 2>/dev/null | wc -l)"
echo "blobs >20MB remaining: $(git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectsize)' 2>/dev/null | awk '$1=="blob" && $2>20000000' | wc -l)"
for term in REMEDi4ALL "Rare Beacon" REPO4EU "Every Cure"; do
  n=$(git log --all --oneline -S"$term" -- . 2>/dev/null | wc -l)
  echo "pickaxe '$term' anywhere in exported history: $n"
done
