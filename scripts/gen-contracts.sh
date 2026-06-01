#!/usr/bin/env bash
# OpenAPI-first codegen (doc 18 §2): lint + bundle the contract, then generate the
# server Pydantic models and the TS client. `--check` regenerates and fails if the
# bundled contract hash drifts from packages/contracts/.contract.lock.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || cd "$(dirname "$0")/.." && pwd)"
SPEC="$ROOT/packages/contracts"
DIST="$SPEC/dist"
LOCK="$SPEC/.contract.lock"
CHECK="${1:-}"

mkdir -p "$DIST"

# 1. lint + bundle the (possibly split) spec into one file
npx --yes @redocly/cli lint "$SPEC/openapi.yaml"
npx --yes @redocly/cli bundle "$SPEC/openapi.yaml" -o "$DIST/openapi.json"

# 2. checksum gate
NEW_HASH="$(sha256sum "$DIST/openapi.json" | awk '{print $1}')"
if [ "$CHECK" = "--check" ]; then
  if [ ! -f "$LOCK" ] || [ "$(cat "$LOCK")" != "$NEW_HASH" ]; then
    echo "contract drift: regenerate with 'just contracts' and commit packages/contracts/.contract.lock" >&2
    echo "  expected: $(cat "$LOCK" 2>/dev/null || echo '<none>')" >&2
    echo "  actual:   $NEW_HASH" >&2
    exit 1
  fi
  echo "gen-contracts: contract in sync ($NEW_HASH)"
  exit 0
fi
echo "$NEW_HASH" > "$LOCK"

# 3. server: Pydantic v2 models
( cd "$ROOT/apps/api" && uv run datamodel-codegen \
    --input "$DIST/openapi.json" --input-file-type openapi \
    --output src/easysynq_api/_generated/models.py \
    --output-model-type pydantic_v2.BaseModel \
    --use-standard-collections --use-annotated --target-python-version 3.12 )

# 4. client: TS types
( cd "$ROOT/apps/web" && npx --yes openapi-typescript "$DIST/openapi.json" \
    -o src/api/_generated/schema.d.ts )

echo "gen-contracts: bundled + generated (hash $NEW_HASH)"
