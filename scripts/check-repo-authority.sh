#!/usr/bin/env bash
# Dependency-free authority contract. It deliberately checks the whole live tree as well as the
# reviewed manifest: a newly added consumer must not be able to bypass an older manifest.
set -uo pipefail
LC_ALL=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${AUTHORITY_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
MANIFEST="${ROOT}/scripts/repo-authority-live-paths.txt"
FAIL=0
declare -A EMITTED=()

reason() {
  local code="$1"
  if [ -z "${EMITTED[$code]+x}" ]; then
    printf '%s\n' "$code"
    EMITTED["$code"]=1
    FAIL=1
  fi
}

declare -A RESIDUAL_FIELD_COUNTS=()
validate_residual_record_fields() {
  local field
  for field in Status Owner Source Reason 'Closure contract' 'Last reviewed'; do
    if [ "${RESIDUAL_FIELD_COUNTS[$field]:-0}" -ne 1 ]; then
      reason AUTHORITY_RESIDUAL_LEDGER_SCHEMA
    fi
  done
}

if [ ! -d "$ROOT" ]; then
  printf 'AUTHORITY_INTERNAL_BAD_ROOT\n' >&2
  exit 2
fi

for required in AGENTS.md CLAUDE.md docs/current-status.md docs/open-residuals.md docs/slice-history.md; do
  if [ ! -f "$ROOT/$required" ]; then
    case "$required" in
      AGENTS.md) reason AUTHORITY_MISSING_AGENTS ;;
      CLAUDE.md) reason AUTHORITY_MISSING_CLAUDE ;;
      docs/current-status.md) reason AUTHORITY_MISSING_CURRENT_STATUS ;;
      docs/open-residuals.md) reason AUTHORITY_MISSING_OPEN_RESIDUALS ;;
      docs/slice-history.md) reason AUTHORITY_MISSING_SLICE_HISTORY ;;
    esac
  fi
done

if [ ! -f "$MANIFEST" ]; then
  reason AUTHORITY_MISSING_LIVE_PATH_MANIFEST
else
  declare -A MANIFEST_PATHS=()
  while IFS= read -r path || [ -n "$path" ]; do
    [ -z "$path" ] && continue
    case "$path" in
      \#*) continue ;;
    esac
    if [ -n "${MANIFEST_PATHS[$path]+x}" ]; then
      reason AUTHORITY_DUPLICATE_LIVE_PATH
    fi
    MANIFEST_PATHS["$path"]=1
    if [ ! -e "$ROOT/$path" ]; then
      # Task 1 deliberately precedes creation of these neutral authority homes.
      case "$path" in
        AGENTS.md|docs/current-status.md|docs/open-residuals.md) ;;
        *) reason AUTHORITY_UNKNOWN_LIVE_PATH ;;
      esac
    fi
  done <"$MANIFEST"
fi

if [ -f "$ROOT/docs/current-status.md" ]; then
  declare -A STATUS_VALUES=(
    [easysynq_status_schema]='1'
    [as_of]='"2026-08-08"'
    [baseline_commit]='"c15541f"'
    [last_shipped_slice]='"S-upload-identity"'
    [migration_head]='"0085"'
    [next_migration]='"0086"'
    [api_unit_tests]='1686'
    [web_test_files]='249'
    [web_tests]='1468'
    [contract_tests]='283'
    [integration_passed]='1051'
    [integration_skipped]='2'
    [ci_jobs]='10'
    [ci_checks]='14'
  )
  declare -A STATUS_COUNTS=()
  in_frontmatter=0
  closed_frontmatter=0
  while IFS= read -r line || [ -n "$line" ]; do
    if [ "$in_frontmatter" -eq 0 ]; then
      if [ "$line" = '---' ]; then
        in_frontmatter=1
      elif [ -n "$line" ]; then
        reason AUTHORITY_STATUS_FRONTMATTER
        break
      fi
      continue
    fi
    if [ "$line" = '---' ]; then
      closed_frontmatter=1
      break
    fi
    if [[ ! "$line" =~ ^([a-z_]+):[[:space:]](.+)$ ]]; then
      reason AUTHORITY_STATUS_FRONTMATTER
      continue
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [ -z "${STATUS_VALUES[$key]+x}" ]; then
      reason AUTHORITY_UNKNOWN_STATUS_KEY
      continue
    fi
    STATUS_COUNTS["$key"]=$(( ${STATUS_COUNTS[$key]:-0} + 1 ))
    if [ "${STATUS_COUNTS[$key]}" -gt 1 ]; then
      reason AUTHORITY_DUPLICATE_STATUS_KEY
    fi
    if [ "$value" != "${STATUS_VALUES[$key]}" ]; then
      reason AUTHORITY_STATUS_VALUE
    fi
    case "$key" in
      easysynq_status_schema|api_unit_tests|web_test_files|web_tests|contract_tests|integration_passed|integration_skipped|ci_jobs|ci_checks)
        [[ "$value" =~ ^[0-9]+$ ]] || reason AUTHORITY_STATUS_VALUE
        ;;
    esac
  done <"$ROOT/docs/current-status.md"
  if [ "$in_frontmatter" -eq 0 ] || [ "$closed_frontmatter" -eq 0 ]; then
    reason AUTHORITY_STATUS_FRONTMATTER
  fi
  for key in "${!STATUS_VALUES[@]}"; do
    [ "${STATUS_COUNTS[$key]:-0}" -ne 0 ] || reason AUTHORITY_MISSING_STATUS_KEY
  done
fi

if [ -f "$ROOT/AGENTS.md" ]; then
  for authority_home in docs/current-status.md docs/open-residuals.md docs/slice-history.md docs/decisions-register.md; do
    grep -Fq "$authority_home" "$ROOT/AGENTS.md" || reason AUTHORITY_AGENT_DECLARATIONS
  done
fi

if [ -f "$ROOT/docs/current-status.md" ] && grep -Eq '^##[[:space:]]+RES-' "$ROOT/docs/current-status.md"; then
  reason AUTHORITY_STATUS_RESIDUAL_RECORD
fi

if [ -f "$ROOT/docs/open-residuals.md" ]; then
  residual_record_open=0
  residual_record_count=0
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^##[[:space:]]+(RES-[A-Z][A-Z0-9-]*)$ ]]; then
      if [ "$residual_record_open" -eq 1 ]; then
        validate_residual_record_fields
      fi
      RESIDUAL_FIELD_COUNTS=()
      residual_record_open=1
      residual_record_count=$((residual_record_count + 1))
      continue
    fi

    if [[ "$line" =~ ^(Status|Owner|Source|Reason|Closure[[:space:]]contract|Last[[:space:]]reviewed):[[:space:]] ]]; then
      if [ "$residual_record_open" -eq 0 ]; then
        reason AUTHORITY_RESIDUAL_LEDGER_SCHEMA
        continue
      fi
      field="${BASH_REMATCH[1]}"
      RESIDUAL_FIELD_COUNTS["$field"]=$(( ${RESIDUAL_FIELD_COUNTS[$field]:-0} + 1 ))
      if [ "$field" = 'Status' ] && [ "$line" != 'Status: OPEN' ]; then
        reason AUTHORITY_RESIDUAL_LEDGER_SCHEMA
      fi
    fi

    if [[ "$line" =~ ^(easysynq_status_schema|as_of|baseline_commit|last_shipped_slice|migration_head|next_migration|api_unit_tests|web_test_files|web_tests|contract_tests|integration_passed|integration_skipped|ci_jobs|ci_checks): ]]; then
      reason AUTHORITY_RESIDUAL_STATUS_FACT
    fi
  done <"$ROOT/docs/open-residuals.md"

  if [ "$residual_record_open" -eq 1 ]; then
    validate_residual_record_fields
  fi
  if [ "$residual_record_count" -eq 0 ]; then
    reason AUTHORITY_RESIDUAL_LEDGER_SCHEMA
  fi
fi

if [ -f "$ROOT/CLAUDE.md" ] && grep -Eqi '^#{1,6}[[:space:]].*(current[[:space:]]+status|recent[[:space:]]+learnings)' "$ROOT/CLAUDE.md"; then
  reason AUTHORITY_CLAUDE_CURRENT_OWNER
fi

if [ -f "$ROOT/CLAUDE.md" ] && grep -Eqi '(migration[[:space:]]+(head|snapshot)|next[[:space:]]+migration|baseline[[:space:]]+commit|last[[:space:]]+shipped[[:space:]]+slice|current[[:space:]]+slice|api[[:space:]]+unit[[:space:]]+tests|web[[:space:]]+(test[[:space:]]+files|tests)|contract[[:space:]]+tests|integration[[:space:]]+(passed|skipped)|ci[[:space:]]+(jobs|checks)|RES-[A-Z][A-Z0-9-]*|decision[[:space:]]+range|R[1-9][0-9]*[–-]R[1-9][0-9]*|permission[[:space:]]+(catalog|count|keys))' "$ROOT/CLAUDE.md"; then
  reason AUTHORITY_CLAUDE_MUTABLE_FACTS
fi

# Task 3's terminal compatibility file has an intentionally tiny allowlisted structure. Run this
# only after legacy current/mutable ownership is gone so Task 2 diagnostics stay attributable to the
# deliberately uncut boundary rather than acquiring a redundant structure reason.
if [ -f "$ROOT/CLAUDE.md" ] && \
    [ -z "${EMITTED[AUTHORITY_CLAUDE_CURRENT_OWNER]+x}" ] && \
    [ -z "${EMITTED[AUTHORITY_CLAUDE_MUTABLE_FACTS]+x}" ]; then
  mapfile -t claude_headings < <(grep -E '^#{1,6}[[:space:]]' "$ROOT/CLAUDE.md" || true)
  claude_structure_ok=1
  if [ "${#claude_headings[@]}" -ne 3 ] || \
      [ "${claude_headings[0]:-}" != '# EasySynQ Claude compatibility' ] || \
      [ "${claude_headings[1]:-}" != '## Claude hooks and commands' ] || \
      [ "${claude_headings[2]:-}" != '## Claude memory behavior' ]; then
    claude_structure_ok=0
  fi

  claude_pointer='Read AGENTS.md before work. Current execution state is in docs/current-status.md; current residuals are in docs/open-residuals.md.'
  if [ "$(grep -Fxc "$claude_pointer" "$ROOT/CLAUDE.md" || true)" -ne 1 ]; then
    claude_structure_ok=0
  fi

  claude_section=''
  claude_hook_location=0
  claude_command_location=0
  claude_session_start=0
  claude_memory_convention=0
  declare -A CLAUDE_COMPATIBILITY_LINES=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      '# EasySynQ Claude compatibility'|'') continue ;;
      'Read AGENTS.md before work. Current execution state is in docs/current-status.md; current residuals are in docs/open-residuals.md.') continue ;;
      '## Claude hooks and commands') claude_section='hooks'; continue ;;
      '## Claude memory behavior') claude_section='memory'; continue ;;
    esac

    if [ -z "$claude_section" ] || [[ ! "$line" =~ ^-[[:space:]] ]]; then
      claude_structure_ok=0
      continue
    fi

    line_key="$claude_section:$line"
    if [ -n "${CLAUDE_COMPATIBILITY_LINES[$line_key]+x}" ]; then
      claude_structure_ok=0
      continue
    fi
    CLAUDE_COMPATIBILITY_LINES["$line_key"]=1

    case "$claude_section" in
      hooks)
        case "$line" in
          '- Active Claude hooks live under `.claude/hooks/`.')
            claude_hook_location=1
            ;;
          '- Active Claude commands live under `.claude/commands/`.')
            claude_command_location=1
            ;;
          '- Claude hook wiring lives in `.claude/settings.json`.')
            ;;
          '- Claude session-start behavior is wired in `.claude/settings.json` and implemented by `.claude/hooks/test-baseline.sh`.')
            claude_session_start=1
            ;;
          *) claude_structure_ok=0 ;;
        esac
        ;;
      memory)
        case "$line" in
          '- Claude session memory remains tool-specific.') claude_memory_convention=1 ;;
          '- Claude `/effort` selection is per-session.') claude_memory_convention=1 ;;
          '- Claude persistent memory lives outside the repository under `~/.claude/projects/<path-derived-key>/memory/`.') claude_memory_convention=1 ;;
          '- `MEMORY.md` is the index for Claude persistent memory.') claude_memory_convention=1 ;;
          '- Claude memory paths are machine- and OS-specific.') claude_memory_convention=1 ;;
          *) claude_structure_ok=0 ;;
        esac
        ;;
    esac
  done <"$ROOT/CLAUDE.md"

  if [ "$claude_hook_location" -ne 1 ] || \
      [ "$claude_command_location" -ne 1 ] || \
      [ "$claude_session_start" -ne 1 ] || \
      [ "$claude_memory_convention" -ne 1 ]; then
    claude_structure_ok=0
  fi

  if [ "$claude_structure_ok" -ne 1 ]; then
    reason AUTHORITY_CLAUDE_PRODUCT_RULES
  fi
fi

if [ -f "$ROOT/docs/slice-history.md" ]; then
  history_preamble="$(sed -n '1,220p' "$ROOT/docs/slice-history.md")"
  if grep -Eqi '(^#{1,6}[[:space:]].*open[[:space:]]+residuals|migration[[:space:]]+head|current[[:space:]]+(migration[[:space:]]+)?head)' <<<"$history_preamble"; then
    reason AUTHORITY_HISTORY_MUTABLE_HEAD
  fi
  if ! grep -Fq 'docs/open-residuals.md' "$ROOT/docs/slice-history.md"; then
    reason AUTHORITY_HISTORY_AUTHORITY
  fi
fi

is_historical_path() {
  case "$1" in
    docs/superpowers/*|docs/audit-2026-06-17.md|docs/review-2026-07-22.md|docs/slice-history.md)
      return 0
      ;;
    *) return 1 ;;
  esac
}

# Keep these scopes explicit for reviewers; the full-tree scan below is the enforcement backstop.
CURRENT_PATHS=(README.md AGENTS.md CLAUDE.md apps .claude docs/00-overview.md
  docs/16-roadmap.md docs/17-gaps-and-open-questions.md
  docs/18-mvp-implementation-plan.md docs/dev-workflow.md docs/manuals docs/runbooks)
HISTORICAL_EXCLUDES=(docs/superpowers docs/audit-2026-06-17.md
  docs/review-2026-07-22.md docs/slice-history.md)

# Fixture scripts deliberately contain malformed authority examples. They exercise the guard but
# are not repository authority consumers and must never make the live gate fail.
is_fixture_test_path() {
  case "$1" in
    scripts/tests/test-agent-authority.sh|scripts/tests/test-claude-hooks.sh) return 0 ;;
    *) return 1 ;;
  esac
}

declare -a LIVE_TEXT_FILES=()
while IFS= read -r -d '' absolute_path; do
  relative_path="$absolute_path"
  is_historical_path "$relative_path" && continue
  [ "$relative_path" = 'CLAUDE.md' ] && continue
  is_fixture_test_path "$relative_path" && continue
  LIVE_TEXT_FILES+=("$ROOT/$relative_path")
done < <(git -C "$ROOT" ls-files --cached -z)

if [ "${#LIVE_TEXT_FILES[@]}" -gt 0 ]; then
  if grep -I -Eqi 'CLAUDE\.md.{0,160}(authoritative|owns|current[[:space:]]+status|migration[[:space:]]+head|residual|rules)|(authoritative|owns|current[[:space:]]+status|migration[[:space:]]+head|residual|rules).{0,160}CLAUDE\.md' "${LIVE_TEXT_FILES[@]}"; then
    reason AUTHORITY_LIVE_CLAUDE_OWNER
  fi

  # The register owns its canonical self-range. Exclude only that file from the mirror scan; it
  # remains in LIVE_TEXT_FILES for every other authority and residual-reference check.
  declare -a DECISION_RANGE_MIRROR_FILES=()
  for absolute_path in "${LIVE_TEXT_FILES[@]}"; do
    [ "$absolute_path" = "$ROOT/docs/decisions-register.md" ] && continue
    DECISION_RANGE_MIRROR_FILES+=("$absolute_path")
  done
  if [ "${#DECISION_RANGE_MIRROR_FILES[@]}" -gt 0 ] && \
      grep -I -Eq 'R[1-9][0-9]*[–-]R[1-9][0-9]*' "${DECISION_RANGE_MIRROR_FILES[@]}"; then
    reason AUTHORITY_DECISION_RANGE_MIRROR
  fi
fi

if [ -f "$ROOT/docs/open-residuals.md" ]; then
  declare -A RESIDUAL_IDS=()
  while IFS= read -r residual_id; do
    if [ -n "${RESIDUAL_IDS[$residual_id]+x}" ]; then
      reason AUTHORITY_DUPLICATE_RESIDUAL_ID
    fi
    RESIDUAL_IDS["$residual_id"]=1
  done < <(grep -Eo '^##[[:space:]]+(RES-[A-Z][A-Z0-9-]*)' "$ROOT/docs/open-residuals.md" | sed -E 's/^##[[:space:]]+//')

  while IFS= read -r referenced_id; do
    [ -z "$referenced_id" ] && continue
    [ -n "${RESIDUAL_IDS[$referenced_id]+x}" ] || reason AUTHORITY_UNKNOWN_RESIDUAL_ID
  done < <(grep -I -Eho 'RES-[A-Z][A-Z0-9-]*' "${LIVE_TEXT_FILES[@]}" || true)
fi

if [ "$FAIL" -eq 0 ]; then
  printf 'AUTHORITY_OK\n'
  exit 0
fi
exit 1
