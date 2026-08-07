#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
audit_parent="$(cd "${RUNNER_TEMP:-/tmp}" && pwd -P)"
audit_tmp="$(mktemp -d "$audit_parent/easysynq-pip-audit.XXXXXX")"

cleanup() {
  case "$audit_tmp" in
    "$audit_parent"/easysynq-pip-audit.*)
      if [ -d "$audit_tmp" ] && [ ! -L "$audit_tmp" ]; then
        rm -rf -- "$audit_tmp"
      fi
      ;;
  esac
}
trap cleanup EXIT

cd "$ROOT/apps/api"

if ! uv export --frozen --no-group security --no-emit-project \
  --format requirements-txt \
  -o "$audit_tmp/py-requirements.txt" >/dev/null 2>&1; then
  echo "::error::pip-audit dependency export failed" >&2
  exit 1
fi

set +e
uv run --frozen --only-group security pip-audit \
  -r "$audit_tmp/py-requirements.txt" \
  --format json \
  -o "$audit_tmp/pip-audit.json" >/dev/null 2>&1
audit_status=$?
set -e

report="$audit_tmp/pip-audit.json"
if ! jq -e '
  (type == "object")
  and ((.dependencies | type) == "array")
  and all(.dependencies[];
    ((.name | type) == "string")
    and ((.version | type) == "string")
    and ((.vulns | type) == "array")
    and all(.vulns[]; ((.id | type) == "string"))
  )
' "$report" >/dev/null 2>&1; then
  echo "::error::pip-audit produced no valid report" >&2
  exit 1
fi

vulnerability_count="$(jq '[.dependencies[].vulns[]] | length' "$report")"
if ! {
  [ "$audit_status" -eq 0 ] && [ "$vulnerability_count" -eq 0 ]
} && ! {
  [ "$audit_status" -eq 1 ] && [ "$vulnerability_count" -gt 0 ]
}; then
  echo "::error::pip-audit status/report disagreement" >&2
  exit 1
fi

jq -r '
  .dependencies[]
  | select(.vulns | length > 0)
  | "\(.name) \(.version): \([.vulns[].id] | join(", "))"
' "$report"
