"""Owned immutable-image acceptance for the original version-page transport.

The required runner invokes this module explicitly. Provider observations and hostile
finite responses are separate proof components; neither can substitute for the other.
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.docker_client import DockerClient
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
    _runtime_reader,
)

_PYTHON = "/app/.venv/bin/python"
_PROBE = "/run/audit-version-page-transport-runtime-probe.py"
_CONFIG = "/run/audit-version-page-transport-runtime-config.json"
_WRITABLE = "/run/audit-version-page-transport-write"
_RUN_LABEL = "com.easysynq.audit-external.run"
_PROOF_PREFIX = "AUDIT_VERSION_PAGE_TRANSPORT_PROOF "
_FIXTURE_LABEL = "com.easysynq.audit-page-fixture"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BODY_OUTCOMES = {
    "duplicates-markers-opaque-labels": "accepted",
    "key-only-cursor": "accepted",
    "two-part-cursor": "accepted",
    "control-null-cursor": "accepted",
    "next-two-part-cursor": "accepted",
    "malformed-200": "RESPONSE_INVALID",
    "coercible-truncation": "RESPONSE_INVALID",
    "coercible-latest": "RESPONSE_INVALID",
    "wrong-namespace": "RESPONSE_INVALID",
    "wrong-scope": "SCOPE_MISMATCH",
    "thousand-observations": "accepted",
    "thousand-one-observations": "PAGE_LIMIT",
    "exact-body-boundary": "accepted",
    "body-boundary-one-over": "BODY_LIMIT",
}


@contextmanager
def _probe_container(
    image: _ApiImage,
    config: Path,
    *,
    mounts: tuple[tuple[Path, str], ...] = (),
    network_mode: str = "none",
    writable: Path | None = None,
) -> Iterator[DockerContainer]:
    source = Path(__file__).with_name("audit_version_page_transport_runtime_probe.py")
    assert source.is_file() and not source.is_symlink()
    container = DockerContainer(
        image.image_id,
        command=["sleep", "300"],
        volumes=[
            (str(source), _PROBE, "ro"),
            (str(config), _CONFIG, "ro"),
            *((str(path), target, "ro") for path, target in mounts),
            *([] if writable is None else [(str(writable), _WRITABLE, "rw")]),
        ],
        user="10001:10001",
        read_only=True,
        network_mode=network_mode,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={_RUN_LABEL: os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]},
    )
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
        assert attrs["Config"]["Labels"][_RUN_LABEL] == os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]
        assert _environment(attrs["Config"].get("Env")) == image.environment
        assert all(
            not mount["RW"] or mount["Destination"] == _WRITABLE for mount in attrs["Mounts"]
        )
        if writable is not None:
            assert any(
                mount["RW"] and mount["Destination"] == _WRITABLE for mount in attrs["Mounts"]
            )
        yield container


def _config(value: dict[str, Any]) -> Path:
    return _owned_file("page-transport-config.json", json.dumps(value).encode())


def _exec(container: DockerContainer, mode: str, secrets: tuple[str, ...]) -> dict[str, Any]:
    command = [_PYTHON, _PROBE, mode, "--config", _CONFIG]
    assert all(secret not in argument for secret in secrets for argument in command)
    result = container.exec(ExecConfig(command=command, environment={}))
    output = result.output.decode("utf-8")
    if any(secret in output for secret in secrets):
        raise AssertionError("runtime probe output contains a forbidden value")
    if result.exit_code != 0:
        tail = output.encode("utf-8")[-8192:].decode("utf-8", errors="ignore")
        raise AssertionError(f"runtime probe failed:\n{tail}")
    value = json.loads(output)
    assert type(value) is dict
    return value


def _bundle(image: _ApiImage) -> tuple[bytes, str]:
    config = _config({})
    try:
        with _probe_container(image, config) as container:
            result = _exec(container, "certifi", ())
            bundle = _archive_file(container, result["certifi_path"])
    finally:
        config.unlink()
    assert hashlib.sha256(bundle).hexdigest() == result["certifi_sha256"]
    return bundle, result["certifi_path"]


def _assert_worker(worker: dict[str, Any]) -> None:
    assert worker["limits"] == {
        "address_space": [536_870_912, 536_870_912],
        "cpu_seconds": [20, 20],
        "file_descriptors": [64, 64],
        "file_bytes": [0, 0],
        "core_bytes": [0, 0],
    }
    assert 0 < worker["maximum_rss_kib"] <= 524_288
    assert 0 < worker["maximum_vms_kib"] <= 524_288
    assert type(worker["elapsed_ms"]) is int and worker["elapsed_ms"] >= 0
    for field in (
        "reaped",
        "pipes_closed",
        "selector_closed",
        "worker_environment_fixed",
        "worker_argv_private",
    ):
        assert worker[field] is True


def _synthetic_acceptance(image: _ApiImage) -> dict[str, Any]:
    base_bundle, bundle_path = _bundle(image)
    trusted_ca, trusted_certificate, trusted_private = _certificate_material()
    _untrusted_ca, untrusted_certificate, untrusted_private = _certificate_material()
    mounts = (
        (_owned_file("page-trusted.pem", trusted_certificate), "/run/page-trusted.pem"),
        (_owned_file("page-trusted-key.pem", trusted_private), "/run/page-trusted-key.pem"),
        (_owned_file("page-untrusted.pem", untrusted_certificate), "/run/page-untrusted.pem"),
        (_owned_file("page-untrusted-key.pem", untrusted_private), "/run/page-untrusted-key.pem"),
        (_owned_file("page-ca-bundle.pem", base_bundle.rstrip() + b"\n" + trusted_ca), bundle_path),
    )
    access = "synthetic-page-access"
    secret = "synthetic-page-secret"
    config = _config(
        {
            "access_key": access,
            "secret_key": secret,
            "trusted_certificate": "/run/page-trusted.pem",
            "trusted_private_key": "/run/page-trusted-key.pem",
            "untrusted_certificate": "/run/page-untrusted.pem",
            "untrusted_private_key": "/run/page-untrusted-key.pem",
            "writable": _WRITABLE,
        }
    )
    writable = _owned_parent() / f"{uuid.uuid4()}-page-write"
    writable.mkdir(mode=0o777)
    writable.chmod(0o777)
    try:
        with _probe_container(image, config, mounts=mounts, writable=writable) as container:
            result = _exec(container, "synthetic", (access, secret))
        assert list(writable.iterdir()) == []
    finally:
        shutil.rmtree(writable)
        config.unlink()
        for source, _target in mounts:
            source.unlink()
    assert not writable.exists()
    assert result["uid"] == 10001
    assert result["dev_packages_absent"] == ["mypy", "pytest", "ruff"]
    assert set(result["runtime_versions"]) == {"python", "boto3", "botocore", "expat"}
    assert all(type(value) is str and value for value in result["runtime_versions"].values())
    assert _SHA256.fullmatch(result["input_vectors_sha256"])
    assert set(result["cases"]) == set(_BODY_OUTCOMES)
    for name, expected in _BODY_OUTCOMES.items():
        case = result["cases"][name]
        assert case["outcome"] == expected and case["physical_sends"] == 1
        assert _SHA256.fullmatch(case["body_sha256"])
        assert case["fixture_closed"] is True
        _assert_worker(case["worker"])
        assert case["worker"]["exit_code"] == 0
    assert result["cases"]["exact-body-boundary"]["body_bytes"] == 16_777_216
    assert result["cases"]["body-boundary-one-over"]["body_bytes"] == 16_777_217
    assert set(result["routing"]) == {
        "trusted-tls",
        "untrusted-tls",
        "wrong-host-tls",
        "redirect",
        "deny",
    }
    for name, case in result["routing"].items():
        _assert_worker(case["worker"])
        assert case["fixture_closed"] is True
        assert case["outcome"] == (
            "accepted"
            if name == "trusted-tls"
            else "PROVIDER_FAILURE"
            if name in {"redirect", "deny"}
            else "TRANSPORT_FAILURE"
        )
        if name in {"redirect", "deny"}:
            assert case["physical_sends"] == 1 and case["second_target_requests"] == 0
        else:
            assert case["http_requests"] == (1 if name == "trusted-tls" else 0)
    assert result["late_hook_control"] == {
        "outcome": "PROVIDER_FAILURE",
        "required_outcome": "RESPONSE_INVALID",
        "contract_would_fail": True,
        "physical_sends": 1,
        "fixture_closed": True,
    }
    outcomes = {
        "oversized-buffered": "WORKER_FAILED",
        "blocked": "TRANSPORT_FAILURE",
        "blocked-cancel": "cancelled",
        "trickle": "DEADLINE_EXCEEDED",
        "parent-survives": "accepted",
    }
    assert set(result["containment"]) == set(outcomes)
    for name, expected in outcomes.items():
        case = result["containment"][name]
        assert case["outcome"] == expected and case["physical_sends"] == 1
        assert case["fixture_closed"] is True
        _assert_worker(case["worker"])
    buffered = result["containment"]["oversized-buffered"]
    assert buffered["planned_bytes"] == 603_979_776
    assert 0 < buffered["sent_bytes"] <= buffered["planned_bytes"]
    assert buffered["maximum_chunk_bytes"] <= 65_536
    assert buffered["peer_disconnected"] is True
    assert buffered["worker"]["exit_code"] != 0
    ipc_outcomes = {
        "valid-result": "accepted",
        "bad-magic": "PROTOCOL_INVALID",
        "unknown-error": "PROTOCOL_INVALID",
        "oversized-frame": "OUTPUT_LIMIT",
        "truncated-frame": "PROTOCOL_INVALID",
        "extra-frame": "PROTOCOL_INVALID",
        "post-result-failure": "WORKER_FAILED",
        "withheld-eof": "DEADLINE_EXCEEDED",
        "withheld-exit": "cancelled",
    }
    assert set(result["ipc"]) == set(ipc_outcomes)
    for name, expected in ipc_outcomes.items():
        case = result["ipc"][name]
        assert case["outcome"] == expected
        _assert_worker(case["worker"])
        assert case["worker"]["spawn_kind"] == "adversarial-ipc-producer"
    for name in ("valid-result", "post-result-failure", "withheld-eof", "withheld-exit"):
        assert result["ipc"][name]["valid_result_frame_seen"] is True
    assert result["ipc"]["withheld-exit"]["worker"]["stdout_eof_observed"] is True
    assert result["ipc"]["withheld-eof"]["worker"]["stdout_eof_observed"] is False
    for section in ("cases", "routing", "containment"):
        for case in result[section].values():
            assert case["worker"]["spawn_kind"] == "production-worker"
    resources = result["resources"]
    assert resources["effective"] == buffered["worker"]["limits"]
    assert resources["writable_control"] is True
    assert resources["installed_worker_limit_function"] is True
    assert resources["cleanup_complete"] is True
    assert set(resources["cases"]) == {"memory", "file", "descriptors", "cpu"}
    for case in resources["cases"].values():
        assert case["reaped"] is True and case["pipes_closed"] is True
    assert resources["cases"]["memory"]["outcome"] == {"memory": "MemoryError"}
    assert resources["cases"]["file"]["outcome"] == {"file_errno": 27, "bytes": 0}
    assert resources["cases"]["descriptors"]["outcome"]["descriptor_errno"] == 24
    assert resources["cases"]["cpu"]["exit_code"] in {-9, -24}
    assert 19000 <= resources["cases"]["cpu"]["child_cpu_ms"] <= 22000
    assert result["cleanup_complete"] is True
    return result


class _TlsMc(DockerContainer):
    """Keep existing account provisioning/cleanup helpers on the verified TLS alias."""

    def copy_into_container(self, source: bytes, destination: str, mode: int = 0o644) -> None:
        assert type(source) is bytes and len(source) <= 65_536 and b"\0" not in source
        text = source.decode("utf-8", errors="strict")
        assert type(destination) is str
        assert re.fullmatch(
            r"/tmp/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\.json",  # noqa: S108 - owned container tmpfs
            destination,
        )
        assert type(mode) is int and mode == 0o644
        # Docker's archive API rejects this read-only helper, including its tmpfs target.
        # The fixed script writes only the validated policy path; data stays positional.
        result = self.get_wrapped_container().exec_run(
            [
                "/bin/sh",
                "-c",
                'umask 022; printf %s "$1" > "$2"',
                "page-policy-copy",
                text,
                destination,
            ]
        )
        assert result.exit_code == 0

    def exec(self, command: Any) -> Any:
        assert type(command) is list and command[0] == "mc"
        return super().exec(
            [
                "mc",
                "--config-dir",
                "/tmp/mc",  # noqa: S108 - private container tmpfs config
                *command[1:],
            ]
        )


def _pinned_images(client: Any) -> dict[str, str]:
    root = Path(__file__).resolve().parents[4]
    references = {}
    for line in (root / "infra/images.lock").read_text().splitlines():
        fields = line.split()
        if fields and fields[0] in {"minio", "mc"}:
            references[fields[0]] = fields[1]
    assert set(references) == {"minio", "mc"}
    identities = {}
    for name, reference in references.items():
        image = client.images.get(reference)
        assert reference.split("@", 1)[1] in {
            value.split("@", 1)[1] for value in image.attrs["RepoDigests"]
        }
        assert image.id.startswith("sha256:") and len(image.id) == 71
        identities[name] = image.id
    return identities


def _trace_snapshot(trace: Any) -> tuple[list[dict[str, Any]], str]:
    raw = trace.logs(stdout=True, stderr=True)
    assert len(raw) <= 16_777_216
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder()
    events = []
    offset = 0
    while True:
        while offset < len(text) and text[offset] in " \t\r\n":
            offset += 1
        if offset == len(text):
            return events, ""
        try:
            value, end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            # Docker snapshots may end in the middle of a pretty-printed object.
            # Callers retry under a deadline and require an empty final remainder.
            return events, text[offset:]
        assert type(value) is dict
        events.append(value)
        offset = end


def _provider_address(host: str) -> tuple[str, str]:
    """Return the exact TLS host and a daemon-side publish address reachable from it."""
    assert type(host) is str and host and "%" not in host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        name = host.encode("idna").decode("ascii").lower()
        assert len(name) <= 253 and re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
            name,
        )
        # A remote Docker service must publish beyond its own loopback namespace.
        binding = "127.0.0.1" if name == "localhost" else "0.0.0.0"  # noqa: S104
        return name, binding
    if address.is_loopback:
        binding = str(address)
    else:
        binding = "::" if address.version == 6 else "0.0.0.0"  # noqa: S104 - remote fixture
    return str(address), binding


def _provider_endpoint(host: str, port: str) -> str:
    host, _binding = _provider_address(host)
    assert type(port) is str and port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535
    authority = f"[{host}]" if ":" in host else host
    return f"https://{authority}:{port}"


def _provider_certificate_material(host: str) -> tuple[bytes, bytes, bytes]:
    """Issue this provider's certificate for both external admin and namespace-local readers."""
    host, _binding = _provider_address(host)
    names: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    try:
        identity: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        identity = x509.DNSName(host)
    if identity not in names:
        names.append(identity)
    now = datetime.datetime.now(datetime.UTC)
    ca_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "EasySynQ Page Test CA")])
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
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_bytes = ca.public_bytes(serialization.Encoding.PEM)
    certificate = server.public_bytes(serialization.Encoding.PEM) + ca_bytes
    private = server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return ca_bytes, certificate, private


def _provider_client(endpoint: str, access: str, secret: str, ca: Path) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
        verify=str(ca),
        config=Config(
            signature_version="s3v4",
            connect_timeout=1,
            read_timeout=2,
            retries={"total_max_attempts": 1},
            proxies={},
            s3={"addressing_style": "path"},
        ),
    )


def _seed_provider(
    admin: Any,
    bucket: str,
    org: str,
) -> tuple[list[list[str]], list[dict[str, str]]]:
    expected = []
    created = []
    for index in range(502):
        key = f"checkpoints/{org}/{index:04d}-percent%2F+slash/雪"
        for revision in range(2):
            response = admin.put_object(Bucket=bucket, Key=key, Body=f"row-{revision}".encode())
            version = response["VersionId"]
            assert type(version) is str and version
            expected.append(["version", key, version])
            created.append({"Key": key, "VersionId": version})
        if index in {0, 250, 501}:
            response = admin.delete_object(Bucket=bucket, Key=key)
            assert response["DeleteMarker"] is True
            version = response["VersionId"]
            assert type(version) is str and version
            expected.append(["delete_marker", key, version])
            created.append({"Key": key, "VersionId": version})
    for key in (
        "outside-checkpoints/percent%+slash/雪",
        "checkpoints/22222222-2222-4222-8222-222222222222/foreign%+slash/雪",
    ):
        response = admin.put_object(Bucket=bucket, Key=key, Body=b"excluded namespace")
        created.append({"Key": key, "VersionId": response["VersionId"]})
    assert len(expected) == 1007 and len(created) == 1009
    return expected, created


def _assert_provider_pages(
    result: dict[str, Any],
    events: list[dict[str, Any]],
    expected: list[list[str]],
    bucket: str,
    org: str,
    reader_access: str,
) -> list[dict[str, Any]]:
    assert result["uid"] == 10001
    assert result["dev_packages_absent"] == ["mypy", "pytest", "ruff"]
    assert result["observation_count"] == 1007 and result["delete_markers"] == 3
    assert result["exact_expected_multiset"] is True and result["cleanup_complete"] is True
    listing = [event for event in events if event.get("api") == "s3.ListObjectVersions"]
    assert len(listing) == len(result["pages"]) == 2
    observed = [tuple(row) for page in result["pages"] for row in page["observations"]]
    assert Counter(observed) == Counter(tuple(row) for row in expected)
    serialized = json.dumps(sorted(expected), ensure_ascii=True, separators=(",", ":")).encode()
    assert result["observations_sha256"] == hashlib.sha256(serialized).hexdigest()
    pages = []
    key = version = None
    for index, (event, page) in enumerate(zip(listing, result["pages"], strict=True)):
        query = f"versions&prefix={quote(f'checkpoints/{org}/', safe='-_.~')}"
        query += "&max-keys=1000&encoding-type=url"
        if key is not None:
            query += "&key-marker=" + quote(key, safe="-_.~")
        if version is not None:
            query += "&version-id-marker=" + quote(version, safe="-_.~")
        assert page["key_marker"] == key and page["version_id_marker"] == version
        request = event["request"]
        assert request["method"] == "GET" and request["path"] == f"/{bucket}"
        assert request["rawQuery"] == query
        assert request["headers"]["Host"] == "127.0.0.1:9000"
        authorization = request["headers"]["Authorization"]
        assert authorization.startswith("AWS4-HMAC-SHA256 ")
        assert f"Credential={reader_access}/" in authorization
        assert "/us-east-1/s3/aws4_request" in authorization
        response = event["response"]
        assert response["statusCode"] == 200
        original = response["body"].encode("utf-8")
        assert len(original) == page["body_bytes"] == int(response["headers"]["Content-Length"])
        assert hashlib.sha256(original).hexdigest() == page["body_sha256"]
        _assert_worker(page["worker"])
        assert page["worker"]["spawn_kind"] == "production-worker"
        assert page["worker"]["exit_code"] == 0 and page["worker"]["stdout_eof_observed"] is True
        assert page["truncated"] is (index == 0)
        assert len(page["observations"]) == (1000 if index == 0 else 7)
        if index == 0:
            assert type(page["next_key_marker"]) is str
            assert type(page["next_version_id_marker"]) is str
        else:
            assert page["next_key_marker"] is page["next_version_id_marker"] is None
        key, version = page["next_key_marker"], page["next_version_id_marker"]
        pages.append(
            {field: value for field, value in page.items() if field != "observations"}
            | {
                "observation_count": len(page["observations"]),
                "physical_sends": 1,
                "original_trace_body_equal": True,
                "exact_request": True,
            }
        )
    return pages


def _provider_acceptance(image: _ApiImage) -> dict[str, Any]:
    base_bundle, bundle_path = _bundle(image)
    fixture_id = uuid.uuid4().hex
    material = _owned_parent() / f"{fixture_id}-page-provider"
    material.mkdir(mode=0o700)
    material.chmod(0o700)
    labels = {
        _RUN_LABEL: os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"],
        LABEL_SESSION_ID: SESSION_ID,
        _FIXTURE_LABEL: fixture_id,
    }
    # The resolver owns this SDK client; the existing finally closes it after all cleanup.
    resolver = DockerClient(timeout=15)
    client = resolver.client
    admin = None
    containers = []
    created: list[dict[str, str]] = []
    bucket = None
    outcome = None
    try:
        identities = _pinned_images(client)
        host, publish_address = _provider_address(resolver.host())
        ca, certificate, private = _provider_certificate_material(host)
        certs = material / "certs"
        certs.mkdir(mode=0o755)
        certs.chmod(0o755)

        def material_file(path: Path, data: bytes) -> Path:
            path.write_bytes(data)
            path.chmod(0o444)
            return path

        material_file(certs / "public.crt", certificate)
        material_file(certs / "private.key", private)
        ca_file = material_file(material / "ca.pem", ca)
        combined = material_file(material / "combined.pem", base_bundle.rstrip() + b"\n" + ca)
        root_access = "synthetic-admin-" + uuid.uuid4().hex[:12]
        root_secret = "synthetic-" + uuid.uuid4().hex
        provider = client.containers.create(
            identities["minio"],
            command=["server", "--certs-dir", "/certs", "/data"],
            environment={
                "MINIO_ROOT_USER": root_access,
                "MINIO_ROOT_PASSWORD": root_secret,
                "MINIO_BROWSER": "off",
            },
            ports={"9000/tcp": (publish_address, None)},
            volumes={str(certs): {"bind": "/certs", "mode": "ro"}},
            tmpfs={
                "/data": "rw,size=134217728,mode=0700",
                "/tmp": "rw,size=8388608,mode=1777",  # noqa: S108 - owned container tmpfs
            },
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels=labels,
        )
        containers.append(provider)
        provider.start()
        provider.reload()
        assert provider.attrs["Image"] == identities["minio"]
        assert provider.attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert provider.attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert all(
            not mount["RW"]
            or (
                mount["Type"] == "tmpfs"
                and mount["Destination"]
                in {
                    "/data",
                    "/tmp",  # noqa: S108 - verify the owned container tmpfs
                }
            )
            for mount in provider.attrs["Mounts"]
        )
        assert any(
            mount["Destination"] == "/certs" and not mount["RW"]
            for mount in provider.attrs["Mounts"]
        )
        port = provider.attrs["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"]
        endpoint = _provider_endpoint(host, port)
        admin = _provider_client(endpoint, root_access, root_secret, ca_file)
        deadline = time.monotonic() + 25
        while True:
            try:
                admin.list_buckets()
                break
            except (BotoCoreError, ClientError):
                provider.reload()
                assert provider.status == "running" and time.monotonic() < deadline
                time.sleep(0.2)
        bucket = _create_bucket(admin, locked=False)
        admin.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        org = "11111111-1111-4111-8111-111111111111"
        expected, created = _seed_provider(admin, bucket, org)
        mc_config = material_file(
            material / "mc-config.json",
            json.dumps(
                {
                    "version": "10",
                    "aliases": {
                        "local": {
                            "url": "https://127.0.0.1:9000",
                            "accessKey": root_access,
                            "secretKey": root_secret,
                            "api": "S3v4",
                            "path": "on",
                        }
                    },
                }
            ).encode(),
        )
        mc_mounts = [
            (
                str(mc_config),
                "/tmp/mc/config.json",  # noqa: S108 - read-only private container config
                "ro",
            ),
            (
                str(ca_file),
                "/tmp/mc/certs/CAs/ca.pem",  # noqa: S108 - read-only container CA mount
                "ro",
            ),
        ]
        with _TlsMc(
            identities["mc"],
            command=["-c", "sleep 300"],
            entrypoint="/bin/sh",
            network_mode="container:" + provider.id,
            volumes=mc_mounts,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels={name: value for name, value in labels.items() if name != LABEL_SESSION_ID},
        ).with_tmpfs_mount(
            "/tmp",  # noqa: S108 - owned helper container tmpfs
            "rw,size=8388608,mode=1777",
        ) as helper:
            helper_image = helper.get_wrapped_container().attrs["Image"]
            assert helper_image == identities["mc"]
            helper_labels = helper.get_wrapped_container().attrs["Config"]["Labels"]
            assert helper_labels[LABEL_SESSION_ID] == SESSION_ID
            assert helper_labels[_RUN_LABEL] == labels[_RUN_LABEL]
            assert helper_labels[_FIXTURE_LABEL] == fixture_id
            with _runtime_reader(helper, {}, (bucket,)) as (reader_access, reader_secret):
                assert reader_access != root_access and reader_secret != root_secret
                reader = _provider_client(endpoint, reader_access, reader_secret, ca_file)
                try:
                    with pytest.raises(ClientError) as denied:
                        reader.put_object(
                            Bucket=bucket,
                            Key=f"checkpoints/{org}/denied",
                            Body=b"no",
                        )
                    assert denied.value.response["Error"]["Code"] == "AccessDenied"
                finally:
                    reader.close()
                trace = client.containers.create(
                    identities["mc"],
                    entrypoint=["mc"],
                    command=[
                        "--config-dir",
                        "/tmp/mc",  # noqa: S108 - private container tmpfs config
                        "--json",
                        "--no-color",
                        "--disable-pager",
                        "admin",
                        "trace",
                        "--verbose",
                        "--call",
                        "s3",
                        "local",
                    ],
                    network_mode="container:" + provider.id,
                    volumes={
                        source: {"bind": target, "mode": mode} for source, target, mode in mc_mounts
                    },
                    tmpfs={
                        "/tmp": "rw,size=8388608,mode=1777",  # noqa: S108 - owned container tmpfs
                    },
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    labels=labels,
                )
                containers.append(trace)
                trace.start()
                deadline = time.monotonic() + 15
                while True:
                    admin.list_buckets()
                    events, _pending = _trace_snapshot(trace)
                    if any(event.get("api") == "s3.ListBuckets" for event in events):
                        break
                    trace.reload()
                    assert trace.status == "running" and time.monotonic() < deadline
                    time.sleep(0.2)
                config = material_file(
                    material / "probe-config.json",
                    json.dumps(
                        {
                            "endpoint": "https://127.0.0.1:9000",
                            "bucket": bucket,
                            "org_id": org,
                            "access_key": reader_access,
                            "secret_key": reader_secret,
                            "expected": expected,
                        }
                    ).encode(),
                )
                with _probe_container(
                    image,
                    config,
                    mounts=((combined, bundle_path),),
                    network_mode="container:" + provider.id,
                ) as probe:
                    result = _exec(
                        probe, "provider", (root_access, root_secret, reader_access, reader_secret)
                    )
                barrier = admin.list_buckets()["ResponseMetadata"]["RequestId"]
                deadline = time.monotonic() + 10
                while True:
                    events, pending = _trace_snapshot(trace)
                    observed_barrier = any(
                        event.get("api") == "s3.ListBuckets"
                        and event.get("response", {}).get("headers", {}).get("X-Amz-Request-Id")
                        == barrier
                        for event in events
                    )
                    if observed_barrier and not pending:
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                trace.reload()
                assert trace.status == "running" and trace.attrs["Image"] == identities["mc"]
                trace.stop(timeout=5)
                trace.reload()
                assert trace.status == "exited"
                events, pending = _trace_snapshot(trace)
                assert pending == ""
                pages = _assert_provider_pages(result, events, expected, bucket, org, reader_access)
                outcome = {
                    "case": "genuine-provider-tls-pagination",
                    "provider_image_id": identities["minio"],
                    "trace_image_id": identities["mc"],
                    "uid": result["uid"],
                    "runtime_versions": result["runtime_versions"],
                    "dev_packages_absent": result["dev_packages_absent"],
                    "observation_count": 1007,
                    "delete_markers": 3,
                    "expected_multiset_sha256": result["observations_sha256"],
                    "exact_expected_multiset": True,
                    "excluded_namespace_objects": 2,
                    "configured_tls": True,
                    "distinct_readonly_reader": True,
                    "physical_sends": len(pages),
                    "pages": pages,
                    "trace_drained": True,
                    "trace_process_stopped": True,
                }
    finally:
        failures = []
        if admin is not None:
            try:
                if bucket is not None:
                    for reference in created:
                        admin.delete_object(Bucket=bucket, **reference)
                    admin.delete_bucket(Bucket=bucket)
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup
                failures.append(error)
            finally:
                try:
                    admin.close()
                except Exception as error:  # noqa: BLE001 - preserve cleanup failure
                    failures.append(error)
        for container in reversed(containers):
            try:
                container.remove(force=True, v=True)
            except Exception as error:  # noqa: BLE001 - attempt every owned cleanup
                failures.append(error)
        try:
            remaining = client.containers.list(
                all=True,
                filters={"label": _FIXTURE_LABEL + "=" + fixture_id},
            )
            assert remaining == []
        except Exception as error:  # noqa: BLE001 - verify cleanup independently
            failures.append(error)
        finally:
            client.close()
            shutil.rmtree(material)
        if failures:
            raise ExceptionGroup("provider fixture cleanup failed", failures)
    assert outcome is not None and not material.exists()
    return outcome | {
        "cleanup_complete": True,
        "owned_containers_remaining": 0,
        "material_removed": True,
        "admin_closed": True,
        "reader_identity_removed": True,
        "versioned_bucket_removed": True,
    }


def test_version_page_transport_runtime_preserves_original_observations_and_limits(
    _api_image: _ApiImage,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider = _provider_acceptance(_api_image)
    synthetic = _synthetic_acceptance(_api_image)
    assert provider["runtime_versions"] == synthetic["runtime_versions"]
    assert provider["cleanup_complete"] is synthetic["cleanup_complete"] is True
    proof = {
        "case": "original-version-page-transport",
        "application_image_id": _api_image.image_id,
        "provider": provider,
        "synthetic": synthetic,
        "uid": 10001,
        "cleanup_complete": True,
    }
    with capsys.disabled():
        print(_PROOF_PREFIX + json.dumps(proof, sort_keys=True, separators=(",", ":")))
