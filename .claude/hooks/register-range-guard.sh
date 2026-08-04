#!/usr/bin/env bash
# PostToolUse hook: docs/decisions-register.md gained an R-number that the range declarations
# have not caught up with.
#
# The register is AUTHORITATIVE — it supersedes conflicting section text — and its range
# ("R1–RNN") is declared in THREE places that must agree: the register's own intro paragraph,
# its "## Part 3 — Resolutions R1–RNN" heading, and two lines in CLAUDE.md. Adding an entry
# means bumping all of them, and a missed bump is completely silent: nothing lints it, no CI job
# reads it, and the next session orienting from CLAUDE.md is told the register stops one entry
# short of where it actually stops.
#
# Verified needed 2026-08-03: R64 required five separate hand-edits across two files.
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
# The register declares the range in its intro and its Part 3 heading; CLAUDE.md declares it twice.
grep -q "R1–R${highest})" docs/decisions-register.md 2>/dev/null || stale="${stale} the register's intro paragraph;"
grep -q "^## Part 3 — Resolutions R1–R${highest}\$" docs/decisions-register.md 2>/dev/null || stale="${stale} the register's 'Part 3 — Resolutions' heading;"
if [ -f CLAUDE.md ]; then
  # `grep -c` always PRINTS a count but exits 1 when that count is zero — so a `|| echo 0`
  # fallback appends a second line and the later `[ … -lt … ]` dies on "0\n0".
  claude_hits="$(grep -c "R1–R${highest}" CLAUDE.md 2>/dev/null)"
  claude_hits="${claude_hits:-0}"
  [ "$claude_hits" -lt 2 ] && stale="${stale} CLAUDE.md (expects 2 mentions — the orientation blockquote and the Deep-Dive line, found ${claude_hits});"
fi

[ -z "$stale" ] && exit 0

printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"Decisions-register range check: the highest entry is now R${highest}, but the range still reads stale in:${stale} Bump every one to R1–R${highest}. This is silent — no lint or CI job reads the range, and a session orienting from CLAUDE.md would be told the register stops short. Also confirm the new entry's 'Back-propagation:' line was actually honoured in each document it names (the docs-drift-reviewer agent checks this).\"}}"
exit 0
