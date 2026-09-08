#!/usr/bin/env bash
set -euo pipefail

# Post-merge runs with stdin closed. Do not run Drizzle "push" here: it can
# propose destructive table changes and requires an interactive confirmation.
# Database schema changes must use the project's reviewed migration/publish
# flow, not an unattended hook.
export CI=true

pnpm install --frozen-lockfile
pnpm run typecheck
PORT="${PORT:-21854}" BASE_PATH="${BASE_PATH:-/}" \
  pnpm --filter @workspace/web-frontend run build
