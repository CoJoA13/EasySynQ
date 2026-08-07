#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$#" -eq 0 ]; then
  echo "usage: run-contract-tool.sh {redocly|openapi-typescript} [arguments]" >&2
  exit 64
fi

tool="$1"
shift
case "$tool" in
  redocly|openapi-typescript) ;;
  *) echo "unsupported contract tool: $tool" >&2; exit 64 ;;
esac

binary="$ROOT/packages/contracts/node_modules/.bin/$tool"
if [ ! -x "$binary" ]; then
  echo "contract tool is not installed: $tool" >&2
  echo "Run: npm ci --prefix packages/contracts --ignore-scripts" >&2
  exit 127
fi

if [ "$tool" = "redocly" ]; then
  export REDOCLY_TELEMETRY=off
  export REDOCLY_SUPPRESS_UPDATE_NOTICE=true
fi
cd "$ROOT"
exec "$binary" "$@"
