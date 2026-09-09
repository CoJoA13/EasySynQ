"""Private entry point for the isolated external-audit worker process."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from .audit import (
    _EXTERNAL_ENVIRONMENT_KEYS,
    _EXTERNAL_FIXED_ENVIRONMENT,
    _EXTERNAL_LIVE_MODE,
    _EXTERNAL_WORKER_MODES,
    _emit_external_report,
    _external_failure_report,
    _run_external,
)


def _environment_is_exact(environ: Mapping[str, str]) -> bool:
    return set(environ) == set(_EXTERNAL_ENVIRONMENT_KEYS) and all(
        environ.get(name) == value for name, value in _EXTERNAL_FIXED_ENVIRONMENT.items()
    )


def _configuration_failure(*, mode: str) -> int:
    return _emit_external_report(
        _external_failure_report(
            None,
            code="CONFIG_INVALID",
            message="external verifier configuration is invalid",
            mode=mode,
        ),
        configuration=True,
    )


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Validate the isolated process contract, then run the strict verifier once."""
    supplied_argv = list(sys.argv[1:] if argv is None else argv)
    mode = (
        supplied_argv[0]
        if supplied_argv and supplied_argv[0] in _EXTERNAL_WORKER_MODES
        else _EXTERNAL_LIVE_MODE
    )
    if (
        len(supplied_argv) != 2
        or supplied_argv[0] not in _EXTERNAL_WORKER_MODES
        or not _environment_is_exact(os.environ)
    ):
        return _configuration_failure(mode=mode)
    return _run_external(Path(supplied_argv[1]), os.environ, mode=supplied_argv[0])


if __name__ == "__main__":
    raise SystemExit(main())
