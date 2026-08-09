#!/usr/bin/env bash
# SessionStart hook: surface the dated test baseline from the neutral execution snapshot.
#
# It deliberately does not run suites. The snapshot is a measured prior baseline, while a new slice's
# after-count still needs fresh execution evidence. Silent failure is intentional: an ambiguous
# frontmatter value must never become a guessed baseline.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
cd "$ROOT" || exit 0

status_file='docs/current-status.md'
[ -f "$status_file" ] || exit 0

in_frontmatter=0
closed_frontmatter=0
invalid=0
api=''
web=''
declare -A status_keys=(
  [easysynq_status_schema]=1 [as_of]=1 [baseline_commit]=1 [last_shipped_slice]=1
  [migration_head]=1 [next_migration]=1 [api_unit_tests]=1 [web_test_files]=1 [web_tests]=1
  [contract_tests]=1 [integration_passed]=1 [integration_skipped]=1 [ci_jobs]=1 [ci_checks]=1
)
declare -A numeric_keys=(
  [easysynq_status_schema]=1 [api_unit_tests]=1 [web_test_files]=1 [web_tests]=1
  [contract_tests]=1 [integration_passed]=1 [integration_skipped]=1 [ci_jobs]=1 [ci_checks]=1
)
declare -A status_counts=()

while IFS= read -r line || [ -n "$line" ]; do
  if [ "$in_frontmatter" -eq 0 ]; then
    if [ "$line" = '---' ]; then
      in_frontmatter=1
    elif [ -n "$line" ]; then
      invalid=1
      break
    fi
    continue
  fi

  if [ "$line" = '---' ]; then
    closed_frontmatter=1
    break
  fi

  if [[ ! "$line" =~ ^([a-z_]+):[[:space:]](.+)$ ]]; then
    invalid=1
    continue
  fi

  key="${BASH_REMATCH[1]}"
  value="${BASH_REMATCH[2]}"
  if [ -z "${status_keys[$key]+x}" ]; then
    invalid=1
    continue
  fi
  status_counts["$key"]=$(( ${status_counts[$key]:-0} + 1 ))
  [ "${status_counts[$key]}" -eq 1 ] || invalid=1
  if [ -n "${numeric_keys[$key]+x}" ] && [[ ! "$value" =~ ^[0-9]+$ ]]; then
    invalid=1
  fi
  case "$key" in
    api_unit_tests) api="$value" ;;
    web_tests) web="$value" ;;
  esac
done <"$status_file"

[ "$in_frontmatter" -eq 1 ] || exit 0
[ "$closed_frontmatter" -eq 1 ] || exit 0
[ "$invalid" -eq 0 ] || exit 0
for key in "${!status_keys[@]}"; do
  [ "${status_counts[$key]:-0}" -eq 1 ] || exit 0
done
[ "$invalid" -eq 0 ] || exit 0

msg="Test-count baseline from docs/current-status.md: api=${api}; web=${web}. Use these as the before-counts for /finish-slice, then confirm every after-count with fresh suite evidence."
printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"${msg}\"}}"
