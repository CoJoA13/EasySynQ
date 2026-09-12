"""Fresh isolated readers must not load unused database or application settings code."""

from __future__ import annotations

import subprocess
import sys
import typing
from pathlib import Path

import pytest

_IMPORT_PROBE = """
import importlib
import importlib.abc
import sys

forbidden = (
    'sqlalchemy',
    'easysynq_api.config',
    'easysynq_api.db',
    'easysynq_api.services.audit.checkpoint',
)

def unrelated(name):
    return any(name == prefix or name.startswith(prefix + '.') for prefix in forbidden)

assert not any(unrelated(name) for name in sys.modules)

class RejectUnusedDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if unrelated(fullname):
            raise AssertionError('isolated reader imported unrelated dependency: ' + fullname)

sys.meta_path.insert(0, RejectUnusedDependency())
module = importlib.import_module(sys.argv[1])
assert callable(getattr(module, sys.argv[2]))
assert not any(unrelated(name) for name in sys.modules)
print('isolated reader import boundary passed')
"""


@pytest.mark.parametrize(
    ("module", "reader"),
    [
        ("isolated_raw", "read_raw_checkpoint_version_isolated"),
        ("isolated_version_page", "read_raw_checkpoint_version_page_isolated"),
    ],
)
def test_fresh_isolated_reader_does_not_import_database_or_settings(
    module: str, reader: str, tmp_path: Path
) -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter, probe, and two reader modules
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _IMPORT_PROBE,
            "easysynq_api.services.audit." + module,
            reader,
        ],
        cwd=tmp_path,
        env={"LANG": "C.UTF-8", "TZ": "UTC"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "isolated reader import boundary passed\n"


def test_legacy_verifier_runtime_type_remains_shared_and_resolvable() -> None:
    from easysynq_api.services.audit import checkpoint, trust, verify

    assert trust.LegacySignatureVerifier is checkpoint.LegacySignatureVerifier
    assert verify.LegacySignatureVerifier is checkpoint.LegacySignatureVerifier
    assert typing.get_type_hints(trust.legacy_verifier)["return"] is (
        checkpoint.LegacySignatureVerifier
    )


def test_sink_settings_still_use_the_existing_cached_settings() -> None:
    from easysynq_api import config
    from easysynq_api.services.audit import sink

    settings = config.get_settings()
    assert sink.get_settings() is settings
    assert sink.get_settings() is settings
