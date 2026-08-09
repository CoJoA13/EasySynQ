#!/usr/bin/env bash
# PostToolUse hook: docs/decisions-register.md gained an R-number that its two self-declarations
# have not caught up with. The hook intentionally reads no compatibility mirror.
#
# Non-blocking: emits PostToolUse `additionalContext`, exit 0.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

case "$file" in
  */docs/decisions-register.md|docs/decisions-register.md) ;;
  *) exit 0 ;;
esac

cd "$ROOT" || exit 0

# Highest "### RNN — " entry actually present in the register.
highest="$(grep -o '^### R[0-9]\+ ' docs/decisions-register.md 2>/dev/null \
  | sed 's/^### R//; s/ $//' | sort -n | tail -1)"
[ -z "$highest" ] && exit 0

stale=""
# Compare the highest entry with the register's two self-declarations only.
intro="$(awk 'NF && $0 !~ /^#/ { print; exit }' docs/decisions-register.md 2>/dev/null)"
[[ "$intro" == *"R1–R${highest})"* ]] || stale="${stale} the register's intro paragraph;"
grep -q "^## Part 3 — Resolutions R1–R${highest}\$" docs/decisions-register.md 2>/dev/null || stale="${stale} the register's 'Part 3 — Resolutions' heading;"

[ -z "$stale" ] && exit 0

printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"Decisions-register range check: the highest entry is now R${highest}, but the range still reads stale in:${stale} Bump both self-declarations to R1–R${highest}. Also confirm the new entry's Back-propagation line was honoured in every document it names.\"}}"
