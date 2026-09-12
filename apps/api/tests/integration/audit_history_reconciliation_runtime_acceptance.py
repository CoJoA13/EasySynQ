"""Independent installed-image, genuine TLS and complete-history acceptance.

Only disposable public fixtures are used. Synthetic work is counted separately
from provider requests. A compact receipt is admitted against host-owned inputs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote
from xml.etree import ElementTree as ET

import pytest
import rfc8785
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.docker_client import DockerClient
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID

from .audit_external_runtime_acceptance import _api_image as _api_image
from .audit_external_runtime_acceptance import _ApiImage
from .audit_history_collection_runtime_acceptance import _cleanup_content_md5
from .audit_history_collection_runtime_acceptance import _exec as _old_exec
from .audit_raw_runtime_acceptance import (
    _archive_file,
    _create_bucket,
    _environment,
    _owned_parent,
    _runtime_reader,
)
from .audit_version_page_transport_runtime_acceptance import (
    _pinned_images,
    _provider_address,
    _provider_certificate_material,
    _provider_client,
    _provider_endpoint,
    _TlsMc,
    _trace_snapshot,
)

_CASES = frozenset(
    {
        "provider-missing",
        "provider-consistent",
        "provider-conflict",
        "synthetic-consistent",
        "synthetic-conflict",
        "hostile",
    }
)
_LIMITS = dict(
    address_space=536870912,
    cpu_seconds=120,
    descriptors=32,
    core_bytes=0,
    file_bytes=268435456,
    command_seconds=10,
    frame_bytes=131072,
    result_bytes=65536,
)
_CLEAN = (
    "reaped",
    "pipes_closed",
    "watchdog_joined",
    "directories_removed",
    "open_unlinked_absent",
    "credentials_absent",
)


class ReceiptError(ValueError):
    """Fixed safe failure text, never provider labels or supplied JSON."""


def _invalid() -> None:
    raise ReceiptError("invalid reconciliation receipt")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            _invalid()
        value[key] = item
    return value


def _same(actual: Any, expected: Any) -> None:
    if type(actual) is not type(expected):
        _invalid()
    if type(expected) is dict:
        if actual.keys() != expected.keys():
            _invalid()
        for key in expected:
            _same(actual[key], expected[key])
    elif type(expected) is list:
        if len(actual) != len(expected):
            _invalid()
        for a, e in zip(actual, expected, strict=True):
            _same(a, e)
    elif actual != expected:
        _invalid()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def transport_digest(events: list[list[str]]) -> str:
    """Canonical sorted multiset preserves repeated exact transport events."""
    return hashlib.sha256(_json(sorted(events))).hexdigest()


def check_receipt(raw: bytes, expected: dict[str, Any]) -> dict[str, Any]:
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        _invalid()
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _value: _invalid())
    except (ValueError, RecursionError, UnicodeError):
        _invalid()
    if type(value) is not dict or set(value) != {
        "schema_version",
        "case",
        "build_digest",
        "proof_digest",
        "runtime",
        "counts",
        "outcomes",
        "limits",
        "cleanup",
        "transport_digest",
    }:
        _invalid()
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or type(value["case"]) is not str
        or value["case"] not in _CASES
    ):
        _invalid()
    for key in ("case", "build_digest", "proof_digest", "counts", "outcomes", "transport_digest"):
        _same(value[key], expected[key])
    for key in ("build_digest", "proof_digest", "transport_digest"):
        if type(value[key]) is not str or re.fullmatch("[0-9a-f]{64}", value[key]) is None:
            _invalid()
    runtime = value["runtime"]
    if type(runtime) is not dict or set(runtime) != {
        "python",
        "sqlite",
        "uid",
        "module_root",
        "installed_sources",
        "dev_packages_absent",
    }:
        _invalid()
    for key, pattern in (("python", r"3\.12\.\d+"), ("sqlite", r"3\.\d+\.\d+")):
        if type(runtime[key]) is not str or re.fullmatch(pattern, runtime[key]) is None:
            _invalid()
    _same(runtime["uid"], 10001)
    _same(runtime["module_root"], "/app/src/easysynq_api")
    _same(runtime["installed_sources"], expected["installed_sources"])
    _same(runtime["dev_packages_absent"], ["mypy", "pytest", "ruff"])
    _same(value["limits"], _LIMITS)
    _same(value["cleanup"], {"workers": expected["workers"], **dict.fromkeys(_CLEAN, True)})
    return value


pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[4]
_RUN_LABEL = "com.easysynq.audit-external.run"
_SOURCE_LABEL = "com.easysynq.audit-external.source"
_FIXTURE_LABEL = "com.easysynq.audit-reconciliation-fixture"
_PROBE = "/run/audit-history-reconciliation-probe.py"
_CONFIG = "/run/audit-history-reconciliation-config.json"
_WRITABLE = "/run/audit-history-reconciliation-write"
_PYTHON = "/app/.venv/bin/python"
_SOURCE_PATH = _ROOT / "apps/api/src/easysynq_api/services/audit"


def _sources() -> dict[str, str]:
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(_SOURCE_PATH.glob("*.py"))
    }


def _material(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    path.chmod(0o444)
    return path


def _config(
    case: Any, name: str, record: dict[str, Any], *, kernel: bool = False
) -> dict[str, Any]:
    from tests.unit.audit_history_reconciliation_vectors import kernel_payload

    case = dataclasses.replace(
        case, args=(*case.args[:4], dataclasses.replace(case.args[4], maximum_wall_seconds=60))
    )
    payload = kernel_payload(case)
    return dict(
        case=name,
        build_digest=record["build_input_sha256"],
        proof_digest=record["proof_input_sha256"],
        scope=payload["scope"],
        root=payload["root"],
        pages=payload["pages"],
        readers=[
            dict(witness_id=str(r.witness_id), **dataclasses.asdict(r.reader)) for r in case.args[3]
        ],
        provider=[] if name.startswith("provider-") else payload["provider"],
        writable=_WRITABLE,
        kernel=kernel,
    )


def _expected(
    case: Any, name: str, record: dict[str, Any], events: list[list[str]], *, kernel: bool = False
) -> dict[str, Any]:
    kind = "provider" if name.startswith("provider-") else "synthetic"
    counts = dict(provider=dict(list=0, get=0), synthetic=dict(list=0, get=0))
    counts[kind] = dict(
        list=sum(e[1] == "LIST" for e in events), get=sum(e[1] == "GET" for e in events)
    )
    if name == "hostile":
        outcomes = (
            dict.fromkeys(("kill-seal", "kill-graph", "kill-final"), "WORKER_FAILED")
            | dict(
                stale="PROTOCOL_INVALID",
                trailing="PROTOCOL_INVALID",
                cancel="cancelled",
                deadline="DEADLINE_EXCEEDED",
                cleanup="CLEANUP_FAILED",
                heap="enforced",
                fd="enforced",
                cpu="enforced",
                file="enforced",
            )
            | {
                "sql-journal": "RUNTIME_UNSUPPORTED",
                "sql-sort": "RUNTIME_UNSUPPORTED",
                "sql-attach": "RUNTIME_UNSUPPORTED",
                "sql-heap": "RESOURCE_LIMIT",
                "sql-file": "RESOURCE_LIMIT",
                "sql-policy": dict(
                    denied=6,
                    attached_limit=0,
                    settings=dict(
                        page_size=4096,
                        max_page_count=65536,
                        journal_mode="memory",
                        temp_store=2,
                        mmap_size=0,
                        cache_size=-1024,
                        hard_heap_limit=67108864,
                        synchronous=0,
                        trusted_schema=0,
                        threads=0,
                        busy_timeout=0,
                    ),
                ),
            }
        )
        workers = 18
    else:
        bad = name.endswith("-conflict")
        missing = name == "provider-missing"
        codes = (
            [["composition", "GLOBAL_SIGNED_HEAD_CONFLICT"], ["lineage", "AUDIT_HEAD_CONFLICT"]]
            if bad
            else [["composition", "V2_WITNESS_COVERAGE_MISSING"]]
            if missing
            else []
        )
        outcomes = dict(
            status="failed" if bad else "incomplete" if missing else "consistent",
            codes=codes,
            required_checkpoint_relation="included"
            if name.startswith("provider-") or bad
            else "not-provided",
            path_length=0 if bad or missing else len(case.expected_path),
            used_epoch_count=0 if bad or missing else len(case.expected_used_epochs),
            tip=None if bad or missing else case.expected_path[-1],
        )
        workers = 1 + int(kernel)
    return dict(
        case=name,
        build_digest=record["build_input_sha256"],
        proof_digest=record["proof_input_sha256"],
        installed_sources=_sources(),
        counts=counts,
        outcomes=outcomes,
        transport_digest=transport_digest(events),
        workers=workers,
    )


def _original_events(case: Any) -> list[list[str]]:
    buckets = {r.reader.bucket: str(r.witness_id) for r in case.args[3]}
    return [
        [buckets[bucket], "LIST", key or "", version or "", hashlib.sha256(page.body).hexdigest()]
        for (bucket, key, version), page in case.list_map.items()
    ] + [
        [str(d.witness), "GET", d.key, d.version, hashlib.sha256(d.body).hexdigest()]
        for d in case.deliveries
    ]


def _exec_case(
    image: _ApiImage,
    material: Path,
    labels: dict[str, str],
    config: dict[str, Any],
    *,
    mounts: tuple[tuple[Path, str], ...] = (),
    network: str = "none",
) -> tuple[bytes, dict[str, Any] | None]:
    path = _material(material / (config["case"] + ".json"), _json(config))
    writable = material / (config["case"] + "-write")
    writable.mkdir(mode=0o777)
    writable.chmod(0o777)
    source = Path(__file__).with_name("audit_history_reconciliation_runtime_probe.py")
    kernel = _ROOT / "apps/api/tests/unit/audit_history_reconciliation_kernel_worker.py"
    container = DockerContainer(
        image.image_id,
        command=["sleep", "180"],
        user="10001:10001",
        read_only=True,
        network_mode=network,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={k: v for k, v in labels.items() if k != LABEL_SESSION_ID},
        volumes=[
            (str(source), _PROBE, "ro"),
            (str(path), _CONFIG, "ro"),
            (str(writable), _WRITABLE, "rw"),
            (str(kernel), "/app/tests/unit/audit_history_reconciliation_kernel_worker.py", "ro"),
            *((str(p), dest, "ro") for p, dest in mounts),
        ],
    )
    detail = None
    try:
        with container:
            wrapped = container.get_wrapped_container()
            wrapped.reload()
            attrs = wrapped.attrs
            assert attrs["Image"] == image.image_id and attrs["Config"]["User"] == "10001:10001"
            assert attrs["HostConfig"]["ReadonlyRootfs"] is True and attrs["HostConfig"][
                "CapDrop"
            ] == ["ALL"]
            assert "no-new-privileges:true" in attrs["HostConfig"]["SecurityOpt"]
            assert _environment(attrs["Config"].get("Env")) == image.environment
            assert all(not m["RW"] or m["Destination"] == _WRITABLE for m in attrs["Mounts"])
            assert all(attrs["Config"]["Labels"][k] == v for k, v in labels.items())
            for name in _sources():
                assert (
                    _archive_file(container, "/app/src/easysynq_api/services/audit/" + name)
                    == (_SOURCE_PATH / name).read_bytes()
                )
            run = container.exec(
                ExecConfig(command=[_PYTHON, "-I", "-B", "-u", _PROBE, _CONFIG], environment={})
            )
            raw = run.output
            assert all(
                r[k].encode() not in raw
                for r in config["readers"]
                for k in ("access_key", "secret_key")
            )
            assert run.exit_code == 0, (
                f"reconciliation probe failed case={config['case']}: {raw[-512:]!r}"
            )
            assert 0 < len(raw) <= 65536
            if config["kernel"]:
                original = _archive_file(container, _WRITABLE + "/kernel.json")
                assert len(original) <= 65536
                detail = json.loads(original, object_pairs_hook=_pairs)
                (writable / "kernel.json").unlink()
            assert not list(writable.iterdir())
        assert not wrapped.client.containers.list(all=True, filters={"id": wrapped.id})
    finally:
        shutil.rmtree(writable)
        path.unlink()
    return raw, detail


def _assert_kernel(detail: dict[str, Any], case: Any, sqlite_version: str) -> None:
    assert detail["path_digest"] == hashlib.sha256(_json(list(case.expected_path))).hexdigest()
    assert (
        detail["epochs_digest"]
        == hashlib.sha256(_json(list(case.expected_used_epochs))).hexdigest()
    )
    assert detail["counts"] == dict(
        inspect=4097, verify=4097, edge=4097, legacy_decode=4609, legacy_auth=4609
    )
    metrics = detail["measurements"]
    assert metrics["sqlite"] == sqlite_version
    assert 0 < metrics["maximum_step_seconds"] < 10 and 0 < metrics["maximum_step_work"] <= 512
    assert 0 < metrics["cpu_seconds"] < 120 and 0 < metrics["peak_rss_kib"] < 512 * 1024
    assert 0 < metrics["database_bytes"] <= 268435456
    assert len(detail["plans"]) == 58
    assert all(
        p["plan"] and not any("TEMP B-TREE" in row for row in p["plan"]) for p in detail["plans"]
    )


def _header(response: dict[str, Any], name: str) -> str:
    matches = [v for k, v in response["headers"].items() if k.lower() == name.lower()]
    assert len(matches) == 1 and type(matches[0]) is str
    return matches[0]


def _trace_events(trace: list[dict[str, Any]], case: Any) -> list[list[str]]:
    """Trace pins method/key/version; original GET hashes come from exact seeded bytes.

    MinIO verbose trace exposes GET as <BLOB>, so it is never misrepresented as
    independent raw GET byte capture. The probe's actual raw hash must match the
    separately retained seed for that exact version; original LIST XML is traced.
    """
    expected = {(str(d.witness), d.key, d.version): d.body for d in case.deliveries}
    assert len(expected) == len(case.deliveries)
    events, gets, listed = [], [], []
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    for reader in case.args[3]:
        bucket, access, witness = (
            reader.reader.bucket,
            reader.reader.access_key,
            str(reader.witness_id),
        )
        selected = [
            e
            for e in trace
            if f"Credential={access}/"
            in e.get("request", {}).get("headers", {}).get("Authorization", "")
        ]
        pages = 0
        prefix = quote(f"checkpoints/{case.args[0].stream.org_id}/", safe="-_.~")
        for event in selected:
            request, response = event["request"], event["response"]
            assert request["method"] == "GET" and response["statusCode"] == 200
            assert request["headers"]["Host"] == "127.0.0.1:9000"
            assert "/us-east-1/s3/aws4_request" in request["headers"]["Authorization"]
            if event["api"] == "s3.ListObjectVersions":
                pages += 1
                assert request["path"] == "/" + bucket
                assert (
                    request["rawQuery"]
                    == f"versions&prefix={prefix}&max-keys=1000&encoding-type=url"
                )
                raw = response["body"].encode()
                assert len(raw) == int(_header(response, "Content-Length"))
                doc = ET.fromstring(raw)  # noqa: S314 - bounded disposable provider trace
                assert doc.findtext("s:IsTruncated", namespaces=ns) == "false"
                assert not doc.findall("s:DeleteMarker", ns)
                for item in doc.findall("s:Version", ns):
                    listed.append(
                        (
                            witness,
                            unquote(item.findtext("s:Key", namespaces=ns)),
                            unquote(item.findtext("s:VersionId", namespaces=ns)),
                        )
                    )
                events.append([witness, "LIST", "", "", hashlib.sha256(raw).hexdigest()])
            else:
                assert event["api"] == "s3.GetObject"
                query = parse_qs(request["rawQuery"], strict_parsing=True, keep_blank_values=True)
                assert set(query) == {"versionId"} and len(query["versionId"]) == 1
                assert request["path"].startswith("/" + bucket + "/")
                key, version = request["path"][len(bucket) + 2 :], query["versionId"][0]
                assert _header(response, "X-Amz-Version-Id") == version
                assert response["body"] == "<BLOB>"
                body = expected[witness, key, version]
                assert len(body) == int(_header(response, "Content-Length"))
                gets.append((witness, key, version))
                events.append([witness, "GET", key, version, hashlib.sha256(body).hexdigest()])
        assert pages == 1
    assert Counter(gets) == Counter(expected.keys()) and Counter(listed) == Counter(expected.keys())
    return events


def _seed_cases(
    admin: Any,
    buckets: list[str],
    credentials: list[tuple[str, str]],
    created: dict[str, list[dict[str, str]]],
):
    """Bind actual retained legacy versions before signing the v2 descendants."""
    from easysynq_api.services.audit.bootstrap_bridge import BridgePageObservation, BridgeWitnessPin
    from easysynq_api.services.audit.lineage import RequiredCheckpointPin
    from easysynq_api.services.audit.sink import ExplicitHistoryReader
    from tests.unit.audit_history_reconciliation_vectors import (
        PREFIX,
        Delivery,
        commitment,
        large_case,
        rebind_package,
        replace_deliveries,
    )

    case = large_case(3, 5, 2)
    unique = tuple(dict.fromkeys(case.deliveries))
    readers, pins, legacy = [], [], []
    versions = {}
    for index, old in enumerate(case.args[3]):
        namespace = dict(
            kind="worm_bucket",
            endpoint="https://127.0.0.1:9000",
            bucket=buckets[index],
            region="us-east-1",
            prefix=PREFIX,
        )
        digest = commitment("namespace", rfc8785.dumps(namespace))
        pins.append(BridgeWitnessPin(old.witness_id, digest))
        readers.append(
            dataclasses.replace(
                old,
                reader=ExplicitHistoryReader(
                    namespace["endpoint"], buckets[index], "us-east-1", *credentials[index]
                ),
            )
        )
        for d in unique:
            if d.witness != old.witness_id or "/v2/" in d.key:
                continue
            version = admin.put_object(Bucket=buckets[index], Key=d.key, Body=d.body)["VersionId"]
            created[buckets[index]].append(dict(Key=d.key, VersionId=version))
            versions[str(d.witness), d.key, d.version] = version
            legacy.append(Delivery(d.witness, d.key, version, d.body))
    root = json.loads(case.args[1])
    for entry, pin in zip(root["witnesses"], pins, strict=True):
        entry["namespace_hash"] = pin.namespace_hash
    pages = [json.loads(p.body) for p in case.args[2]]
    for page in pages:
        for entry in page["entries"]:
            entry["version_id"] = versions[
                entry["witness_id"], entry["object_key"], entry["version_id"]
            ]
    case = dataclasses.replace(
        case,
        args=(
            dataclasses.replace(case.args[0], witnesses=tuple(pins)),
            *case.args[1:3],
            tuple(readers),
            case.args[4],
        ),
    )
    case = rebind_package(case, tuple(BridgePageObservation(rfc8785.dumps(p)) for p in pages), root)
    enrollment = dataclasses.replace(
        case.args[0],
        stream=dataclasses.replace(
            case.args[0].stream, required_checkpoint=RequiredCheckpointPin(case.expected_path[0], 1)
        ),
    )
    case = dataclasses.replace(case, args=(enrollment, *case.args[1:]))
    graph = tuple(dict.fromkeys(d for d in case.deliveries if "/v2/" in d.key))
    retained, missing = list(legacy), None
    for d in graph:
        index = next(i for i, r in enumerate(readers) if r.witness_id == d.witness)
        if index == 1 and json.loads(d.body)["checkpoint"]["sequence"] == "2":
            missing = d
            continue
        version = admin.put_object(Bucket=buckets[index], Key=d.key, Body=d.body)["VersionId"]
        created[buckets[index]].append(dict(Key=d.key, VersionId=version))
        retained.append(dataclasses.replace(d, version=version))
    assert missing is not None and len(retained) == 10
    return replace_deliveries(case, tuple(retained)), missing


def _provider_cases(
    image: _ApiImage,
    material: Path,
    labels: dict[str, str],
    bundle: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    from botocore.exceptions import BotoCoreError, ClientError

    from tests.unit.audit_history_reconciliation_vectors import (
        PREFIX,
        Delivery,
        replace_deliveries,
        sign_v2,
    )

    resolver = DockerClient(timeout=15)
    client = resolver.client
    admin, outcome = None, None
    containers, buckets = [], []
    created = {}
    started = time.monotonic()
    try:
        identities = _pinned_images(client)
        host, binding = _provider_address(resolver.host())
        ca, certificate, private = _provider_certificate_material(host)
        certs = material / "certs"
        certs.mkdir(mode=0o755)
        certs.chmod(0o755)
        _material(certs / "public.crt", certificate)
        _material(certs / "private.key", private)
        ca_file = _material(material / "provider-ca.pem", ca)
        combined = _material(material / "combined-ca.pem", bundle["bundle"].rstrip() + b"\n" + ca)
        root_access, root_secret = (
            "synthetic-admin-" + uuid.uuid4().hex[:12],
            "synthetic-" + uuid.uuid4().hex,
        )
        provider = client.containers.create(
            identities["minio"],
            command=["server", "--certs-dir", "/certs", "/data"],
            environment={
                "MINIO_ROOT_USER": root_access,
                "MINIO_ROOT_PASSWORD": root_secret,
                "MINIO_BROWSER": "off",
            },
            ports={"9000/tcp": (binding, None)},
            volumes={str(certs): {"bind": "/certs", "mode": "ro"}},
            tmpfs={"/data": "rw,size=134217728,mode=0700", "/tmp": "rw,size=8388608,mode=1777"},  # noqa: S108 - owned provider tmpfs
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
        port = provider.attrs["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"]
        endpoint = _provider_endpoint(host, port)
        admin = _provider_client(endpoint, root_access, root_secret, ca_file)
        admin.meta.events.register(
            "before-sign.s3.DeleteObjects",
            _cleanup_content_md5,
            unique_id="easysynq.reconciliation.cleanup-md5",
        )
        while True:
            try:
                admin.list_buckets()
                break
            except (BotoCoreError, ClientError):
                assert time.monotonic() - started < 20
                time.sleep(0.2)
        for _ in range(2):
            bucket = _create_bucket(admin, locked=False)
            buckets.append(bucket)
            created[bucket] = []
            admin.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
        mc_config = _material(
            material / "mc-config.json",
            _json(
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
            ),
        )
        mc_mounts = [
            (str(mc_config), "/tmp/mc/config.json", "ro"),  # noqa: S108 - private helper config
            (str(ca_file), "/tmp/mc/certs/CAs/ca.pem", "ro"),  # noqa: S108 - read-only CA
        ]
        with _TlsMc(
            identities["mc"],
            command=["-c", "sleep 240"],
            entrypoint="/bin/sh",
            network_mode="container:" + provider.id,
            volumes=mc_mounts,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels={k: v for k, v in labels.items() if k != LABEL_SESSION_ID},
        ).with_tmpfs_mount("/tmp", "rw,size=8388608,mode=1777") as helper:  # noqa: S108 - owned helper tmpfs
            with ExitStack() as stack:
                credentials = [
                    stack.enter_context(_runtime_reader(helper, {}, (b,))) for b in buckets
                ]
                assert len({a for a, _s in credentials}) == 2
                assert all(a != root_access and s != root_secret for a, s in credentials)
                case, missing = _seed_cases(admin, buckets, credentials, created)
                enrollment, root = case.args[:2]
                trace = client.containers.create(
                    identities["mc"],
                    entrypoint=["mc"],
                    command=[
                        "--config-dir",
                        "/tmp/mc",  # noqa: S108 - private trace configuration
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
                    volumes={src: {"bind": dest, "mode": mode} for src, dest, mode in mc_mounts},
                    tmpfs={"/tmp": "rw,size=8388608,mode=1777"},  # noqa: S108 - owned trace tmpfs
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    labels=labels,
                )
                containers.append(trace)
                trace.start()

                def barrier() -> list[dict[str, Any]]:
                    deadline = time.monotonic() + 10
                    while True:
                        request_id = admin.list_buckets()["ResponseMetadata"]["RequestId"]
                        time.sleep(0.05)
                        events, pending = _trace_snapshot(trace)
                        if not pending and any(
                            e.get("response", {}).get("headers", {}).get("X-Amz-Request-Id")
                            == request_id
                            for e in events
                        ):
                            return events
                        assert time.monotonic() < deadline

                receipts = []
                for name in ("provider-missing", "provider-consistent", "provider-conflict"):
                    if name == "provider-consistent":
                        d, bucket = missing, buckets[1]
                    elif name == "provider-conflict":
                        payload = json.loads(
                            next(
                                d.body
                                for d in case.deliveries
                                if "/v2/" in d.key
                                and json.loads(d.body)["checkpoint"]["sequence"] == "3"
                            )
                        )["checkpoint"]
                        payload.update(
                            anchor_id=str(uuid.UUID(int=90000)), latest_row_hash="cd" * 32
                        )
                        d = Delivery(
                            case.args[3][1].witness_id,
                            PREFIX + "zz-conflict",
                            "",
                            sign_v2(payload, 1),
                        )
                        bucket = buckets[1]
                    if name != "provider-missing":
                        version = admin.put_object(Bucket=bucket, Key=d.key, Body=d.body)[
                            "VersionId"
                        ]
                        created[bucket].append(dict(Key=d.key, VersionId=version))
                        case = replace_deliveries(
                            case, (*case.deliveries, dataclasses.replace(d, version=version))
                        )
                    assert case.args[0] == enrollment and case.args[1] == root
                    before = len(barrier())
                    raw, detail = _exec_case(
                        image,
                        material,
                        labels,
                        _config(case, name, record),
                        mounts=((combined, bundle["certifi_path"]),),
                        network="container:" + provider.id,
                    )
                    assert detail is None
                    events = _trace_events(barrier()[before:], case)
                    expected = _expected(case, name, record, events)
                    receipt = check_receipt(raw, expected)
                    # The same independent trace/seed inventory rejects omitted B reads,
                    # substituted versions and substituted original bytes in a receipt.
                    for mutation in ("missing-b", "version", "bytes"):
                        altered = [list(e) for e in events]
                        index = next(
                            i
                            for i, e in enumerate(altered)
                            if e[0] == str(case.args[3][1].witness_id) and e[1] == "GET"
                        )
                        if mutation == "missing-b":
                            altered.pop(index)
                        else:
                            altered[index][3 if mutation == "version" else 4] = "different"
                        changed = dict(receipt, transport_digest=transport_digest(altered))
                        with pytest.raises(ReceiptError):
                            check_receipt(_json(changed), expected)
                    receipts.append(receipt)
                outcome = dict(
                    receipts=receipts,
                    provider_image_id=identities["minio"],
                    trace_image_id=identities["mc"],
                    same_enrollment=True,
                    exact_transport_mutations_rejected=9,
                )
    finally:
        failures = []
        if admin is not None:
            for bucket in reversed(buckets):
                try:
                    if created[bucket]:
                        response = admin.delete_objects(
                            Bucket=bucket, Delete={"Objects": created[bucket], "Quiet": True}
                        )
                        assert not response.get("Errors")
                    admin.delete_bucket(Bucket=bucket)
                except Exception as error:  # noqa: BLE001 - clean every owned fixture
                    failures.append(error)
            admin.close()
        for container in reversed(containers):
            try:
                container.remove(force=True, v=True)
            except Exception as error:  # noqa: BLE001 - clean every owned fixture
                failures.append(error)
        try:
            assert not client.containers.list(
                all=True, filters={"label": _FIXTURE_LABEL + "=" + labels[_FIXTURE_LABEL]}
            )
        finally:
            client.close()
        if failures:
            raise ExceptionGroup("reconciliation provider cleanup failed", failures)
    assert outcome is not None
    return outcome | dict(all_owned_provider_resources_removed=True)


def test_history_reconciliation_runtime_preserves_global_closure_and_owned_limits(
    _api_image: _ApiImage, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.unit.audit_history_reconciliation_vectors import large_case, late_case

    started = time.monotonic()
    fixture_id = uuid.uuid4().hex
    labels = {
        _RUN_LABEL: os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"],
        LABEL_SESSION_ID: SESSION_ID,
        _FIXTURE_LABEL: fixture_id,
    }
    material = _owned_parent() / (fixture_id + "-reconciliation")
    material.mkdir(mode=0o700)
    material.chmod(0o700)
    client = DockerClient(timeout=15).client
    phase, timings, receipts = "identity", {}, []
    phase_started = started
    try:
        record = json.loads((_owned_parent() / "runner.json").read_text())
        assert record["run_id"] == labels[_RUN_LABEL] and record["image_id"] == _api_image.image_id
        image = client.images.get(_api_image.image_id)
        assert image.attrs["Config"]["Labels"][_SOURCE_LABEL] == record["build_input_sha256"]
        assert image.attrs["Config"]["Labels"][_RUN_LABEL] == labels[_RUN_LABEL]
        phase, phase_started = "provider", time.monotonic()
        bundle = _old_exec(_api_image, material, labels, "certifi", {})
        provider = _provider_cases(_api_image, material, labels, bundle, record)
        receipts.extend(provider.pop("receipts"))
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "synthetic-consistent", time.monotonic()
        large = large_case(4097, 4609, 2)
        assert large.page_deliveries == 10 and large.body_deliveries == 12809
        raw, detail = _exec_case(
            _api_image, material, labels, _config(large, phase, record, kernel=True)
        )
        positive = check_receipt(
            raw, _expected(large, phase, record, _original_events(large), kernel=True)
        )
        assert detail is not None
        _assert_kernel(detail, large, positive["runtime"]["sqlite"])
        receipts.append(positive)
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "synthetic-conflict", time.monotonic()
        conflicting = late_case(large, "cross-format-conflict")
        raw, extra = _exec_case(_api_image, material, labels, _config(conflicting, phase, record))
        assert extra is None
        receipts.append(
            check_receipt(raw, _expected(conflicting, phase, record, _original_events(conflicting)))
        )
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "hostile", time.monotonic()
        small = large_case(3, 5, 2)
        raw, extra = _exec_case(_api_image, material, labels, _config(small, phase, record))
        assert extra is None
        receipts.append(
            check_receipt(raw, _expected(small, phase, record, _original_events(small) * 8))
        )
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        assert len(receipts) == 6 and {r["case"] for r in receipts} == _CASES
        assert all(r["runtime"] == positive["runtime"] for r in receipts)
    except BaseException:
        with capsys.disabled():
            print(
                "AUDIT_HISTORY_RECONCILIATION_FAILURE "
                + _json(
                    dict(
                        phase=phase,
                        phase_elapsed_ms=int((time.monotonic() - phase_started) * 1000),
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        completed_phases_ms=timings,
                    )
                ).decode()
            )
        raise
    finally:
        failures = []
        try:
            for container in client.containers.list(
                all=True, filters={"label": _FIXTURE_LABEL + "=" + fixture_id}
            ):
                try:
                    container.remove(force=True, v=True)
                except Exception as error:  # noqa: BLE001 - finish every owned cleanup
                    failures.append(error)
            assert not client.containers.list(
                all=True, filters={"label": _FIXTURE_LABEL + "=" + fixture_id}
            )
        finally:
            client.close()
            shutil.rmtree(material)
        if failures:
            raise ExceptionGroup("reconciliation fixture cleanup failed", failures)
    elapsed = int((time.monotonic() - started) * 1000)
    assert not material.exists() and elapsed <= 300_000, (
        "reconciliation acceptance allocation exceeded"
    )
    proof = dict(
        schema_version=1,
        application_image_id=_api_image.image_id,
        commit=record["commit"],
        run_id=record["run_id"],
        fixture_id=fixture_id,
        build_digest=record["build_input_sha256"],
        proof_digest=record["proof_input_sha256"],
        runtime=positive["runtime"],
        provider=provider,
        receipts=[{k: v for k, v in receipt.items() if k != "runtime"} for receipt in receipts],
        kernel=detail,
        timings_ms=timings,
        elapsed_ms=elapsed,
        all_owned_resources_removed=True,
    )
    original = _json(proof)
    assert len(original) <= 65536
    with capsys.disabled():
        print("AUDIT_HISTORY_RECONCILIATION_PROOF " + original.decode())
