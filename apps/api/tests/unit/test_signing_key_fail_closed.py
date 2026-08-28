"""RES-SECRETS-VOLUME-SILENT-EPHEMERAL-KEY — signing keys must be durably persisted.

Both loaders used to catch ``OSError`` and continue with an ephemeral in-memory key behind a log
warning. Nothing else noticed: readiness probes PostgreSQL, Redis, MinIO, Keycloak and Alembic, not
these paths, so an upgrade reported green while every printed verify QR read UNKNOWN and every
off-host checkpoint anchored under the previous key became unverifiable (R13/D-8).

That became easy to reach once the containers stopped running as root (audit U33): a ``secrets``
volume created by an earlier root-running build stays root-owned across an upgrade.
"""

from __future__ import annotations

import os
import sys
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.audit import checkpoint as cp
from easysynq_api.services.common.signing import (
    SigningKeyUnavailable,
    check_signing_key_path,
    ensure_signing_keys_persistable,
)
from easysynq_api.services.vault import verify_token as vt


@pytest.fixture
def unwritable(tmp_path: Path) -> Any:
    """A directory the process cannot write — the root-owned-volume shape, without needing root."""
    blocked = tmp_path / "secrets"
    blocked.mkdir()
    blocked.chmod(0o500)  # r-x: traversable, not writable
    yield blocked
    blocked.chmod(0o700)


# --- the probe -------------------------------------------------------------------------------


def test_probe_accepts_a_writable_directory(tmp_path: Path) -> None:
    assert check_signing_key_path("k", tmp_path / "sub" / "key.pem") is None


def test_probe_rejects_an_unwritable_directory(unwritable: Path) -> None:
    problem = check_signing_key_path("k", unwritable / "key.pem")
    assert problem is not None
    assert "not writable" in problem


def test_probe_accepts_an_existing_readable_key(tmp_path: Path) -> None:
    key = tmp_path / "key.pem"
    key.write_bytes(b"x")
    assert check_signing_key_path("k", key) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_probe_rejects_an_existing_unreadable_key(tmp_path: Path) -> None:
    key = tmp_path / "key.pem"
    key.write_bytes(b"x")
    key.chmod(0o000)
    try:
        problem = check_signing_key_path("k", key)
    finally:
        key.chmod(0o600)
    assert problem is not None
    assert "not readable" in problem


# --- the startup gate ------------------------------------------------------------------------


def _settings(secrets: Path, *, allow: bool) -> Settings:
    return Settings(
        verify_token_signing_key_path=str(secrets / "verify_token_key"),
        audit_checkpoint_signing_key_path=str(secrets / "audit_ckpt_key"),
        allow_ephemeral_signing_keys=allow,
    )


def test_startup_gate_refuses_an_unwritable_secrets_volume(unwritable: Path) -> None:
    with pytest.raises(SigningKeyUnavailable) as excinfo:
        ensure_signing_keys_persistable(_settings(unwritable, allow=False))
    message = str(excinfo.value)
    assert "uid 10001" in message, "the operator needs to know WHICH ownership to fix"
    assert "ALLOW_EPHEMERAL_SIGNING_KEYS" in message, "and how to opt out in development"


def test_startup_gate_passes_on_a_writable_volume(tmp_path: Path) -> None:
    ensure_signing_keys_persistable(_settings(tmp_path, allow=False))


def test_startup_gate_yields_to_the_explicit_development_opt_in(unwritable: Path) -> None:
    ensure_signing_keys_persistable(_settings(unwritable, allow=True))


def test_the_worker_gate_is_a_bootstep_not_a_signal() -> None:
    """A signal receiver CANNOT abort startup.

    Measured: a ``worker_init`` handler that raises logs a traceback and the worker then reports
    "celery@... ready" and keeps running, because Celery's ``Signal.send`` catches ``Exception``.
    Only a bootstep's ``start`` propagates.
    """
    from easysynq_api.tasks.app import SigningKeyGate, app

    assert SigningKeyGate in app.steps["worker"], "the worker gate must be a registered bootstep"


def test_the_bootstep_actually_calls_the_gate_and_propagates(monkeypatch: Any) -> None:
    """Registering the bootstep is not enough — its ``start`` must call the gate and re-raise.

    Without this, gutting the bootstep body leaves every other assertion in this file green.
    """
    import easysynq_api.tasks.app  # noqa: F401 - ensure the submodule is imported

    tasks_app = sys.modules["easysynq_api.tasks.app"]

    called: list[bool] = []

    def _boom() -> None:
        called.append(True)
        raise SigningKeyUnavailable("unwritable")

    monkeypatch.setattr(tasks_app, "ensure_signing_keys_persistable", _boom)
    step = tasks_app.SigningKeyGate(parent=None)
    with pytest.raises(SigningKeyUnavailable):
        step.start(None)
    assert called, "the bootstep did not invoke the signing-key gate"


def test_the_beat_gate_calls_the_gate_and_exits(monkeypatch: Any) -> None:
    import easysynq_api.tasks.app  # noqa: F401 - ensure the submodule is imported

    tasks_app = sys.modules["easysynq_api.tasks.app"]

    called: list[bool] = []

    def _boom() -> None:
        called.append(True)
        raise SigningKeyUnavailable("unwritable")

    monkeypatch.setattr(tasks_app, "ensure_signing_keys_persistable", _boom)
    with pytest.raises(SystemExit):
        tasks_app._gate_beat_startup()
    assert called, "the beat gate did not invoke the signing-key gate"


def test_the_beat_gate_uses_systemexit_because_beat_has_no_bootsteps() -> None:
    """``celery.beat.Service`` carries no blueprint, so ``app.steps["beat"]`` is consumed by
    nothing — measured: beat started normally with an unwritable key path. ``SystemExit`` is a
    ``BaseException``, so it escapes the signal dispatcher's ``except Exception``."""
    from celery.signals import beat_init

    from easysynq_api.tasks.app import _gate_beat_startup

    connected = [
        receiver() if isinstance(receiver, weakref.ref) else receiver
        for _, receiver in beat_init.receivers
    ]
    assert _gate_beat_startup in connected, "beat_init is not connected to the signing-key gate"

    source = Path(__file__).resolve().parents[2] / "src/easysynq_api/tasks/app.py"
    assert "raise SystemExit(1)" in source.read_text(), (
        "a plain raise is swallowed by Celery's signal dispatch; beat would keep running"
    )


# --- the loaders themselves --------------------------------------------------------------------


def test_verify_token_loader_refuses_rather_than_minting_unverifiable_tokens(
    unwritable: Path, monkeypatch: Any
) -> None:
    """An ephemeral key mints QR tokens no other process can verify — every copy reads UNKNOWN."""
    monkeypatch.setattr(vt, "_cached_key", None, raising=False)
    monkeypatch.setattr(
        vt,
        "get_settings",
        lambda: SimpleNamespace(
            verify_token_signing_key_path=str(unwritable / "verify_token_key"),
            allow_ephemeral_signing_keys=False,
        ),
    )
    with pytest.raises(SigningKeyUnavailable):
        vt.load_verify_signing_key()


def test_checkpoint_loader_refuses_rather_than_orphaning_anchored_checkpoints(
    unwritable: Path, monkeypatch: Any
) -> None:
    """Regenerating this key makes every checkpoint anchored under the old one unverifiable."""
    monkeypatch.setattr(
        cp,
        "get_settings",
        lambda: SimpleNamespace(
            audit_checkpoint_signing_key_path=str(unwritable / "audit_ckpt_key"),
            audit_checkpoint_public_key_path=str(unwritable / "audit_ckpt_pub"),
            allow_ephemeral_signing_keys=False,
        ),
    )
    with pytest.raises(SigningKeyUnavailable):
        cp.load_signing_key()


def test_the_api_process_is_not_gated() -> None:
    """The api only VERIFIES. Reading UNKNOWN before the worker has minted is legitimate, so
    gating the api would refuse to serve on a perfectly healthy first boot."""
    main = Path(__file__).resolve().parents[2] / "src/easysynq_api/main.py"
    assert "ensure_signing_keys_persistable" not in main.read_text()
