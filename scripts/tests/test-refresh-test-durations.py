#!/usr/bin/env python3
"""Exercise the artifact refresh against local GitHub Actions API fixtures; no network."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "refresh-test-durations.sh"
ORIGINAL = '{"original": 9}\n'


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
        self.target.write_text(ORIGINAL)
        self.fixture = {
            "run": {
                "id": 77,
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "path": ".github/workflows/ci.yml",
            },
            "artifacts": [
                {"id": 100 + i, "name": f"test-durations-{i}", "expired": False}
                for i in range(1, 5)
            ],
            "contents": {f"test-durations-{i}": {f"test_{i}": float(i)} for i in range(1, 5)},
        }
        fake = self.root / "bin/gh"
        fake.write_text("""#!/usr/bin/env python3
import io, json, os, pathlib, sys, zipfile
root = pathlib.Path(os.environ['REFRESH_FIXTURE'])
data = json.loads((root / 'fixture.json').read_text())
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if args[0] == 'api':
    assert len(args) == 2, args
    path = args[1]
    assert '{owner}/{repo}' in path, path
    if path.startswith('repos/{owner}/{repo}/actions/workflows/ci.yml/runs?'):
        print(json.dumps({'total_count': 1, 'workflow_runs': [data['run']]}))
    elif path == 'repos/{owner}/{repo}/actions/runs/77':
        print(json.dumps(data['run']))
    elif path.startswith('repos/{owner}/{repo}/actions/runs/77/artifacts?'):
        print(json.dumps({'total_count': len(data['artifacts']), 'artifacts': data['artifacts']}))
    else:
        raise AssertionError(path)
elif args[:2] == ['run', 'download']:
    # gh run download <run-id> -n <artifact-name> -D <dir>: fetch the artifact zip, extract into dir.
    assert len(args) == 7 and args[3] == '-n' and args[5] == '-D', args
    assert args[2] == '77', args
    name = args[4]
    if data.get('download_failure') == name:
        print('artifact download failed', file=sys.stderr)
        sys.exit(1)
    assert any(a['name'] == name for a in data['artifacts']), name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('.test_durations', json.dumps(data['contents'][name]))
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        archive.extractall(args[6])
else:
    raise AssertionError(args)
""")
        fake.chmod(0o755)
        # The old hosting path must never be used by these local fixture runs.
        forbidden = self.root / "bin/glab"
        forbidden.write_text('#!/bin/sh\necho "retired hosting command invoked" >&2\nexit 99\n')
        forbidden.chmod(0o755)

    def run_refresh(self, *args):
        (self.root / "fixture.json").write_text(json.dumps(self.fixture))
        env = os.environ.copy()
        env["PATH"] = f"{self.root / 'bin'}:{env['PATH']}"
        env["REFRESH_FIXTURE"] = str(self.root)
        result = subprocess.run(
            ["bash", str(self.root / "scripts/refresh-test-durations.sh"), *args],
            env=env, text=True, capture_output=True,
        )
        self.assertNotIn("retired hosting command invoked", result.stderr)
        return result

    def calls(self):
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def assert_no_temporary_files(self):
        leftovers = [p.name for p in (self.root / "apps/api").iterdir() if p.name != ".test_durations"]
        self.assertEqual(leftovers, [])

    def assert_rejected_without_changes(self, *args):
        result = self.run_refresh(*(args or ("77",)))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.target.read_text(), ORIGINAL)
        self.assert_no_temporary_files()
        return result

    def test_merges_four_disjoint_shards_from_latest_successful_main_run(self):
        result = self.run_refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.target.read_text()), {f"test_{i}": float(i) for i in range(1, 5)})
        self.assert_no_temporary_files()
        calls = self.calls()
        self.assertEqual(calls[0][0], "api")
        self.assertIn("actions/workflows/ci.yml/runs?", calls[0][1])
        self.assertIn("branch=main&status=success", calls[0][1])
        downloads = [c for c in calls if c[:2] == ["run", "download"]]
        self.assertEqual([c[4] for c in downloads], [f"test-durations-{i}" for i in range(1, 5)])

    def test_accepts_explicit_run_id_without_listing_runs(self):
        result = self.run_refresh("77")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("actions/workflows/ci.yml/runs?", self.calls()[0][1])
        self.assertIn("run 77", result.stdout)

    def test_rejects_malformed_run_ids_before_any_request(self):
        for bad in ("abc", "0", "-5", "77x"):
            with self.subTest(run_id=bad):
                self.assert_rejected_without_changes(bad)
                self.assertEqual(self.calls(), [])

    def test_rejects_too_many_arguments(self):
        self.assert_rejected_without_changes("77", "78")
        self.assertEqual(self.calls(), [])

    def test_rejects_run_whose_id_differs_from_request(self):
        self.fixture["run"]["id"] = 78
        self.assert_rejected_without_changes()

    def test_rejects_non_main_run(self):
        self.fixture["run"]["head_branch"] = "feature"
        self.assert_rejected_without_changes()

    def test_rejects_failed_run(self):
        self.fixture["run"]["conclusion"] = "failure"
        self.assert_rejected_without_changes()

    def test_rejects_incomplete_run(self):
        self.fixture["run"]["status"] = "in_progress"
        self.fixture["run"]["conclusion"] = None
        self.assert_rejected_without_changes()

    def test_rejects_run_of_another_workflow(self):
        self.fixture["run"]["path"] = ".github/workflows/nightly.yml"
        self.assert_rejected_without_changes()

    def test_rejects_missing_shard(self):
        self.fixture["artifacts"].pop()
        self.assert_rejected_without_changes()

    def test_rejects_duplicate_shard_artifact(self):
        self.fixture["artifacts"].append({"id": 105, "name": "test-durations-1", "expired": False})
        self.assert_rejected_without_changes()

    def test_rejects_expired_shard_artifact(self):
        self.fixture["artifacts"][0]["expired"] = True
        self.assert_rejected_without_changes()

    def test_rejects_overlapping_test_names(self):
        self.fixture["contents"]["test-durations-2"] = {"test_1": 2.0}
        self.assert_rejected_without_changes()

    def test_rejects_invalid_duration_values(self):
        self.fixture["contents"]["test-durations-2"] = {"test_2": "not a duration"}
        self.assert_rejected_without_changes()

    def test_rejects_empty_shard_mapping(self):
        self.fixture["contents"]["test-durations-3"] = {}
        self.assert_rejected_without_changes()

    def test_download_failure_keeps_original_file(self):
        self.fixture["download_failure"] = "test-durations-3"
        result = self.assert_rejected_without_changes()
        self.assertIn("GitHub API request failed", result.stderr)
        self.assertNotIn("artifact download failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
