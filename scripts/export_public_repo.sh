#!/usr/bin/env bash
# export_public_repo.sh - build the sanitized public mirror of this repo.
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
  --path research \
  --path output \
  --path cache/cache.db \
  --path-glob 'checkpoints.db*' \
  --path data_prep/raw/dc_dump.sql.gz \
  --path validation/triage_discrimination_studyb_checkpoint.jsonl \
  --path-glob 'validation/triage_discrimination_checkpoint*.jsonl' \
  --path validation/engineering_acceptance_only_phenobarbital.json \
  --path validation/canonical_validation_2026-07-11.json \
  --path publication/submission \
  --path migration_export \
  --path replit.md --path .replit --path .replitignore \
  --path MIGRATION_NOTES.md \
  --path-glob 'artifacts/*/.replit-artifact/*' \
  --path .gitattributes \
  --invert-paths \
  --strip-blobs-bigger-than 20M \
  --replace-text "$RULES"
rm -f "$RULES"

echo "== export verification =="
echo "commits: $(git rev-list --count HEAD)"
echo "tags: $(git tag -l | tr '\n' ' ')"
echo "internal paths remaining in history: $(git rev-list --objects --all | grep -cE ' (\.agents|outreach|artifacts/mockup-sandbox)(/|$)' || true)"
# `--path checkpoints.db` matched the database and NOT its sidecars: SQLite
# writes checkpoints.db-wal and checkpoints.db-shm, both of which were
# committed, and the WAL blobs carry (expired, third-party) presigned-URL
# credentials. An export under the old flag would have published them. Assert
# the strip rather than trusting it -- the previous bug was invisible precisely
# because nothing checked.
echo "checkpoint db/wal/shm objects remaining: $(git rev-list --objects --all | grep -cE ' checkpoints\.db(-wal|-shm)?$' || true)"
# research/ holds disease-selection reasoning and notes on other organisations
# working in this space. It says nothing about how the system works, and a
# public repo should carry the machine and its evidence, not the strategy
# behind which diseases to chase. It postdates the last export, so nothing in
# the strip list covered it and a push would have published it.
echo "research/ paths remaining in history: $(git rev-list --objects --all | grep -cE ' research(/|$)' || true)"
# output/ is generated run artifacts, not the machine: 65 tracked files of old
# dossiers and internal dev reports. It is also where presigned-URL credentials
# keep reappearing (output/structure_validation.json carried a live
# X-Amz-Credential), so not publishing it is better than relying on text
# redaction to catch every one.
echo "output/ paths remaining in history: $(git rev-list --objects --all | grep -cE ' output(/|$)' || true)"
# publication/submission held the cover letter, journal shortlist, bioRxiv
# checklist and reviewer-response template. That is submission strategy, not
# the machine, and the repo is meant to carry the machine and its evidence.
echo "publication/submission paths remaining: $(git rev-list --objects --all | grep -cE ' publication/submission(/|$)' || true)"
# Dead Replit config: the migration finished, nothing imports these, and a
# stale replit.md duplicated the README while documenting AI_INTEGRATIONS_*
# environment variables that no longer exist anywhere in the codebase --
# following it was a guaranteed failed setup.
echo "replit config paths remaining: $(git rev-list --objects --all | grep -cE ' (replit\.md|\.replit|\.replitignore|MIGRATION_NOTES\.md)$|\.replit-artifact/' || true)"
# Orphaned validation blobs: referenced by no code and no document, 880KB
# between them. Study resume-checkpoints are intermediate state, not results;
# the studyb one was already stripped and the others were not, which was just
# an oversight.
echo "orphaned validation blobs remaining: $(git rev-list --objects --all | grep -cE ' validation/(engineering_acceptance_only_phenobarbital\.json|canonical_validation_2026-07-11\.json|triage_discrimination_checkpoint.*\.jsonl)$' || true)"
# Must be ZERO. Every LFS-tracked path is stripped above, and .gitattributes
# with it, so a surviving pointer means the public repo references an LFS
# object that will never be pushed with it -- a checkout then fails or yields
# a 130-byte text stub instead of the file. migration_export/runtime_state.zip
# was exactly that: history-only, invisible at HEAD, and the one pointer left
# standing after the previous export.
echo "LFS pointers remaining in all refs (must be 0): $(git lfs ls-files --all 2>/dev/null | wc -l)"
echo "blobs >20MB remaining: $(git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectsize)' 2>/dev/null | awk '$1=="blob" && $2>20000000' | wc -l)"
# This script names the search terms itself, so it matches its own source and
# the check could never read zero. Exclude it, and exclude the manuscript's
# bibliography: citing a related platform by name is what a paper's reference
# list is for, and is not the internal strategy this check exists to catch.
for term in REMEDi4ALL "Rare Beacon" REPO4EU "Every Cure"; do
  n=$(git log --all --oneline -S"$term" -- .         ':(exclude)scripts/export_public_repo.sh'         ':(exclude)publication/manuscript.md' 2>/dev/null | wc -l)
  echo "pickaxe '$term' outside script+bibliography: $n"
done
