#!/usr/bin/env bash
# SessionStart hook: surface the LAST slice's recorded test counts as this session's baseline.
#
# /finish-slice requires before→after deltas ("api unit 1254→1282; web 1433→1458"). Reconstructing
# the "before" at slice end means re-running suites you have already changed — by then the baseline
# is gone. But it is not actually unknown: the previous slice's *after* IS this slice's *before*,
# and every Recent-learnings bullet in CLAUDE.md records it.
#
# ⚠ Deliberately does NOT run the suites. A full web run is ~5 minutes; paying that on every session
# start to produce a number that is already written down would be a bad trade. This is a grep.
#
# Emits SessionStart `additionalContext`, exit 0. Silent if it cannot find a count rather than
# guessing — a wrong baseline is worse than none, because it ships into the slice record.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
cd "$ROOT" || exit 0
[ -f CLAUDE.md ] || exit 0

# The newest Recent-learnings bullet is the most recent slice. Its trailing parenthetical carries
# the counts, e.g. "(api unit 1254→1282; +20 integration; web 1433→1458; PR #429 squash `ec2e93e`.)"
newest="$(grep -m1 '^- 20[0-9][0-9]-' CLAUDE.md || true)"
[ -z "$newest" ] && exit 0

api="$(printf '%s' "$newest" | sed -n 's/.*api unit [0-9]*→\([0-9]*\).*/\1/p')"
web="$(printf '%s' "$newest" | sed -n 's/.*web [0-9]*→\([0-9]*\).*/\1/p')"
[ -z "$api" ] && api="$(printf '%s' "$newest" | sed -n 's/.*api unit \([0-9]*\)[;,)].*/\1/p')"
[ -z "$web" ] && web="$(printf '%s' "$newest" | sed -n 's/.*web \([0-9]*\)[;,)].*/\1/p')"

# Nothing parseable → stay quiet.
[ -z "$api" ] && [ -z "$web" ] && exit 0

msg="Test-count baseline for this session, read from the newest CLAUDE.md Recent-learnings bullet (the previous slice's *after* is this slice's *before*):"
[ -n "$api" ] && msg="$msg api unit = ${api}."
[ -n "$web" ] && msg="$msg web = ${web}."
msg="$msg Use these as the 'before' in /finish-slice rather than re-deriving them, but CONFIRM the 'after' by actually running the suites — do not report a delta you did not measure. If the working tree already differs from that slice, treat these as stale and re-measure both ends."

printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"${msg}\"}}"
exit 0
