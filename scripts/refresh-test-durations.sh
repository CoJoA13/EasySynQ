#!/usr/bin/env bash
# Refresh integration timings from one successful GitLab main pipeline.
# Usage: scripts/refresh-test-durations.sh [pipeline-id]
# Requires Python 3 and authenticated glab; never prints a credential.
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


def api(endpoint, paginate=False):
    command = ["glab", "api", endpoint]
    if paginate:
        command.append("--paginate")
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    # glab versions can emit either one combined array or successive JSON pages.
    decoder = json.JSONDecoder()
    remaining = result.stdout.strip()
    pages = []
    while remaining:
        value, end = decoder.raw_decode(remaining)
        pages.append(value)
        remaining = remaining[end:].lstrip()
    if paginate:
        if not pages or any(not isinstance(page, list) for page in pages):
            raise ValueError("GitLab did not return job arrays")
        return [item for page in pages for item in page]
    if len(pages) != 1:
        raise ValueError("GitLab returned an empty or ambiguous response")
    return pages[0]


def refresh():
    if len(sys.argv) > 3:
        raise ValueError("usage: scripts/refresh-test-durations.sh [pipeline-id]")
    pipeline_id = sys.argv[2] if len(sys.argv) == 3 else None
    if pipeline_id is None:
        pipelines = api("projects/:id/pipelines?ref=main&status=success&order_by=id&sort=desc&per_page=1")
        if not isinstance(pipelines, list) or not pipelines:
            raise ValueError("no successful main pipeline found")
        pipeline_id = str(pipelines[0]["id"])
    if not re.fullmatch(r"[1-9][0-9]*", pipeline_id):
        raise ValueError("pipeline-id must be a positive integer")
    pipeline = api(f"projects/:id/pipelines/{pipeline_id}")
    if pipeline.get("id") != int(pipeline_id) or pipeline.get("ref") != "main" or pipeline.get("status") != "success":
        raise ValueError("choose a successful main pipeline")

    jobs = api(f"projects/:id/pipelines/{pipeline_id}/jobs?include_retried=false&per_page=100", paginate=True)
    shard_jobs = {}
    for job in jobs:
        match = re.fullmatch(r"integration-shards ([1-4])/4", job.get("name", ""))
        if match:
            shard = int(match[1])
            if shard in shard_jobs or job.get("status") != "success":
                raise ValueError("integration shard jobs must be unique and successful")
            shard_jobs[shard] = job["id"]
    if set(shard_jobs) != {1, 2, 3, 4}:
        raise ValueError("pipeline must contain all four successful integration shards")

    merged = {}
    for shard in range(1, 5):
        job_id = shard_jobs[shard]
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
            raise ValueError("invalid integration job ID")
        durations = api(f"projects/:id/jobs/{job_id}/artifacts/apps/api/.test_durations")
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
    print(f"wrote {len(merged)} durations from GitLab pipeline {pipeline_id}; review the diff and commit")


try:
    refresh()
except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
    # API stderr can include request diagnostics; disclose only the failure class.
    if isinstance(error, subprocess.CalledProcessError):
        message = "GitLab API request failed; check glab authentication and artifact retention"
    else:
        message = str(error)
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)
PYEOF
