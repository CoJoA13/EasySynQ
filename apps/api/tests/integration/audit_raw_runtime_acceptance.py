"""Owned actual-image acceptance for the exact raw retained-version transport.

This module deliberately does not start with ``test_``. The repository-owned runner invokes it
explicitly with the immutable application image that it built from the checked source manifest.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import io
import ipaddress
import json
import os
import tarfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID

from .audit_external_runtime_acceptance import _api_image as _api_image
from .audit_external_runtime_acceptance import _ApiImage
from .test_audit_witness_storage import (
    _mc as _mc,
)
from .test_audit_witness_storage import (
    _mc_exec,
    _policy_from_shipped_heredoc,
    _provision,
    _s3,
)

pytestmark = pytest.mark.integration

_IMAGE_PYTHON = "/app/.venv/bin/python"
_PROBE_IN_CONTAINER = "/run/audit-raw-runtime-probe.py"
_CONFIG_IN_CONTAINER = "/run/audit-raw-runtime-config.json"
_FIXTURE_IN_CONTAINER = "/run/audit-bootstrap-bridge-vectors.json"
_RUN_LABEL = "com.easysynq.audit-external.run"
_PROOF_PREFIX = "AUDIT_RAW_PROOF "


def _owned_parent() -> Path:
    record_text = os.environ.get("EASYSYNQ_ACCEPTANCE_RESOURCE_RECORD")
    assert record_text is not None
    parent = Path(record_text).parent
    root = Path(__file__).resolve().parents[4]
    assert parent.parent == root / ".pytest_cache"
    assert parent.name.startswith("audit-external-")
    return parent


def _owned_file(name: str, data: bytes, *, mode: int = 0o444) -> Path:
    path = _owned_parent() / f"{uuid.uuid4()}-{name}"
    path.write_bytes(data)
    path.chmod(mode)
    return path


def _config_file(value: dict[str, Any]) -> Path:
    return _owned_file(
        "raw-config.json",
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    )


def _probe_source() -> Path:
    path = Path(__file__).with_name("audit_raw_runtime_probe.py")
    assert path.is_file() and not path.is_symlink()
    return path


def _public_fixture() -> Path:
    path = Path(__file__).parents[1] / "fixtures" / "audit_bootstrap_bridge_vectors.json"
    assert path.is_file() and not path.is_symlink()
    return path


def _environment(entries: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries or []:
        name, separator, value = entry.partition("=")
        assert separator and name and name not in result
        result[name] = value
    return result


@contextmanager
def _probe_container(
    image: _ApiImage,
    config: Path,
    *,
    network_mode: str | None = None,
    extra_mounts: tuple[tuple[Path, str], ...] = (),
    environment: dict[str, str] | None = None,
) -> Iterator[DockerContainer]:
    run_id = os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]
    volumes = [
        (str(_probe_source()), _PROBE_IN_CONTAINER, "ro"),
        (str(config), _CONFIG_IN_CONTAINER, "ro"),
        *((str(source), target, "ro") for source, target in extra_mounts),
    ]
    container = DockerContainer(
        image.image_id,
        command=["sleep", "300"],
        volumes=volumes,
        user="10001:10001",
        read_only=True,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={_RUN_LABEL: run_id},
        **({"network_mode": network_mode} if network_mode is not None else {}),
    )
    for name, value in (environment or {}).items():
        container.with_env(name, value)
    with container:
        wrapped = container.get_wrapped_container()
        wrapped.reload()
        attrs = wrapped.attrs
        assert attrs["Image"] == image.image_id
        assert attrs["Config"]["User"] == "10001:10001"
        assert attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in attrs["HostConfig"]["SecurityOpt"]
        assert attrs["Config"]["Labels"][LABEL_SESSION_ID] == SESSION_ID
        assert attrs["Config"]["Labels"][_RUN_LABEL] == run_id
        expected = dict(image.environment)
        expected.update(environment or {})
        assert _environment(attrs["Config"].get("Env")) == expected
        assert all(not mount["RW"] for mount in attrs["Mounts"])
        yield container


def _exec_probe(
    container: DockerContainer, mode: str, forbidden: tuple[str, ...]
) -> dict[str, Any]:
    command = [
        _IMAGE_PYTHON,
        _PROBE_IN_CONTAINER,
        mode,
        "--config",
        _CONFIG_IN_CONTAINER,
    ]
    assert all(value not in command for value in forbidden)
    result = container.exec(ExecConfig(command=command, environment={}))
    decoded = result.output.decode("utf-8")
    assert result.exit_code == 0
    assert all(value not in decoded for value in forbidden)
    value = json.loads(decoded)
    assert type(value) is dict
    return value


def _proof(capsys: pytest.CaptureFixture[str], value: dict[str, Any]) -> None:
    with capsys.disabled():
        print(_PROOF_PREFIX + json.dumps(value, sort_keys=True, separators=(",", ":")))


def _provider_image_id(container_id: str) -> str:
    import docker

    client = docker.from_env()
    try:
        container = client.containers.get(container_id)
        container.reload()
        image_id = container.attrs["Image"]
        assert isinstance(image_id, str) and image_id.startswith("sha256:")
        assert len(image_id) == 71
        return image_id
    finally:
        client.close()


def _reader_policy(buckets: tuple[str, ...]) -> dict[str, Any]:
    policy = copy.deepcopy(_policy_from_shipped_heredoc("audit-sink-readonly.json"))
    assert len(policy["Statement"]) == 1
    policy["Statement"][0]["Resource"] = [
        resource
        for bucket in buckets
        for resource in (f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*")
    ]
    return policy


@contextmanager
def _runtime_reader(
    mc: DockerContainer,
    minio: dict[str, str],
    buckets: tuple[str, ...],
) -> Iterator[tuple[str, str]]:
    salt = uuid.uuid4().hex
    username = f"raw-read-{salt[:12]}"
    secret = f"synthetic-{uuid.uuid4().hex}"
    policy_name = f"raw-read-{salt[12:24]}"
    users: list[str] = []
    policies: list[str] = []
    try:
        _provision(
            mc,
            username=username,
            secret=secret,
            policy_name=policy_name,
            policy=_reader_policy(buckets),
            created_users=users,
            created_policies=policies,
        )
        yield username, secret
    finally:
        failures: list[Exception] = []
        for user in reversed(users):
            try:
                _mc_exec(mc, ["mc", "admin", "user", "rm", "local", user])
            except Exception as error:  # noqa: BLE001 - preserve cleanup across all owned resources
                failures.append(error)
        for policy in reversed(policies):
            try:
                _mc_exec(mc, ["mc", "admin", "policy", "rm", "local", policy])
            except Exception as error:  # noqa: BLE001 - preserve cleanup across all owned resources
                failures.append(error)
        if failures:
            raise failures[0]


def _create_bucket(admin: Any, *, locked: bool) -> str:
    bucket = f"raw-runtime-{uuid.uuid4().hex[:20]}"
    admin.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=locked)
    if not locked:
        return bucket
    admin.put_object_lock_configuration(
        Bucket=bucket,
        ObjectLockConfiguration={
            "ObjectLockEnabled": "Enabled",
            "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}},
        },
    )
    return bucket


def test_raw_version_runtime_preserves_exact_provider_bytes_and_bridge(
    _api_image: _ApiImage,
    _minio: dict[str, str],
    _mc: DockerContainer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = json.loads(_public_fixture().read_text(encoding="utf-8"))
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = _create_bucket(admin, locked=True)
    null_bucket = _create_bucket(admin, locked=False)
    denied_bucket = _create_bucket(admin, locked=True)
    org_id = "00000000-0000-4000-8000-000000000011"
    prefix = f"checkpoints/{org_id}/"
    old_key = prefix + "0000000010-retained.json"
    boundary_key = prefix + "0000000042-boundary.json"
    refs: dict[str, dict[str, str]] = {}
    provider_cases = (
        ("old", old_key),
        ("old_transport", old_key),
        ("boundary", boundary_key),
        ("unlisted_old", prefix + "9999999999-unlisted.json"),
        ("old_head_conflict", prefix + "9999999999-conflict.json"),
        ("above_boundary", prefix + "9999999999-ahead.json"),
    )
    for name, key in provider_cases:
        body = bytes.fromhex(fixture["legacy_vectors"][name]["body_hex"])
        response = admin.put_object(Bucket=bucket, Key=key, Body=body)
        refs[name] = {"key": key, "version_id": response["VersionId"]}
    marker_key = prefix + "0000000042-marker.json"
    admin.put_object(Bucket=bucket, Key=marker_key, Body=b"marker predecessor")
    marker = admin.delete_object(Bucket=bucket, Key=marker_key)
    assert marker["DeleteMarker"] is True
    null_key = "preexisting-null"
    admin.put_object(Bucket=null_bucket, Key=null_key, Body=b"literal null provider object")
    admin.put_bucket_versioning(Bucket=null_bucket, VersioningConfiguration={"Status": "Enabled"})
    denied_key = prefix + "0000000042-denied.json"
    denied = admin.put_object(Bucket=denied_bucket, Key=denied_key, Body=b"denied")
    config = _config_file(
        {
            "endpoint": "http://127.0.0.1:9000",
            "bucket": bucket,
            "region": "us-east-1",
            "prefix": prefix,
            "org_id": org_id,
            "stream_id": "00000000-0000-4000-8000-000000000012",
            "witness_a": "00000000-0000-4000-8000-000000000021",
            "witness_b": "00000000-0000-4000-8000-000000000022",
            "refs": refs,
            "marker_key": marker_key,
            "marker_version": marker["VersionId"],
            "null_bucket": null_bucket,
            "null_key": null_key,
            "denied_bucket": denied_bucket,
            "denied_key": denied_key,
            "denied_version": denied["VersionId"],
            "fixture": _FIXTURE_IN_CONTAINER,
            "access_env": "RAW_ACCESS_KEY",
            "secret_env": "RAW_SECRET_KEY",
        }
    )
    with _runtime_reader(_mc, _minio, (bucket, null_bucket)) as (access, secret):
        environment = {"RAW_ACCESS_KEY": access, "RAW_SECRET_KEY": secret}
        with _probe_container(
            _api_image,
            config,
            network_mode=f"container:{_minio['container_id']}",
            extra_mounts=((_public_fixture(), _FIXTURE_IN_CONTAINER),),
            environment=environment,
        ) as container:
            result = _exec_probe(container, "provider", (access, secret))
    admin.close()
    assert result["case_count"] == 21
    assert result["bridge"]["consistent"] == "consistent"
    assert result["bridge"]["required_witness"] == "incomplete"
    assert result["literal_null"] == "VERSION_MISMATCH"
    assert result["delete_marker"] == "PROVIDER_FAILURE"
    _proof(
        capsys,
        {
            "case": "provider-and-bridge",
            "application_image_id": _api_image.image_id,
            "provider_image_id": _provider_image_id(_minio["container_id"]),
            "case_count": result["case_count"],
            "bridge": result["bridge"],
            "literal_null": result["literal_null"],
            "delete_marker": result["delete_marker"],
            "missing_version": result["missing_version"],
            "cleanup_complete": True,
        },
    )


def _certificate_material() -> tuple[bytes, bytes, bytes]:
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EasySynQ Raw Test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    certificate = server.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(
        serialization.Encoding.PEM
    )
    private = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return ca.public_bytes(serialization.Encoding.PEM), certificate, private


def _archive_file(container: DockerContainer, path: str) -> bytes:
    stream, _metadata = container.get_wrapped_container().get_archive(path)
    archive = io.BytesIO(b"".join(stream))
    with tarfile.open(fileobj=archive, mode="r:") as bundle:
        members = [member for member in bundle.getmembers() if member.isfile()]
        assert len(members) == 1
        extracted = bundle.extractfile(members[0])
        assert extracted is not None
        return extracted.read()


def _hostile_environment(config: Path, credentials: Path) -> dict[str, str]:
    return {
        "RAW_ACCESS_KEY": "runtime-explicit-access",
        "RAW_SECRET_KEY": "runtime-explicit-secret",
        "AWS_ACCESS_KEY_ID": "poison-access",
        "AWS_SECRET_ACCESS_KEY": "poison-secret",
        "AWS_SESSION_TOKEN": "poison-token",
        "AWS_DEFAULT_REGION": "eu-north-1",
        "AWS_REGION": "eu-west-3",
        "AWS_RETRY_MODE": "adaptive",
        "AWS_MAX_ATTEMPTS": "9",
        "AWS_USE_FIPS_ENDPOINT": "true",
        "AWS_USE_DUALSTACK_ENDPOINT": "true",
        "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
        "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
        "AWS_ENDPOINT_URL": "https://fallback.invalid",
        "AWS_ENDPOINT_URL_S3": "https://s3-fallback.invalid",
        "AWS_PROFILE": "poison",
        "AWS_CONFIG_FILE": "/run/hostile-aws-config",
        "AWS_SHARED_CREDENTIALS_FILE": "/run/hostile-aws-credentials",
        "HTTP_PROXY": "http://proxy.invalid:1",
        "HTTPS_PROXY": "http://proxy.invalid:1",
        "AWS_CA_BUNDLE": "/run/missing-ambient-ca.pem",
        "REQUESTS_CA_BUNDLE": "/run/missing-requests-ca.pem",
        "SSL_CERT_FILE": "/run/missing-ssl-ca.pem",
        "S3_ENDPOINT": "https://application-fallback.invalid",
        "AUDIT_SINK_READ_ACCESS_KEY": "application-poison-access",
        "AUDIT_SINK_READ_SECRET_KEY": "application-poison-secret",
        "RAW_HOST_CONFIG": str(config),
        "RAW_HOST_CREDENTIALS": str(credentials),
    }


def test_raw_version_runtime_enforces_routing_and_tls(
    _api_image: _ApiImage,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = _config_file({})
    with _probe_container(_api_image, empty) as container:
        certifi_info = _exec_probe(container, "certifi", ())
        base_bundle = _archive_file(container, certifi_info["certifi_path"])
    assert hashlib.sha256(base_bundle).hexdigest() == certifi_info["certifi_sha256"]

    ca_pem, certificate_pem, private_key_pem = _certificate_material()
    certificate = _owned_file("server.pem", certificate_pem)
    private_key = _owned_file("server-key.pem", private_key_pem)
    combined = _owned_file("combined-ca.pem", base_bundle.rstrip() + b"\n" + ca_pem)
    hostile_config = _owned_file(
        "hostile-aws-config",
        b"[profile poison]\nregion=ap-south-1\ns3_endpoint_url=https://profile.invalid\n",
    )
    hostile_credentials = _owned_file(
        "hostile-aws-credentials",
        b"[poison]\naws_access_key_id=profile-poison\naws_secret_access_key=profile-poison\n",
    )
    environment = _hostile_environment(hostile_config, hostile_credentials)
    common_mounts = (
        (certificate, "/run/raw-server-cert.pem"),
        (private_key, "/run/raw-server-key.pem"),
        (hostile_config, "/run/hostile-aws-config"),
        (hostile_credentials, "/run/hostile-aws-credentials"),
    )
    untrusted_config = _config_file(
        {
            "certificate": "/run/raw-server-cert.pem",
            "private_key": "/run/raw-server-key.pem",
            "trusted": False,
        }
    )
    forbidden = (
        *tuple(
            environment[name]
            for name in (
                "RAW_ACCESS_KEY",
                "RAW_SECRET_KEY",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_SESSION_TOKEN",
                "AWS_ENDPOINT_URL",
                "AWS_ENDPOINT_URL_S3",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "S3_ENDPOINT",
                "AUDIT_SINK_READ_ACCESS_KEY",
                "AUDIT_SINK_READ_SECRET_KEY",
            )
        ),
        "profile-poison",
        "https://profile.invalid",
        "cached-default-poison-access",
        "cached-default-poison-secret",
        "cached-default-poison-token",
    )
    with _probe_container(
        _api_image,
        untrusted_config,
        extra_mounts=common_mounts,
        environment=environment,
    ) as container:
        untrusted = _exec_probe(container, "routing", forbidden)
    assert untrusted["tls"] == "untrusted-rejected"
    assert untrusted["handler_requests"] == 0
    default_session = {
        "populated": True,
        "distinct_credentials": True,
        "distinct_region": True,
        "restored": True,
    }
    assert untrusted["default_session"] == default_session

    trusted_config = _config_file(
        {
            "certificate": "/run/raw-server-cert.pem",
            "private_key": "/run/raw-server-key.pem",
            "trusted": True,
        }
    )
    with _probe_container(
        _api_image,
        trusted_config,
        extra_mounts=(*common_mounts, (combined, certifi_info["certifi_path"])),
        environment=environment,
    ) as container:
        trusted = _exec_probe(container, "routing", forbidden)
    effective_hash = hashlib.sha256(combined.read_bytes()).hexdigest()
    assert trusted["certifi_sha256"] == effective_hash
    assert trusted["opaque_target"] == "exact"
    assert trusted["redirect_target_count"] == 0
    assert trusted["redirect_original_counts"] == [1, 1]
    assert trusted["default_session"] == default_session
    _proof(
        capsys,
        {
            "case": "routing-and-tls",
            "application_image_id": _api_image.image_id,
            "case_count": untrusted["case_count"] + trusted["case_count"],
            "base_ca_sha256": certifi_info["certifi_sha256"],
            "effective_ca_sha256": effective_hash,
            "default_session": trusted["default_session"],
            "opaque_target": trusted["opaque_target"],
            "redirect_original_counts": trusted["redirect_original_counts"],
            "redirect_target_count": trusted["redirect_target_count"],
            "cleanup_complete": True,
        },
    )


def test_raw_version_runtime_bounds_streams_and_cleans_up(
    _api_image: _ApiImage,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config_file({})
    environment = {
        "RAW_ACCESS_KEY": "runtime-explicit-access",
        "RAW_SECRET_KEY": "runtime-explicit-secret",
    }
    with _probe_container(_api_image, config, environment=environment) as container:
        result = _exec_probe(container, "stream", tuple(environment.values()))
    assert result["case_count"] == 13
    assert result["cleanup_complete"] is True
    assert result["categories"] == [
        "bounds",
        "checksum",
        "content-encoding",
        "error-buffering",
        "framing",
        "read-timeout",
        "released-joined-cancellation",
    ]
    error_prefix = b"<Error><Code>NoSuchVersion</Code>"
    error_suffix = b"</Error>"
    error_body = (
        error_prefix + b"x" * (65_536 - len(error_prefix) - len(error_suffix)) + error_suffix
    )
    assert result["error_buffering"] == {
        "event": "before-parse.s3.GetObject",
        "body_bytes": 65_536,
        "body_sha256": hashlib.sha256(error_body).hexdigest(),
        "observed_before_api_return": True,
    }
    socket_timeout = result["socket_timeout"]
    assert socket_timeout["configured_read_timeout_seconds"] == 5
    assert socket_timeout["observation_boundary"] == "StreamingBody.read"
    assert socket_timeout["observed_exception"] == "ReadTimeoutError"
    assert socket_timeout["server_hold_seconds"] == 6.2
    assert type(socket_timeout["elapsed_ms"]) is int
    assert socket_timeout["elapsed_ms"] >= 4_000
    _proof(
        capsys,
        {
            "case": "streams-and-cancellation",
            "application_image_id": _api_image.image_id,
            "case_count": result["case_count"],
            "categories": result["categories"],
            "error_buffering": result["error_buffering"],
            "hidden_framing": result["hidden_framing"],
            "socket_timeout": socket_timeout,
            "cleanup_complete": result["cleanup_complete"],
        },
    )
