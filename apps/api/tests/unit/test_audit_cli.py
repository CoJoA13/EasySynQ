from __future__ import annotations

import base64
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from easysynq_api.cli import _audit_external as audit_external_worker
from easysynq_api.cli import audit as audit_cli
from easysynq_api.services.audit import external
from easysynq_api.services.audit.trust import (
    ExternalCredentials,
    TrustDescriptor,
    TrustedLegacyKey,
    TrustedOrganization,
    TrustedWitness,
)

pytestmark = pytest.mark.unit


def _descriptor() -> TrustDescriptor:
    public_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32).public_key()
    return TrustDescriptor(
        descriptor_id=uuid.UUID(int=99),
        sha256="d" * 64,
        organizations=(
            TrustedOrganization(
                org_id=uuid.UUID(int=1_000),
                public_keys=(TrustedLegacyKey("ed25519-sha256:" + "a" * 64, public_key),),
                witnesses=(
                    TrustedWitness(
                        witness_id=uuid.UUID(int=10_000),
                        kind="worm_bucket",
                        endpoint="https://witness.example.test",
                        bucket="audit-witness",
                        region="us-central-1",
                    ),
                ),
            ),
        ),
    )


def _credentials() -> ExternalCredentials:
    return ExternalCredentials(
        database_url="postgresql+psycopg://reader:secret@db.example.test/audit",
        access_key="explicit-access",
        secret_key="explicit-secret",
    )


def _worker_environment(credentials: ExternalCredentials | None = None) -> dict[str, str]:
    supplied = credentials or _credentials()
    return {
        "DATABASE_URL": supplied.database_url,
        "AUDIT_SINK_READ_ACCESS_KEY": supplied.access_key,
        "AUDIT_SINK_READ_SECRET_KEY": supplied.secret_key,
        "HOME": "/dev/null",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }


def _write_descriptor(path: Path, descriptor_id: uuid.UUID) -> bytes:
    public_key = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32).public_key()
    raw_key = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    document = {
        "format_version": 1,
        "descriptor_id": str(descriptor_id),
        "organizations": [
            {
                "org_id": "00000000-0000-0000-0000-000000001000",
                "public_keys": [
                    {
                        "key_id": "ed25519-sha256:" + hashlib.sha256(raw_key).hexdigest(),
                        "public_key": base64.b64encode(raw_key).decode("ascii"),
                    }
                ],
                "witnesses": [
                    {
                        "witness_id": "00000000-0000-0000-0000-000000010000",
                        "kind": "worm_bucket",
                        "endpoint": "https://witness.example.test",
                        "bucket": "audit-witness",
                        "region": "us-central-1",
                    }
                ],
            }
        ],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode()
    path.write_bytes(encoded)
    return encoded


def test_explicit_trust_option_has_safe_json_configuration_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://audit-reader:synthetic@127.0.0.1:5432/easysynq",
    )
    monkeypatch.setenv("AUDIT_SINK_READ_ACCESS_KEY", "synthetic-access-key")
    monkeypatch.setenv("AUDIT_SINK_READ_SECRET_KEY", "synthetic-secret-key")
    path = tmp_path / "trust.json"
    path.write_text('{"format_version": 1}', encoding="utf-8")

    result = audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])

    report = json.loads(capsys.readouterr().out)
    assert result == 2
    assert report["verified"] is False
    assert report["reasons"][0]["code"] == "CONFIG_INVALID"


def _report(
    *,
    verified: bool,
    descriptor: TrustDescriptor | None = None,
    mode: str = audit_cli._EXTERNAL_LIVE_MODE,
) -> external.ExternalVerificationReport:
    reasons = () if verified else (external.VerificationReason("WITNESS_INVALID", "failed"),)
    return external.ExternalVerificationReport(
        descriptor_id=None if descriptor is None else descriptor.descriptor_id,
        descriptor_sha256=None if descriptor is None else descriptor.sha256,
        verified=verified,
        checked=0,
        pending=0,
        sinks_read=0,
        unenrolled_orgs_present=False,
        organizations=(),
        reasons=reasons,
        reasons_omitted=0,
        mode=mode,
    )


@pytest.mark.parametrize(("verified", "expected_exit"), [(True, 0), (False, 1)])
def test_private_worker_emits_only_json_and_uses_external_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    verified: bool,
    expected_exit: int,
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    credentials = _credentials()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda supplied: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda supplied: credentials)
    monkeypatch.setattr(
        audit_cli,
        "get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("strict mode consulted Settings")),
    )
    monkeypatch.setattr(
        audit_cli,
        "load_verify_key",
        lambda: (_ for _ in ()).throw(AssertionError("strict mode loaded a local key")),
    )

    async def verify(
        supplied_descriptor: TrustDescriptor, supplied_credentials: ExternalCredentials
    ) -> Any:
        assert supplied_descriptor is descriptor
        assert supplied_credentials is credentials
        return _report(verified=verified)

    monkeypatch.setattr(audit_cli, "_verify_external", verify)
    monkeypatch.setattr(audit_external_worker.os, "environ", _worker_environment(credentials))

    result = audit_external_worker.main([audit_cli._EXTERNAL_LIVE_MODE, str(path)])
    captured = capsys.readouterr()

    assert result == expected_exit
    assert json.loads(captured.out)["verified"] is verified
    assert captured.err == ""


class _ExecReached(BaseException):
    """Stop the test at the required process replacement boundary."""


def test_explicit_trust_mode_reexecs_with_only_reviewed_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    credentials = _credentials()
    inherited_sentinels = {
        "PGHOSTADDR": "routing-sentinel",
        "PGPORT": "6432",
        "PGSERVICE": "service-sentinel",
        "PGSERVICEFILE": "/sentinel/pg-service.conf",
        "PGSSLMODE": "disable",
        "PGOPTIONS": "-c search_path=sentinel",
        "PYTHONHOME": "/sentinel/python-home",
        "PYTHONPATH": "/sentinel/python-path",
        "LD_PRELOAD": "/sentinel/loader.so",
        "HTTPS_PROXY": "http://proxy-sentinel.invalid",
        "SSL_CERT_FILE": "/sentinel/ssl-cert.pem",
        "OPENSSL_CONF": "/sentinel/openssl.cnf",
        "AWS_PROFILE": "ambient-profile-sentinel",
        "AWS_SHARED_CREDENTIALS_FILE": "/sentinel/aws-credentials",
        "LD_LIBRARY_PATH": "/sentinel/loader-path",
    }
    for name, value in inherited_sentinels.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("HOME", "/sentinel/home")
    monkeypatch.setenv("DATABASE_URL", credentials.database_url)
    monkeypatch.setenv("AUDIT_SINK_READ_ACCESS_KEY", credentials.access_key)
    monkeypatch.setenv("AUDIT_SINK_READ_SECRET_KEY", credentials.secret_key)
    monkeypatch.chdir(tmp_path)
    parent_environment = dict(audit_cli.os.environ)

    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda supplied: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda supplied: credentials)

    bootstrap = (
        "import runpy, sys\n"
        "sys.path.insert(0, sys.argv.pop(1))\n"
        'runpy.run_module("easysynq_api.cli._audit_external", run_name="__main__")'
    )
    expected_source_root = str(Path(audit_cli.__file__).resolve().parents[2])
    expected_argv = [
        sys.executable,
        "-I",
        "-B",
        "-u",
        "-c",
        bootstrap,
        expected_source_root,
        audit_cli._EXTERNAL_LIVE_MODE,
        str(path.resolve()),
    ]
    expected_environment = {
        "DATABASE_URL": credentials.database_url,
        "AUDIT_SINK_READ_ACCESS_KEY": credentials.access_key,
        "AUDIT_SINK_READ_SECRET_KEY": credentials.secret_key,
        "HOME": "/dev/null",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }

    def execve(executable: str, argv: list[str], environment: dict[str, str]) -> None:
        assert executable == sys.executable
        assert Path(executable).is_absolute()
        assert argv == expected_argv
        assert environment == expected_environment
        assert all(
            secret not in argument
            for secret in (
                credentials.database_url,
                credentials.access_key,
                credentials.secret_key,
            )
            for argument in argv
        )
        assert inherited_sentinels.keys().isdisjoint(environment)
        raise _ExecReached

    monkeypatch.setattr(audit_cli.os, "execve", execve)

    with pytest.raises(_ExecReached):
        audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])

    assert dict(audit_cli.os.environ) == parent_environment
    assert capsys.readouterr() == ("", "")


def test_historical_target_requires_an_explicit_trust_descriptor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        audit_cli.main(["verify-offhost", "--historical-target"])

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert captured.out == ""
    assert "--historical-target requires --trust-descriptor" in captured.err


def test_historical_target_reexecs_worker_with_explicit_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    credentials = _credentials()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda _env: credentials)
    monkeypatch.setattr(
        audit_cli.os,
        "environ",
        {
            "DATABASE_URL": credentials.database_url,
            "AUDIT_SINK_READ_ACCESS_KEY": credentials.access_key,
            "AUDIT_SINK_READ_SECRET_KEY": credentials.secret_key,
        },
    )

    def execve(_executable: str, argv: list[str], environment: dict[str, str]) -> None:
        assert argv[-2:] == [audit_cli._EXTERNAL_HISTORICAL_MODE, str(path)]
        assert environment == _worker_environment(credentials)
        raise _ExecReached

    monkeypatch.setattr(audit_cli.os, "execve", execve)

    with pytest.raises(_ExecReached):
        audit_cli.main(["verify-offhost", "--trust-descriptor", str(path), "--historical-target"])

    assert capsys.readouterr() == ("", "")


def test_private_worker_dispatches_historical_verifier_and_emits_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    credentials = _credentials()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda _env: credentials)

    async def verify_historical(
        supplied_descriptor: TrustDescriptor,
        supplied_credentials: ExternalCredentials,
    ) -> external.ExternalVerificationReport:
        assert supplied_descriptor is descriptor
        assert supplied_credentials is credentials
        return _report(
            verified=True,
            descriptor=descriptor,
            mode=audit_cli._EXTERNAL_HISTORICAL_MODE,
        )

    monkeypatch.setattr(audit_cli, "_verify_historical_external", verify_historical)
    monkeypatch.setattr(
        audit_cli,
        "_verify_external",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("historical worker dispatched the live verifier")
        ),
    )
    monkeypatch.setattr(audit_external_worker.os, "environ", _worker_environment(credentials))

    result = audit_external_worker.main([audit_cli._EXTERNAL_HISTORICAL_MODE, str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 0
    assert report["mode"] == audit_cli._EXTERNAL_HISTORICAL_MODE
    assert report["descriptor_id"] == str(descriptor.descriptor_id)
    assert captured.err == ""


def _database_url_at_length(length: int, *, explicit_timeout: bool) -> str:
    prefix = "postgresql+psycopg://reader:url-secret-sentinel@db.example.test/"
    suffix = "?connect_timeout=5" if explicit_timeout else ""
    return prefix + ("d" * (length - len(prefix) - len(suffix))) + suffix


@pytest.mark.parametrize(
    ("length", "explicit_timeout", "expected_exec", "expected_exit"),
    [
        pytest.param(8_192, False, True, 0, id="max-without-timeout"),
        pytest.param(8_193, False, False, 2, id="over-max-without-timeout"),
        pytest.param(8_192, True, True, 0, id="max-with-explicit-timeout"),
    ],
)
def test_parent_forwards_original_database_url_to_actual_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    length: int,
    explicit_timeout: bool,
    expected_exec: bool,
    expected_exit: int,
) -> None:
    path = tmp_path / "trust.json"
    _write_descriptor(path, uuid.UUID(int=303))
    database_url = _database_url_at_length(length, explicit_timeout=explicit_timeout)
    assert len(database_url) == length
    parent_environment = {
        "DATABASE_URL": database_url,
        "AUDIT_SINK_READ_ACCESS_KEY": "boundary-access-sentinel",
        "AUDIT_SINK_READ_SECRET_KEY": "boundary-secret-sentinel",
    }
    monkeypatch.setattr(audit_cli.os, "environ", parent_environment)
    verified_descriptors: list[TrustDescriptor] = []

    async def verify(
        descriptor: TrustDescriptor,
        credentials: ExternalCredentials,
    ) -> external.ExternalVerificationReport:
        assert credentials.access_key == "boundary-access-sentinel"
        assert credentials.secret_key == "boundary-secret-sentinel"
        assert credentials.database_url.endswith("connect_timeout=5")
        verified_descriptors.append(descriptor)
        return _report(verified=True, descriptor=descriptor)

    monkeypatch.setattr(audit_cli, "_verify_external", verify)
    exec_observed: dict[str, object] = {}

    def execve(_executable: str, argv: list[str], environment: dict[str, str]) -> None:
        exec_observed["environment"] = environment
        monkeypatch.setattr(audit_external_worker.os, "environ", environment)
        exec_observed["worker_exit"] = audit_external_worker.main(argv[-2:])
        raise _ExecReached

    monkeypatch.setattr(audit_cli.os, "execve", execve)

    if expected_exec:
        with pytest.raises(_ExecReached):
            audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])
        result = exec_observed["worker_exit"]
    else:
        result = audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])
        assert exec_observed == {}

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert result == expected_exit, report
    if expected_exec:
        environment = exec_observed["environment"]
        assert isinstance(environment, dict)
        assert environment["DATABASE_URL"] == database_url
        assert verified_descriptors
    else:
        assert verified_descriptors == []
    assert report["verified"] is expected_exec
    assert captured.err == ""
    assert "url-secret-sentinel" not in captured.out
    assert "boundary-access-sentinel" not in captured.out
    assert "boundary-secret-sentinel" not in captured.out


def test_parent_credential_snapshot_cannot_be_swapped_after_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    original = {
        "DATABASE_URL": "postgresql+psycopg://reader:original-secret@db.example.test/audit",
        "AUDIT_SINK_READ_ACCESS_KEY": "original-access",
        "AUDIT_SINK_READ_SECRET_KEY": "original-secret",
    }
    source_environment = dict(original)
    replacement_url = "postgresql+psycopg://reader:swapped-secret@swap.example.test/audit"
    monkeypatch.setattr(audit_cli.os, "environ", source_environment)
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    actual_loader = audit_cli.load_external_credentials

    def validate(snapshot: dict[str, str]) -> ExternalCredentials:
        credentials = actual_loader(snapshot)
        source_environment.update(
            {
                "DATABASE_URL": replacement_url,
                "AUDIT_SINK_READ_ACCESS_KEY": "swapped-access",
                "AUDIT_SINK_READ_SECRET_KEY": "swapped-secret",
            }
        )
        return credentials

    monkeypatch.setattr(audit_cli, "load_external_credentials", validate)
    child_environment: dict[str, str] = {}

    def execve(_executable: str, _argv: list[str], environment: dict[str, str]) -> None:
        child_environment.update(environment)
        raise _ExecReached

    monkeypatch.setattr(audit_cli.os, "execve", execve)

    with pytest.raises(_ExecReached):
        audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])

    assert source_environment["DATABASE_URL"] == replacement_url
    assert child_environment["DATABASE_URL"] == original["DATABASE_URL"]
    assert child_environment["AUDIT_SINK_READ_ACCESS_KEY"] == original["AUDIT_SINK_READ_ACCESS_KEY"]
    assert child_environment["AUDIT_SINK_READ_SECRET_KEY"] == original["AUDIT_SINK_READ_SECRET_KEY"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PGHOSTADDR", "routing-sentinel"),
        ("HOME", "/sentinel/home"),
        ("DATABASE_URL", None),
    ],
)
def test_private_worker_rejects_nonexact_environment_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str | None,
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    environment = _worker_environment()
    if value is None:
        environment.pop(name)
    else:
        environment[name] = value
    monkeypatch.setattr(
        audit_external_worker,
        "_run_external",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("invalid worker environment reached verification")
        ),
    )
    monkeypatch.setattr(audit_external_worker.os, "environ", environment)

    result = audit_external_worker.main([audit_cli._EXTERNAL_LIVE_MODE, str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 2
    assert report["reasons"][0]["code"] == "CONFIG_INVALID"
    assert report["descriptor_id"] is None
    assert captured.err == ""
    if value is not None:
        assert value not in captured.out
    assert "routing-sentinel" not in captured.out
    assert "explicit-access" not in captured.out
    assert "explicit-secret" not in captured.out


def test_private_worker_preserves_recognized_historical_mode_on_environment_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    invalid_environment = _worker_environment()
    invalid_environment["PGHOSTADDR"] = "routing-sentinel"
    monkeypatch.setattr(audit_external_worker.os, "environ", invalid_environment)

    result = audit_external_worker.main([audit_cli._EXTERNAL_HISTORICAL_MODE, str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 2
    assert report["mode"] == audit_cli._EXTERNAL_HISTORICAL_MODE
    assert report["verified"] is False
    assert report["reasons"][0]["code"] == "CONFIG_INVALID"
    assert "routing-sentinel" not in captured.out + captured.err


def test_private_worker_configuration_failure_is_safe_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(audit_external_worker.os, "environ", _worker_environment())

    result = audit_external_worker.main([audit_cli._EXTERNAL_LIVE_MODE, str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 2
    assert report["verified"] is False
    assert report["descriptor_id"] is None
    assert report["reasons"][0]["code"] == "CONFIG_INVALID"
    assert captured.err == ""


def test_private_worker_reports_identity_from_its_fresh_descriptor_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    first_bytes = _write_descriptor(path, uuid.UUID(int=101))
    parent_descriptor = audit_cli.load_trust_descriptor(path)
    replacement_id = uuid.UUID(int=202)
    replacement_bytes = _write_descriptor(path, replacement_id)
    seen: list[TrustDescriptor] = []

    async def verify(
        descriptor: TrustDescriptor,
        _credentials: ExternalCredentials,
    ) -> external.ExternalVerificationReport:
        seen.append(descriptor)
        return _report(verified=True, descriptor=descriptor)

    monkeypatch.setattr(audit_cli, "_verify_external", verify)
    monkeypatch.setattr(audit_external_worker.os, "environ", _worker_environment())

    result = audit_external_worker.main([audit_cli._EXTERNAL_LIVE_MODE, str(path)])
    report = json.loads(capsys.readouterr().out)

    assert result == 0
    assert parent_descriptor.sha256 == hashlib.sha256(first_bytes).hexdigest()
    assert parent_descriptor.descriptor_id != replacement_id
    assert seen[0].descriptor_id == replacement_id
    assert report["descriptor_id"] == str(replacement_id)
    assert report["descriptor_sha256"] == hashlib.sha256(replacement_bytes).hexdigest()


def test_parent_exec_failure_does_not_print_exception_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    credentials = _credentials()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda _environ: credentials)
    monkeypatch.setattr(
        audit_cli.os,
        "environ",
        {
            "DATABASE_URL": credentials.database_url,
            "AUDIT_SINK_READ_ACCESS_KEY": credentials.access_key,
            "AUDIT_SINK_READ_SECRET_KEY": credentials.secret_key,
        },
    )

    def failed_exec(*_args: object) -> None:
        raise OSError("explicit-secret at sentinel-endpoint.invalid")

    monkeypatch.setattr(audit_cli.os, "execve", failed_exec)

    result = audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 1
    assert report["verified"] is False
    assert report["reasons"][0]["code"] == "CHECK_INCOMPLETE"
    assert report["organizations"][0]["org_id"] == str(descriptor.organizations[0].org_id)
    assert report["organizations"][0]["witnesses"][0]["attempted"] is False
    assert report["organizations"][0]["witnesses"][0]["status"] == "incomplete"
    assert "explicit-secret" not in captured.out + captured.err
    assert "sentinel-endpoint" not in captured.out + captured.err


def test_private_worker_runtime_failure_does_not_print_exception_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(audit_cli, "load_external_credentials", lambda _environ: _credentials())

    async def failed(*_args: object) -> Any:
        raise RuntimeError("explicit-secret at sentinel-endpoint.invalid")

    monkeypatch.setattr(audit_cli, "_verify_external", failed)
    monkeypatch.setattr(audit_external_worker.os, "environ", _worker_environment())

    result = audit_external_worker.main([audit_cli._EXTERNAL_LIVE_MODE, str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 1
    assert report["verified"] is False
    assert report["reasons"][0]["code"] == "CHECK_INCOMPLETE"
    assert report["organizations"][0]["org_id"] == str(descriptor.organizations[0].org_id)
    assert report["organizations"][0]["witnesses"][0]["attempted"] is False
    assert "explicit-secret" not in captured.out + captured.err
    assert "sentinel-endpoint" not in captured.out + captured.err


def test_explicit_credential_failure_preserves_enrolled_report_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "trust.json"
    path.write_text("{}", encoding="utf-8")
    descriptor = _descriptor()
    monkeypatch.setattr(audit_cli, "load_trust_descriptor", lambda _path: descriptor)
    monkeypatch.setattr(
        audit_cli,
        "load_external_credentials",
        lambda _environ: (_ for _ in ()).throw(
            audit_cli.TrustConfigurationError("sentinel secret")
        ),
    )

    result = audit_cli.main(["verify-offhost", "--trust-descriptor", str(path)])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert result == 2
    assert report["descriptor_id"] == str(descriptor.descriptor_id)
    assert report["descriptor_sha256"] == descriptor.sha256
    assert report["organizations"][0]["present"] is None
    witness = report["organizations"][0]["witnesses"][0]
    assert witness["witness_id"] == str(descriptor.organizations[0].witnesses[0].witness_id)
    assert witness["attempted"] is False
    assert witness["status"] == "incomplete"
    assert witness["verified"] is False
    assert "sentinel" not in captured.out + captured.err


def test_legacy_no_option_output_and_dependencies_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def legacy() -> tuple[bool, int, list[tuple[str, list[str]]]]:
        return False, 1, [("org", ["legacy reason"])]

    monkeypatch.setattr(audit_cli, "_verify_offhost", legacy)
    monkeypatch.setattr(
        audit_cli,
        "load_trust_descriptor",
        lambda _path: (_ for _ in ()).throw(AssertionError("legacy path loaded descriptor")),
    )

    result = audit_cli.main(["verify-offhost"])

    assert result == 1
    assert capsys.readouterr().out == (
        "offhost_verified=False sinks_read=1\n  MISMATCH org=org: legacy reason\n"
    )
