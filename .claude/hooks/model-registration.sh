#!/usr/bin/env bash
# PostToolUse hook: when a model/enum module under db/models/ is created or edited but is NOT
# imported in db/models/__init__.py, remind to register it. That file is the SOLE place
# `Base.metadata` is populated — a model whose module isn't imported there is invisible to
# autogenerate, so `alembic check` reports a phantom-DROP of its table(s)/enum(s) and the
# `migrations` CI job goes red (the 0027 `form_template` lesson). The `migration-reviewer` agent
# catches this at PR time; this hook nudges at EDIT time (same philosophy as contract-drift.sh:
# the CI job is the gate, the hook moves the catch earlier).
#
# Non-blocking: emits PostToolUse `additionalContext` (a nudge for Claude), exit 0. Stays quiet
# for __init__.py itself, for non-models edits, and once __init__.py already imports the module.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$DIR/_lib.sh"

file="$(hook_file_path)"
[ -z "$file" ] && exit 0

# Only the ORM models package (model + enum modules both must be registered there).
case "$file" in
  */apps/api/src/easysynq_api/db/models/*.py|apps/api/src/easysynq_api/db/models/*.py) ;;
  *) exit 0 ;;
esac

base="$(basename "$file" .py)"
# __init__.py is the registry itself (editing it IS the fix) — never nudge on it.
case "$base" in
  __init__) exit 0 ;;
esac

INIT="$ROOT/apps/api/src/easysynq_api/db/models/__init__.py"
[ -f "$INIT" ] || exit 0

# Already imported? Quiet. The canonical registration line is `from .<module> import (`; the
# trailing ` import` anchors the module name so a prefix (._audit) can't false-match ._audit_enums.
if grep -qF "from .${base} import" "$INIT"; then
  exit 0
fi

printf '%s' "{\"hookSpecificOutput\":{\"hookEventName\":\"PostToolUse\",\"additionalContext\":\"Model-registration reminder: you edited db/models/${base}.py but it is not imported in db/models/__init__.py. That file is the sole place Base.metadata is populated — an unregistered model/enum module makes 'alembic check' phantom-DROP its table(s)/type(s) and the migrations CI job goes red. Add 'from .${base} import (...)' there (and the new names to __all__). If this module defines no mapped model/enum, ignore this.\"}}"
exit 0
