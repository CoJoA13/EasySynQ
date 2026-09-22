#!/usr/bin/env bash
# Refresh integration timings from one successful GitHub Actions `ci` run on main.
# Usage: scripts/refresh-test-durations.sh [run-id]
# Requires Python 3 and authenticated gh; never prints a credential.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd -P)"
cd "$repo_root"

exec python3 - "$repo_root" "$@" <<'PYEOF'
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# gh expands {owner}/{repo} from the git remote; the repository is never hard-coded here.
REPO = "repos/{owner}/{repo}"
WORKFLOW = ".github/workflows/ci.yml"
SHARDS = (1, 2, 3, 4)


def gh(*arguments):
    result = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True)
    return result.stdout


def api(endpoint):
    decoder = json.JSONDecoder()
    remaining = gh("api", endpoint).strip()
    pages = []
    while remaining:
        value, end = decoder.raw_decode(remaining)
        pages.append(value)
        remaining = remaining[end:].lstrip()
    if len(pages) != 1:
        raise ValueError("GitHub returned an empty or ambiguous response")
    return pages[0]


def artifact_name(shard):
    return f"test-durations-{shard}"


def refresh():
    if len(sys.argv) > 3:
        raise ValueError("usage: scripts/refresh-test-durations.sh [run-id]")
    run_id = sys.argv[2] if len(sys.argv) == 3 else None
    if run_id is None:
        listing = api(f"{REPO}/actions/workflows/ci.yml/runs?branch=main&status=success&per_page=1")
        runs = listing.get("workflow_runs") if isinstance(listing, dict) else None
        if not isinstance(runs, list) or not runs:
            raise ValueError("no successful main ci run found")
        run_id = str(runs[0]["id"])
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("run-id must be a positive integer")
    run = api(f"{REPO}/actions/runs/{run_id}")
    if (not isinstance(run, dict) or run.get("id") != int(run_id) or run.get("head_branch") != "main"
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("path") != WORKFLOW):
        raise ValueError("choose a successful main ci run")

    listing = api(f"{REPO}/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = listing.get("artifacts") if isinstance(listing, dict) else None
    if not isinstance(artifacts, list):
        raise ValueError("GitHub did not return an artifact list")
    expected = {artifact_name(shard): shard for shard in SHARDS}
    shard_artifacts = {}
    for artifact in artifacts:
        shard = expected.get(artifact.get("name"))
        if shard is None:
            continue
        if shard in shard_artifacts or artifact.get("expired", True):
            raise ValueError("integration shard artifacts must be unique and unexpired")
        shard_artifacts[shard] = artifact
    if set(shard_artifacts) != set(SHARDS):
        raise ValueError("run must contain all four integration shard artifacts")

    merged = {}
    with tempfile.TemporaryDirectory(prefix="test-durations.") as download_root:
        for shard in SHARDS:
            destination = Path(download_root) / str(shard)
            destination.mkdir()
            # The artifact is a zip; gh extracts it into the destination directory.
            gh("run", "download", run_id, "-n", artifact_name(shard), "-D", str(destination))
            files = [path for path in destination.rglob(".test_durations") if path.is_file()]
            if len(files) != 1:
                raise ValueError(f"shard {shard} artifact must contain exactly one .test_durations file")
            durations = json.loads(files[0].read_text())
            if not isinstance(durations, dict) or not durations:
                raise ValueError(f"shard {shard} has no duration mapping")
            if any(not isinstance(key, str) or not isinstance(value, (int, float))
                   or isinstance(value, bool) or not math.isfinite(value) or value < 0
                   for key, value in durations.items()):
                raise ValueError(f"shard {shard} contains an invalid duration")
            overlap = merged.keys() & durations.keys()
            if overlap:
                raise ValueError(f"shards overlap on {sorted(overlap)[:3]} — refusing mixed timing evidence")
            merged.update(durations)

    output = Path(sys.argv[1]) / "apps/api/.test_durations"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, prefix=".test_durations.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(dict(sorted(merged.items())), indent=4) + "\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"wrote {len(merged)} durations from GitHub Actions run {run_id}; review the diff and commit")


try:
    refresh()
except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.CalledProcessError) as error:
    # gh stderr can include request diagnostics; disclose only the failure class.
    if isinstance(error, subprocess.CalledProcessError):
        message = "GitHub API request failed; check gh authentication and artifact retention"
    else:
        message = str(error)
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)
PYEOF
