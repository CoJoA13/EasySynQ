#!/usr/bin/env bash
# PostToolUse hook: packages/contracts/openapi.yaml was edited but .contract.lock did not move.
#
# Sibling of contract-drift.sh, covering the OTHER half of the contract seam. That hook nudges
# when a route module changes without openapi.yaml; this one nudges when openapi.yaml changes
# without the bundled-hash lock.
#
# This hook gives immediate local feedback before the `contracts` CI job runs
# `scripts/gen-contracts.sh --check`. Regenerating here also leaves the generated Pydantic server
# models and TypeScript types ready for local consumers instead of waiting for CI to report drift.
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

printf '%s' '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"Contract-lock reminder: you edited packages/contracts/openapi.yaml but packages/contracts/.contract.lock has no pending change. Run `bash scripts/gen-contracts.sh` and commit the lock; CI runs `scripts/gen-contracts.sh --check`, but regenerating now keeps the local Pydantic/TypeScript outputs current. Note redocly also cannot detect an OMITTED or factually WRONG status code or summary, so re-read the operation you changed against what the handler actually returns and raises."}}'
exit 0
