#!/usr/bin/env bash
# PostToolUse hook: packages/contracts/openapi.yaml was edited but .contract.lock did not move.
#
# Sibling of contract-drift.sh, covering the OTHER half of the contract seam. That hook nudges
# when a route module changes without openapi.yaml; this one nudges when openapi.yaml changes
# without the bundled-hash lock.
#
# ⚠ Why this needs a hook at all: NO CI JOB RUNS scripts/gen-contracts.sh. The `contracts` job
# runs redocly lint (plus the R61 backstop) and nothing else, so a stale .contract.lock never
# goes red — the generated Pydantic server models and TypeScript types silently stop matching
# the spec. Verified 2026-08-03 by grepping .github/workflows/: gen-contracts appears nowhere.
# A stale lock shipped that same day and was caught only by hand.
#
# Non-blocking: emits PostToolUse `additionalContext`, exit 0 (matches contract-drift.sh).
# Regenerating is cheap and idempotent, so the nudge is safe to act on unconditionally.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

case "$file" in
  */packages/contracts/openapi.yaml|packages/contracts/openapi.yaml) ;;
  *) exit 0 ;;
esac

cd "$ROOT" || exit 0

# The lock already has a pending change → the regeneration happened, stay quiet.
if git status --porcelain packages/contracts/.contract.lock 2>/dev/null | grep -q .; then
  exit 0
fi

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Contract-lock reminder: you edited packages/contracts/openapi.yaml but packages/contracts/.contract.lock has no pending change. NO CI job runs scripts/gen-contracts.sh — the contracts job is redocly lint only — so a stale lock will NOT go red and the generated Pydantic/TypeScript types will drift from the spec. Run `bash scripts/gen-contracts.sh` and commit the lock. Note redocly also cannot detect an OMITTED or factually WRONG status code or summary, so re-read the operation you changed against what the handler actually returns and raises."}}'
exit 0
