#!/usr/bin/env bash
# PostToolUse hook: R61 — catch site-specific operational data at WRITE time, not at gate time.
#
# R61 is the repo's most emphatic Critical rule, and its own wording is the reason this hook
# exists: "Sanitize at write time, not after — removal cannot undo publication (git history,
# forks, review-tool comments quoting the diff)." The mechanical checker
# (scripts/check-no-site-data.sh) already existed, but nothing ran it on an edit — so a match
# was only discovered at commit/CI time, by which point the content had already been written
# and, at least once, already committed. It fired TWICE in a single session (2026-08-03):
# docs/slice-history.md and a tool-specific compatibility note, both times after the fact.
#
# Scope: prose files only (*.md). Code paths legitimately carry IPs/hostnames in fixtures and
# config, and the repo-wide checker (which CI runs in the `contracts` job) already covers them
# at the gate. This hook is the fast write-time net for the surface R61 actually keeps failing on.
#
# ⚠ The checker is mechanical and has a known false positive: a 4-segment clause literal (a
# 9.2.2.x-shaped leaf) reads as IPv4. That is a real trap, not noise — the fix is to write such
# examples with a non-numeric final segment, which is why the nudge names it explicitly.
#
# Non-blocking: emits PostToolUse `additionalContext` (a nudge for Claude), exit 0 — matching
# contract-drift.sh. It must never block an edit: a false positive on a clause literal would
# otherwise wedge legitimate documentation work.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

case "$file" in
  *.md) ;;
  *) exit 0 ;;
esac

cd "$ROOT" || exit 0
[ -x scripts/check-no-site-data.sh ] || exit 0

# Run the repo checker and keep only lines naming the file just edited. The checker reports
# repo-relative paths, so match on the basename-anchored suffix to stay correct whether the
# hook received an absolute or a relative path.
rel="${file#"$ROOT"/}"
out="$(bash scripts/check-no-site-data.sh 2>&1 | grep -F "$rel:" || true)"
[ -z "$out" ] && exit 0

# Collapse to a single line for the JSON payload (no jq dependency — see _lib.sh).
detail="$(printf '%s' "$out" | tr '\n' ' ' | sed 's/"/\\"/g; s/[[:space:]]\{2,\}/ /g')"

printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"R61 site-data backstop matched the file you just edited: ${detail} — fix it NOW, in this edit, not later: R61's whole point is that sanitizing after the fact cannot undo publication (git history, forks, review comments quoting the diff). If this is a 4-segment CLAUSE literal rather than an IP, rewrite it with a non-numeric final segment (a 9.2.2.x-shaped leaf) — the checker cannot tell them apart. Verify with: bash scripts/check-no-site-data.sh\"}}"
exit 0
