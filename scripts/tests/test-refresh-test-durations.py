#!/usr/bin/env python3
"""Exercise the artifact refresh against local GitLab API fixtures; no network."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "refresh-test-durations.sh"


class RefreshDurationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "apps/api").mkdir(parents=True)
        (self.root / "bin").mkdir()
        shutil.copyfile(SOURCE, self.root / "scripts/refresh-test-durations.sh")
        self.target = self.root / "apps/api/.test_durations"
        self.target.write_text('{"original": 9}\n')
        self.fixture = {
            "pipeline": {"id": 77, "ref": "main", "status": "success"},
            "jobs": [
                {"id": 100 + i, "name": f"integration-shards {i}/4", "status": "success"}
                for i in range(1, 5)
            ],
            "artifacts": {str(100 + i): {f"test_{i}": float(i)} for i in range(1, 5)},
        }
        fake = self.root / "bin/glab"
        fake.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['REFRESH_FIXTURE'])
data = json.loads((root / 'fixture.json').read_text())
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
assert args[0] == 'api', args
path = args[1]
if path.startswith('projects/:id/pipelines?'):
    print(json.dumps([data['pipeline']]))
elif path == 'projects/:id/pipelines/77':
    print(json.dumps(data['pipeline']))
elif path.startswith('projects/:id/pipelines/77/jobs?'):
    assert '--paginate' in args
    if data.get('paginated'):
        print(json.dumps(data['jobs'][:2]))
        print(json.dumps(data['jobs'][2:]))
    else:
        print(json.dumps(data['jobs']))
elif path.startswith('projects/:id/jobs/'):
    assert path.endswith('/artifacts/apps/api/.test_durations'), path
    job_id = path.split('/')[3]
    if data.get('download_failure') == job_id:
        sys.exit(1)
    print(json.dumps(data['artifacts'][job_id]))
else:
    raise AssertionError(path)
""")
        fake.chmod(0o755)
        # The old hosting path must never be used by these local fixture runs.
        forbidden = self.root / "bin/gh"
        forbidden.write_text('#!/bin/sh\necho "retired hosting command invoked" >&2\nexit 99\n')
        forbidden.chmod(0o755)

    def run_refresh(self, *args):
        (self.root / "fixture.json").write_text(json.dumps(self.fixture))
        env = os.environ.copy()
        env["PATH"] = f"{self.root / 'bin'}:{env['PATH']}"
        env["REFRESH_FIXTURE"] = str(self.root)
        return subprocess.run(
            ["bash", str(self.root / "scripts/refresh-test-durations.sh"), *args],
            env=env, text=True, capture_output=True,
        )

    def assert_rejected_without_changes(self):
        result = self.run_refresh("77")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.target.read_text(), '{"original": 9}\n')

    def test_merges_four_disjoint_shards_from_latest_successful_main_pipeline(self):
        result = self.run_refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.target.read_text()), {f"test_{i}": float(i) for i in range(1, 5)})
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        self.assertIn("ref=main&status=success", calls[0][1])

    def test_accepts_paginated_job_arrays_with_explicit_pipeline(self):
        self.fixture["paginated"] = True
        result = self.run_refresh("77")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_non_main_pipeline(self):
        self.fixture["pipeline"]["ref"] = "feature"
        self.assert_rejected_without_changes()

    def test_rejects_failed_pipeline(self):
        self.fixture["pipeline"]["status"] = "failed"
        self.assert_rejected_without_changes()

    def test_rejects_missing_shard(self):
        self.fixture["jobs"].pop()
        self.assert_rejected_without_changes()

    def test_rejects_failed_shard(self):
        self.fixture["jobs"][0]["status"] = "failed"
        self.assert_rejected_without_changes()

    def test_rejects_overlapping_test_names(self):
        self.fixture["artifacts"]["102"] = {"test_1": 2.0}
        self.assert_rejected_without_changes()

    def test_rejects_invalid_duration_values(self):
        self.fixture["artifacts"]["102"] = {"test_2": "not a duration"}
        self.assert_rejected_without_changes()

    def test_download_failure_keeps_original_file(self):
        self.fixture["download_failure"] = "103"
        self.assert_rejected_without_changes()


if __name__ == "__main__":
    unittest.main()
