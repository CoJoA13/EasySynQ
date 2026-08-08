#!/usr/bin/env bash
# Task 1 establishes the diagnostic seam. Task 3 adds the fixture matrix for the migrated hooks.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/scripts/check-repo-authority.sh"

printf '== Claude hook compatibility contract ==\n'
output="$("$GUARD" 2>&1)"
status=$?
if [ "$status" -ne 1 ]; then
  printf '  FAIL expected the absent neutral baseline to remain diagnostic (status=%s output=%s)\n' "$status" "$output"
  exit 1
fi
if ! grep -Fq 'AUTHORITY_MISSING_CURRENT_STATUS' <<<"$output"; then
  printf '  FAIL expected AUTHORITY_MISSING_CURRENT_STATUS (output=%s)\n' "$output"
  exit 1
fi
printf '  ok   absent neutral baseline remains visible to the Claude hook migration\n'
