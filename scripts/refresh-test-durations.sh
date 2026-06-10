#!/usr/bin/env bash
# Refresh apps/api/.test_durations from the per-shard artifacts a green ci.yml run publishes.
#
# Each integration shard runs pytest-split with --store-durations --clean-durations, so its
# test-durations-<N> artifact holds fresh timings for exactly the tests it ran; the union of the
# four shards is a complete fresh durations file. Run this whenever shard wall-clocks drift apart,
# review the diff, commit.
#
# Usage: scripts/refresh-test-durations.sh [run-id]
#   run-id  a ci.yml run whose integration shards were green; defaults to the latest
#           successful run on main. Artifacts expire after 7 days — if the default run is
#           older, gh fails at download; pass a fresher run id (or let any PR run one).
set -euo pipefail
cd "$(dirname "$0")/.."

run_id="${1:-}"
if [ -z "$run_id" ]; then
  run_id="$(gh run list --workflow=ci.yml --branch=main --status=success --limit 1 \
    --json databaseId --jq '.[0].databaseId')"
  [ -n "$run_id" ] || { echo "error: no successful ci.yml run on main found" >&2; exit 1; }
fi
echo "merging durations artifacts from run $run_id"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
for g in 1 2 3 4; do
  gh run download "$run_id" -n "test-durations-$g" -D "$tmp/$g"
done

(cd apps/api && uv run --no-sync python - "$tmp" <<'PYEOF'
import json
import pathlib
import sys

tmp = pathlib.Path(sys.argv[1])
merged: dict[str, float] = {}
for g in (1, 2, 3, 4):
    shard = json.loads((tmp / str(g) / ".test_durations").read_text())
    overlap = merged.keys() & shard.keys()
    if overlap:
        raise SystemExit(
            f"error: shards overlap on {sorted(overlap)[:3]} — artifacts are not from one run"
        )
    merged.update(shard)
out = pathlib.Path(".test_durations")
out.write_text(json.dumps(dict(sorted(merged.items())), indent=4))
print(f"wrote apps/api/.test_durations with {len(merged)} entries")
PYEOF
)
echo "done — review the diff and commit apps/api/.test_durations"
