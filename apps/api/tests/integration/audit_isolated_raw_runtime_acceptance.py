"""Owned actual-image acceptance for the isolated exact-version reader.

This module deliberately does not start with ``test_``. The repository-owned runner invokes it
explicitly with the immutable application image that it built from the checked source manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID

from .audit_external_runtime_acceptance import _api_image as _api_image
from .audit_external_runtime_acceptance import _ApiImage
from .audit_raw_runtime_acceptance import (
    _archive_file,
    _certificate_material,
    _create_bucket,
    _environment,
    _owned_file,
    _owned_parent,
    _provider_image_id,
    _public_fixture,
    _runtime_reader,
    _s3,
)
from .test_audit_witness_storage import _mc as _mc

pytestmark = pytest.mark.integration

_IMAGE_PYTHON = "/app/.venv/bin/python"
_PROBE_IN_CONTAINER = "/run/audit-isolated-raw-runtime-probe.py"
_CONFIG_IN_CONTAINER = "/run/audit-isolated-raw-runtime-config.json"
_FIXTURE_IN_CONTAINER = "/run/audit-bootstrap-bridge-vectors.json"
_WRITABLE_IN_CONTAINER = "/run/audit-isolated-write"
_RUN_LABEL = "com.easysynq.audit-external.run"
_PROOF_PREFIX = "AUDIT_ISOLATED_RAW_PROOF "


def _probe_source() -> Path:
    path = Path(__file__).with_name("audit_isolated_raw_runtime_probe.py")
    assert path.is_file() and not path.is_symlink()
    return path


def _config_file(value: dict[str, Any]) -> Path:
    return _owned_file(
        "isolated-raw-config.json",
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    )


def _owned_writable() -> Path:
    path = _owned_parent() / f"{uuid.uuid4()}-isolated-write"
    path.mkdir(mode=0o777)
    path.chmod(0o777)
    assert path.is_dir() and not path.is_symlink()
    return path


@contextmanager
def _probe_container(
    image: _ApiImage,
    config: Path,
    *,
    network_mode: str | None = None,
    extra_mounts: tuple[tuple[Path, str], ...] = (),
    writable: Path | None = None,
    environment: dict[str, str] | None = None,
) -> Iterator[DockerContainer]:
    run_id = os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]
    volumes = [
        (str(_probe_source()), _PROBE_IN_CONTAINER, "ro"),
        (str(config), _CONFIG_IN_CONTAINER, "ro"),
        *((str(source), target, "ro") for source, target in extra_mounts),
    ]
    if writable is not None:
        volumes.append((str(writable), _WRITABLE_IN_CONTAINER, "rw"))
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
        expected_environment = dict(image.environment)
        expected_environment.update(environment or {})
        assert _environment(attrs["Config"].get("Env")) == expected_environment
        mounts = {mount["Destination"]: mount for mount in attrs["Mounts"]}
        assert mounts[_PROBE_IN_CONTAINER]["RW"] is False
        assert mounts[_CONFIG_IN_CONTAINER]["RW"] is False
        for _source, target in extra_mounts:
            assert mounts[target]["RW"] is False
        if writable is None:
            assert all(not mount["RW"] for mount in attrs["Mounts"])
        else:
            assert mounts[_WRITABLE_IN_CONTAINER]["RW"] is True
            assert all(
                not mount["RW"] or mount["Destination"] == _WRITABLE_IN_CONTAINER
                for mount in attrs["Mounts"]
            )
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


def _hostile_environment(access_key: str, secret_key: str) -> dict[str, str]:
    return {
        "ISOLATED_ACCESS_KEY": access_key,
        "ISOLATED_SECRET_KEY": secret_key,
        "AWS_ACCESS_KEY_ID": "isolated-poison-access",
        "AWS_SECRET_ACCESS_KEY": "isolated-poison-secret",
        "AWS_SESSION_TOKEN": "isolated-poison-token",
        "AWS_DEFAULT_REGION": "eu-north-1",
        "AWS_REGION": "eu-west-3",
        "AWS_RETRY_MODE": "adaptive",
        "AWS_MAX_ATTEMPTS": "9",
        "AWS_USE_FIPS_ENDPOINT": "true",
        "AWS_USE_DUALSTACK_ENDPOINT": "true",
        "AWS_ENDPOINT_URL": "https://isolated-fallback.invalid",
        "AWS_ENDPOINT_URL_S3": "https://isolated-s3-fallback.invalid",
        "HTTP_PROXY": "http://isolated-proxy.invalid:1",
        "HTTPS_PROXY": "http://isolated-proxy.invalid:1",
        "AWS_CA_BUNDLE": "/run/missing-isolated-ca.pem",
        "REQUESTS_CA_BUNDLE": "/run/missing-isolated-requests-ca.pem",
        "SSL_CERT_FILE": "/run/missing-isolated-ssl-ca.pem",
    }


def _assert_worker(value: dict[str, Any], *, zero: bool | None = None) -> None:
    assert value["reaped"] is True
    assert value["pipes_closed"] is True
    assert value["worker_environment_fixed"] is True
    assert value["worker_argv_private"] is True
    assert value["limits"] == {
        "address_space": [536_870_912, 536_870_912],
        "cpu_seconds": [10, 10],
        "file_descriptors": [64, 64],
        "file_bytes": [0, 0],
        "core_bytes": [0, 0],
    }
    assert 0 < value["maximum_rss_kib"] <= 524_288
    assert 0 < value["maximum_vms_kib"] <= 524_288
    assert type(value["elapsed_ms"]) is int and value["elapsed_ms"] >= 0
    if zero is True:
        assert value["exit_category"] == "zero"
    elif zero is False:
        assert value["exit_category"] != "zero"


def test_isolated_raw_runtime_enforces_process_and_byte_boundaries(
    _api_image: _ApiImage,
    _minio: dict[str, str],
    _mc: DockerContainer,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = _config_file({})
    with _probe_container(_api_image, empty) as container:
        certifi_info = _exec_probe(container, "certifi", ())
        base_bundle = _archive_file(container, certifi_info["certifi_path"])
    assert hashlib.sha256(base_bundle).hexdigest() == certifi_info["certifi_sha256"]

    trusted_ca, trusted_certificate, trusted_private = _certificate_material()
    _untrusted_ca, untrusted_certificate, untrusted_private = _certificate_material()
    trusted_certificate_file = _owned_file("isolated-trusted.pem", trusted_certificate)
    trusted_private_file = _owned_file("isolated-trusted-key.pem", trusted_private)
    untrusted_certificate_file = _owned_file("isolated-untrusted.pem", untrusted_certificate)
    untrusted_private_file = _owned_file("isolated-untrusted-key.pem", untrusted_private)
    combined = _owned_file("isolated-combined-ca.pem", base_bundle.rstrip() + b"\n" + trusted_ca)
    fixture = json.loads(_public_fixture().read_text(encoding="utf-8"))
    retained_body = bytes.fromhex(fixture["legacy_vectors"]["old_transport"]["body_hex"])
    admin = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    bucket = _create_bucket(admin, locked=True)
    key = "isolated/checkpoints/noncanonical-retained.json"
    uploaded = admin.put_object(Bucket=bucket, Key=key, Body=retained_body)
    version_id = uploaded["VersionId"]
    writable = _owned_writable()
    config = _config_file(
        {
            "fixture": _FIXTURE_IN_CONTAINER,
            "trusted_certificate": "/run/isolated-trusted.pem",
            "trusted_private_key": "/run/isolated-trusted-key.pem",
            "untrusted_certificate": "/run/isolated-untrusted.pem",
            "untrusted_private_key": "/run/isolated-untrusted-key.pem",
            "writable": _WRITABLE_IN_CONTAINER,
            "provider_endpoint": "http://127.0.0.1:9000",
            "provider_bucket": bucket,
            "provider_key": key,
            "provider_version_id": version_id,
        }
    )
    container_id = ""
    try:
        with _runtime_reader(_mc, _minio, (bucket,)) as (access_key, secret_key):
            environment = _hostile_environment(access_key, secret_key)
            forbidden = tuple(
                environment[name]
                for name in (
                    "ISOLATED_ACCESS_KEY",
                    "ISOLATED_SECRET_KEY",
                    "AWS_ACCESS_KEY_ID",
                    "AWS_SECRET_ACCESS_KEY",
                    "AWS_SESSION_TOKEN",
                    "AWS_ENDPOINT_URL",
                    "AWS_ENDPOINT_URL_S3",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "AWS_CA_BUNDLE",
                    "REQUESTS_CA_BUNDLE",
                    "SSL_CERT_FILE",
                )
            )
            with _probe_container(
                _api_image,
                config,
                network_mode=f"container:{_minio['container_id']}",
                extra_mounts=(
                    (_public_fixture(), _FIXTURE_IN_CONTAINER),
                    (trusted_certificate_file, "/run/isolated-trusted.pem"),
                    (trusted_private_file, "/run/isolated-trusted-key.pem"),
                    (untrusted_certificate_file, "/run/isolated-untrusted.pem"),
                    (untrusted_private_file, "/run/isolated-untrusted-key.pem"),
                    (combined, certifi_info["certifi_path"]),
                ),
                writable=writable,
                environment=environment,
            ) as container:
                container_id = container.get_wrapped_container().id
                result = _exec_probe(container, "all", forbidden)
    finally:
        admin.close()
        shutil.rmtree(writable)
    assert not writable.exists()
    assert result["uid"] == 10001
    assert result["cleanup_complete"] is True

    provider = result["provider"]
    _assert_worker(provider["process"], zero=True)
    assert provider["retained"]["exact_identity"] is True
    assert provider["retained"]["noncanonical"] is True
    assert provider["retained"]["intended_destination"] is True
    assert provider["retained"]["body_bytes"] == len(retained_body)
    assert provider["retained"]["body_sha256"] == hashlib.sha256(retained_body).hexdigest()
    assert provider["bridge"] == {"positive": "consistent", "altered": "failed"}

    routing = result["routing"]
    assert routing["trusted"] is True
    assert routing["untrusted_rejected"] is True
    assert routing["opaque_target_exact"] is True
    assert routing["redirect_target_requests"] == 0
    assert len(routing["processes"]) == 3
    for process in routing["processes"]:
        _assert_worker(process, zero=True)

    streamed = result["streamed"]
    assert streamed["planned_bytes"] == 536_936_448
    assert 0 < streamed["sent_bytes"] <= streamed["planned_bytes"]
    assert streamed["maximum_chunk_bytes"] <= 65_536
    assert streamed["outcome"] == "WORKER_FAILED"
    assert streamed["peer_close"] in {
        "BrokenPipeError",
        "ConnectionResetError",
        "PeerEOF",
    }
    _assert_worker(streamed["worker"], zero=False)
    _assert_worker(streamed["parent_survival_worker"], zero=True)

    deadlines = result["deadlines"]
    assert deadlines["sdk_read_timeout_seconds"] == 5
    assert deadlines["trickle_interval_ms"] == 3_500
    assert deadlines["trickle_bytes_sent"] >= 2
    assert deadlines["trickle_peer_close"] in {
        "BrokenPipeError",
        "ConnectionResetError",
        "PeerEOF",
    }
    assert deadlines["valid_result_observed_before_cancel"] is True
    _assert_worker(deadlines["watchdog"], zero=False)
    assert 19_000 <= deadlines["watchdog"]["elapsed_ms"] <= 24_000
    _assert_worker(deadlines["post_result_cancellation"], zero=False)
    assert deadlines["post_result_cancellation"]["elapsed_ms"] < 3_000

    limits = result["limits"]
    assert limits["effective"] == provider["process"]["limits"]
    assert limits["writable_mount_control"] is True
    assert limits["address_space"]["outcome"] == "MemoryError"
    assert limits["address_space"]["exit_category"] == "zero"
    assert limits["address_space"]["reaped"] is True
    assert limits["file_growth"]["errno"] == 27
    assert limits["file_growth"]["size"] == 0
    assert limits["file_growth"]["exit_category"] == "zero"
    assert limits["file_growth"]["reaped"] is True
    assert limits["cpu"]["exit_category"] in {"signal-9", "signal-24"}
    assert limits["cpu"]["elapsed_ms"] >= 8_000
    assert limits["cpu"]["reaped"] is True

    proof = {
        "case": "isolated-process-and-byte-boundaries",
        "application_image_id": _api_image.image_id,
        "provider_image_id": _provider_image_id(_minio["container_id"]),
        "probe_container_id": container_id,
        "uid": result["uid"],
        "process_boundary": {
            "real_child": True,
            "environment_fixed": provider["process"]["worker_environment_fixed"],
            "argv_private": provider["process"]["worker_argv_private"],
            "reaped": provider["process"]["reaped"],
        },
        "retained_bytes": provider["retained"],
        "r78_controls": provider["bridge"],
        "tls_routing": {
            "trusted": routing["trusted"],
            "untrusted_rejected": routing["untrusted_rejected"],
            "opaque_target_exact": routing["opaque_target_exact"],
            "redirect_target_requests": routing["redirect_target_requests"],
        },
        "streamed_error": {
            key: streamed[key]
            for key in (
                "planned_bytes",
                "sent_bytes",
                "maximum_chunk_bytes",
                "outcome",
                "peer_close",
            )
        }
        | {
            "worker_resources": streamed["worker"],
            "survivor": streamed["parent_survival_worker"],
        },
        "deadlines": {
            "sdk_read_timeout_seconds": deadlines["sdk_read_timeout_seconds"],
            "trickle_interval_ms": deadlines["trickle_interval_ms"],
            "watchdog_elapsed_ms": deadlines["watchdog"]["elapsed_ms"],
            "watchdog_outcome": "DEADLINE_EXCEEDED",
            "post_result_cancel_elapsed_ms": deadlines["post_result_cancellation"]["elapsed_ms"],
            "post_result_cancelled": True,
        },
        "resource_limits": limits,
        "cleanup_complete": result["cleanup_complete"] and not writable.exists(),
    }
    with capsys.disabled():
        print(_PROOF_PREFIX + json.dumps(proof, sort_keys=True, separators=(",", ":")))
