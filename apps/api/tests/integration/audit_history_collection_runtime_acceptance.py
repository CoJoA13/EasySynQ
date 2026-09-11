"""Mandatory independent acceptance of inactive required-witness collection.

The installed-image probe is an observation producer. This module owns synthetic
inputs, exact expected results, provider request/body trace comparison, mutation
rejection, immutable image/source identity and disposable resource cleanup.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.docker_client import DockerClient
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID

from .audit_external_runtime_acceptance import _api_image as _api_image
from .audit_external_runtime_acceptance import _ApiImage
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

pytestmark = pytest.mark.integration

_PYTHON = "/app/.venv/bin/python"
_PROBE = "/run/audit-history-collection-probe.py"
_CONFIG = "/run/audit-history-collection-config.json"
_WRITABLE = "/run/audit-history-collection-write"
_RUN_LABEL = "com.easysynq.audit-external.run"
_SOURCE_LABEL = "com.easysynq.audit-external.source"
_FIXTURE_LABEL = "com.easysynq.audit-history-fixture"
_PROOF_PREFIX = "AUDIT_HISTORY_COLLECTION_PROOF "
_FAILURE_PREFIX = "AUDIT_HISTORY_COLLECTION_FAILURE "
_RESOURCE_FAILURE_PREFIX = "AUDIT_HISTORY_COLLECTION_RESOURCE_FAILURE "
_RAW_WORKER_FAILURE_PREFIX = "AUDIT_HISTORY_COLLECTION_RAW_WORKER_FAILURE "
_ROOT = Path(__file__).resolve().parents[4]
_ORG = "00000000-0000-4000-8000-000000000011"
_PREFIX = f"checkpoints/{_ORG}/"
_WITNESSES = (
    "00000000-0000-4000-8000-000000000021",
    "00000000-0000-4000-8000-000000000022",
)
_SOURCE_NAMES = (
    "history_collection.py",
    "_history_spool.py",
    "_history_spool_protocol.py",
    "_history_spool_store.py",
    "_history_spool_worker.py",
    "isolated_raw.py",
    "isolated_version_page.py",
    "raw_transport.py",
    "version_page_transport.py",
    "version_page.py",
)
_LIMITS = {
    "address_space": [536_870_912, 536_870_912],
    "cpu_seconds": [120, 120],
    "file_descriptors": [32, 32],
    "core_bytes": [0, 0],
    "file_bytes": [1_073_741_824, 1_073_741_824],
}
_UNPROVED = [
    "body-format-and-signatures",
    "global-history-consistency",
    "provider-non-omission",
    "atomic-snapshot",
    "historical-deletion-absence",
    "witness-custody",
    "database-chain-agreement",
    "freshness",
    "rollback-memory-continuity",
    "key-activation",
]


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _material(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    path.chmod(0o444)
    return path


def _witness(index: int, bucket: str, access: str, secret: str) -> dict[str, Any]:
    namespace = {
        "kind": "worm_bucket",
        "endpoint": "https://127.0.0.1:9000",
        "bucket": bucket,
        "region": "us-east-1",
        "prefix": _PREFIX,
    }
    # All keys/values here are ASCII; sorted compact JSON is the independent
    # canonical encoding for this fixed namespace, without the product hash helper.
    digest = hashlib.sha256(
        b"EasySynQ/AuditLegacyBridge/v1/namespace\0" + _json(namespace)
    ).hexdigest()
    return {
        "witness_id": _WITNESSES[index],
        "namespace_hash": digest,
        "endpoint": namespace["endpoint"],
        "bucket": bucket,
        "access_key": access,
        "secret_key": secret,
    }


def _synthetic_config() -> dict[str, Any]:
    vectors = json.loads(
        (_ROOT / "apps/api/tests/fixtures/audit_history_collection_vectors.json").read_text()
    )
    witnesses = []
    for index, vector in enumerate(vectors["namespaces"]):
        assert vector["witness_id"] == _WITNESSES[index]
        assert bytes.fromhex(vector["canonical_namespace_hex"]) == _json(vector["namespace"])
        assert (
            hashlib.sha256(
                b"EasySynQ/AuditLegacyBridge/v1/namespace\0"
                + bytes.fromhex(vector["canonical_namespace_hex"])
            ).hexdigest()
            == vector["namespace_hash"]
        )
        witnesses.append(
            {
                "witness_id": vector["witness_id"],
                "namespace_hash": vector["namespace_hash"],
                "endpoint": vector["namespace"]["endpoint"],
                "bucket": vector["namespace"]["bucket"],
                "access_key": f"synthetic-collection-access-{index}",
                "secret_key": f"synthetic-collection-secret-{index}",
            }
        )
    pages = []
    for index in range(5):
        cursor = (
            ""
            if index == 0
            else f"<KeyMarker>{quote(_PREFIX + str(index), safe='-_.~')}</KeyMarker>"
        )
        following = (
            ""
            if index == 4
            else f"<NextKeyMarker>{quote(_PREFIX + str(index + 1), safe='-_.~')}</NextKeyMarker>"
        )
        original = (
            '<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"<Name>store-fixture</Name><Prefix>{quote(_PREFIX, safe='-_.~')}</Prefix>"
            "<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
            f"<IsTruncated>{'false' if index == 4 else 'true'}</IsTruncated>"
            + cursor
            + following
            + (
                (
                    f"<Version><Key>{quote(_PREFIX + 'blob', safe='-_.~')}</Key>"
                    "<VersionId>null</VersionId><IsLatest>false</IsLatest></Version>"
                )
                * 1000
            )
            + "</ListVersionsResult>"
        ).encode()
        pages.append(original.hex())
    return {"witnesses": witnesses, "store_pages": pages, "writable": _WRITABLE}


@contextmanager
def _probe_container(
    image: _ApiImage,
    config: Path,
    writable: Path,
    labels: dict[str, str],
    *,
    mounts: tuple[tuple[Path, str], ...] = (),
    network_mode: str = "none",
    lifetime: int = 500,
) -> Iterator[DockerContainer]:
    source = Path(__file__).with_name("audit_history_collection_runtime_probe.py")
    assert source.is_file() and not source.is_symlink()
    container = DockerContainer(
        image.image_id,
        command=["sleep", str(lifetime)],
        volumes=[
            (str(source), _PROBE, "ro"),
            (str(config), _CONFIG, "ro"),
            (str(writable), _WRITABLE, "rw"),
            *((str(path), destination, "ro") for path, destination in mounts),
        ],
        user="10001:10001",
        read_only=True,
        network_mode=network_mode,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={name: value for name, value in labels.items() if name != LABEL_SESSION_ID},
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
        assert all(attrs["Config"]["Labels"][name] == value for name, value in labels.items())
        assert _environment(attrs["Config"].get("Env")) == image.environment
        assert all(
            not mount["RW"] or mount["Destination"] == _WRITABLE for mount in attrs["Mounts"]
        )
        assert any(mount["RW"] and mount["Destination"] == _WRITABLE for mount in attrs["Mounts"])
        for name in _SOURCE_NAMES:
            expected = (_ROOT / "apps/api/src/easysynq_api/services/audit" / name).read_bytes()
            assert (
                _archive_file(container, "/app/src/easysynq_api/services/audit/" + name) == expected
            )
        yield container


def _exec(
    image: _ApiImage,
    material: Path,
    labels: dict[str, str],
    mode: str,
    config: dict[str, Any],
    *,
    mounts: tuple[tuple[Path, str], ...] = (),
    network_mode: str = "none",
) -> dict[str, Any]:
    path = _material(material / (mode + "-config.json"), _json(config))
    writable = material / (mode + "-write")
    writable.mkdir(mode=0o777)
    writable.chmod(0o777)
    forbidden = tuple(
        item[field]
        for item in config.get("witnesses", ())
        for field in ("access_key", "secret_key")
    )
    started = time.monotonic()
    try:
        with _probe_container(
            image,
            path,
            writable,
            labels,
            mounts=mounts,
            network_mode=network_mode,
            lifetime={"certifi": 30, "provider": 500, "synthetic": 90, "resources": 230}[mode],
        ) as container:
            command = [_PYTHON, "-I", "-B", "-u", _PROBE, mode, "--config", _CONFIG]
            result = container.exec(ExecConfig(command=command, environment={}))
            output = result.output.decode("utf-8")
            assert all(value not in output for value in forbidden), (
                "probe output exposed credentials"
            )
            diagnostics = [
                line
                for line in output.splitlines()
                if line.startswith((_RESOURCE_FAILURE_PREFIX, _RAW_WORKER_FAILURE_PREFIX))
            ]
            assert all(
                sum(line.startswith(prefix) for line in diagnostics) <= 1
                for prefix in (_RESOURCE_FAILURE_PREFIX, _RAW_WORKER_FAILURE_PREFIX)
            )
            assert all(len(line) <= 4096 for line in diagnostics)
            assert result.exit_code == 0, (
                f"collection probe phase={mode} "
                f"elapsed_ms={int((time.monotonic() - started) * 1000)} "
                f"failed: {output[-4096:]}\n" + "\n".join(diagnostics)
            )
            observed = json.loads(output)
            if mode == "certifi":
                bundle = _archive_file(container, observed["certifi_path"])
                assert hashlib.sha256(bundle).hexdigest() == observed["certifi_sha256"]
                observed["bundle"] = bundle
        assert list(writable.iterdir()) == []
    finally:
        shutil.rmtree(writable)
        path.unlink()
    assert not writable.exists() and not path.exists()
    return observed | {"fixture_elapsed_ms": int((time.monotonic() - started) * 1000)}


def _assert_runtime(result: dict[str, Any]) -> None:
    assert result["uid"] == 10001
    assert result["dev_packages_absent"] == ["mypy", "pytest", "ruff"]
    assert set(result["runtime_versions"]) == {"python", "sqlite"}
    assert result["installed_sources"] == {
        name: hashlib.sha256(
            (_ROOT / "apps/api/src/easysynq_api/services/audit" / name).read_bytes()
        ).hexdigest()
        for name in _SOURCE_NAMES
    }
    assert result["writable_entries_after"] == []


def _assert_workers(
    case: dict[str, Any],
    *,
    file_bytes: int = 67_108_864,
    allow_startup_expiry: bool = False,
) -> None:
    assert len(case["workers"]) == 1
    worker = case["workers"][0]
    assert worker["cleanup_attempts"] >= 1
    assert worker["selector_closed"] is True, "worker cleanup was not proved"
    assert case["watchdogs_removed"] is True
    if worker["worker_started"]:
        assert worker["directory_created"] is True
        assert type(worker["exit_code"]) is int
        assert worker["reaped"] is worker["pipes_closed"] is True, "worker cleanup was not proved"
    else:
        assert worker["exit_code"] is worker["reaped"] is worker["pipes_closed"] is None
    if worker["directory_created"]:
        assert worker["directory_removed"] is True, "worker cleanup was not proved"
    else:
        assert worker["directory_removed"] is None
    if worker["initialized"] is False:
        assert allow_startup_expiry is True
        assert case["outcome"] == "DEADLINE_EXCEEDED" and case["report"] is None
        assert case["list_attempts"] == case["attempted_exact_reads"] == 0
        assert case["pages"] == case["reads"] == worker["samples"] == []
        assert {
            "limits",
            "directory_mode",
            "database_mode",
            "uid",
            "environment",
            "argv_flags",
        }.isdisjoint(worker), "uninitialized worker has no initialized OS-state proof"
        return
    assert worker["initialized"] is worker["worker_started"] is worker["directory_created"] is True
    assert worker["limits"] == _LIMITS | {"file_bytes": [file_bytes, file_bytes]}
    assert worker["directory_mode"] == 0o700 and worker["database_mode"] == 0o600
    assert worker["uid"] == 10001
    assert sorted(worker["environment"]) == ["LANG=C.UTF-8", "TZ=UTC"]
    assert worker["argv_flags"] == ["-I", "-B", "-u"]
    for field in ("reaped", "pipes_closed", "selector_closed", "directory_removed"):
        assert worker[field] is True
    assert case["watchdogs_removed"] is True
    assert worker["samples"]
    for sample in worker["samples"]:
        assert sample["children"] == ""
        assert sample["directory_entries"] == ["spool.sqlite3"]
        assert 0 < sample["database_bytes"] <= file_bytes
        assert not any(item["target"].startswith("socket:") for item in sample["fds"])
        regular = [item for item in sample["fds"] if item["regular"] and item["writable"]]
        assert len(regular) == 1 and regular[0]["target"].endswith("/spool.sqlite3")
        assert regular[0]["deleted"] is False


def _assert_report(
    case: dict[str, Any], witnesses: list[dict[str, Any]], status: str
) -> dict[str, Any]:
    assert case["outcome"] == "report" and case["report"] is not None
    report = case["report"]
    assert report["scope"] == "required-witness-provider-traversal"
    assert [(row["witness_id"], row["namespace_hash"]) for row in report["witnesses"]] == [
        (row["witness_id"], row["namespace_hash"]) for row in witnesses
    ], "required-witness inventory differs"
    assert report["status"] == status
    assert report["unproved_checks"] == _UNPROVED
    assert report["admitted_total_bytes"] == sum(
        page["body_bytes"] for page in case["pages"]
    ) + sum(row[3] for row in case["reads"])
    for row in report["witnesses"]:
        assert row["version_observations"] == row["successful_reads"] + row["unavailable_reads"]
    _assert_workers(case)
    assert case["workers"][0]["exit_code"] == 0
    return report


def _assert_scaling(case: dict[str, Any], witnesses: list[dict[str, Any]]) -> None:
    assert len(case["reads"]) == case["attempted_exact_reads"] == 5002, (
        "raw-body delivery count differs"
    )
    report = _assert_report(case, witnesses, "failed")
    assert len(case["pages"]) == case["list_attempts"] == 6
    assert [len(page["rows"]) for page in case["pages"]] == [1000, 1000, 1000, 1000, 1000, 2]
    a, b = report["witnesses"]
    assert (a["successful_reads"], a["duplicate_body_deliveries"], a["conflicting_locators"]) == (
        5000,
        4998,
        1,
    ), "duplicate delivery accounting differs"
    assert (b["successful_reads"], b["duplicate_body_deliveries"], b["conflicting_locators"]) == (
        2,
        1,
        0,
    )
    assert report["issues"] == [
        {
            "code": "LOCATOR_CONFLICT",
            "severity": "failed",
            "witness_id": _WITNESSES[0],
            "count": 1,
            "observation_ordinals": [4097],
        }
    ]
    assert (report["failed_issues"], report["incomplete_issues"], report["issues_omitted"]) == (
        1,
        0,
        0,
    )
    assert all(row["terminal_reached"] for row in report["witnesses"])
    original = hashlib.sha256(b"\x00opaque unknown\xff").hexdigest()
    conflict = hashlib.sha256(b"late contradiction").hexdigest()
    assert [row[4] for row in case["reads"]] == [
        conflict if index == 4096 else original for index in range(5002)
    ]


def _assert_cycle(case: dict[str, Any], witnesses: list[dict[str, Any]]) -> None:
    report = _assert_report(case, witnesses, "incomplete")
    assert case["list_attempts"] == 4 and len(case["reads"]) == 8
    assert report["witnesses"][1]["terminal_reached"] is False
    assert [(issue["code"], issue["witness_id"], issue["count"]) for issue in report["issues"]] == [
        ("CURSOR_CYCLE", _WITNESSES[1], 1)
    ], "cursor-cycle obligation disappeared"
    assert [page["cursor"] for page in case["pages"][1:]] == [
        [None, None],
        [_PREFIX + "cursor-a", "null"],
        [_PREFIX + "cursor-b", "null"],
    ]


def _rejection(
    check: Callable[[dict[str, Any]], None],
    value: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
    message: str,
) -> dict[str, Any]:
    corrupted = copy.deepcopy(value)
    mutate(corrupted)
    assert corrupted != value
    with pytest.raises(AssertionError, match=message):
        check(corrupted)
    return {
        "original_sha256": hashlib.sha256(_json(value)).hexdigest(),
        "mutated_sha256": hashlib.sha256(_json(corrupted)).hexdigest(),
        "rejected_by": message,
    }


def _assert_synthetic(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    _assert_runtime(result)
    cases = result["cases"]
    expected_modes = {
        "scaling",
        "boundary",
        "list-gap",
        "unavailable",
        "marker",
        "ineligible",
        "cycle",
        "wall",
        "cancel",
        "file",
        "page-budget",
        "observation-budget",
        "byte-budget",
        "worker-death",
    }
    assert set(cases) == expected_modes
    witnesses = config["witnesses"]
    _assert_scaling(cases["scaling"], witnesses)
    boundary = _assert_report(cases["boundary"], witnesses, "failed")
    assert cases["boundary"]["pages"][0]["body_bytes"] == 16_777_216
    assert len(cases["boundary"]["pages"][0]["rows"]) == 1000
    assert cases["boundary"]["reads"][0][3:] == [
        65_536,
        hashlib.sha256(bytes(range(256)) * 256).hexdigest(),
    ]
    assert [row["successful_reads"] for row in boundary["witnesses"]] == [1000, 2]
    assert [(issue["code"], issue["witness_id"]) for issue in boundary["issues"]] == [
        ("LOCATOR_CONFLICT", witness) for witness in _WITNESSES
    ]
    for mode, status, code, count in (
        ("list-gap", "incomplete", "LIST_UNAVAILABLE", 1),
        ("unavailable", "incomplete", "VERSION_UNAVAILABLE", 2),
        ("marker", "failed", "DELETE_OBSERVATION", 1),
        ("ineligible", "incomplete", "INELIGIBLE_LOCATOR", 1),
    ):
        report = _assert_report(cases[mode], witnesses, status)
        assert report["witnesses"][0]["successful_reads"] == 2
        assert report["witnesses"][1]["successful_reads"] == 0
        assert [
            (issue["code"], issue["witness_id"], issue["count"]) for issue in report["issues"]
        ] == [(code, _WITNESSES[1], count)]
    _assert_cycle(cases["cycle"], witnesses)
    for mode, outcome in (
        ("wall", "DEADLINE_EXCEEDED"),
        ("cancel", "cancelled"),
        ("file", "RESOURCE_LIMIT"),
        ("page-budget", "RESOURCE_LIMIT"),
        ("observation-budget", "RESOURCE_LIMIT"),
        ("byte-budget", "RESOURCE_LIMIT"),
        ("worker-death", "WORKER_FAILED"),
    ):
        case = cases[mode]
        assert case["outcome"] == outcome and case["report"] is None
        _assert_workers(
            case,
            file_bytes=1_048_576 if mode == "file" else 67_108_864,
            allow_startup_expiry=mode == "wall",
        )
    assert (
        cases["worker-death"]["attempted_exact_reads"] == len(cases["worker-death"]["reads"]) == 1
    )
    assert cases["worker-death"]["workers"][0]["exit_code"] == -9
    assert 900 <= cases["wall"]["elapsed_ms"] < 4000

    def drop_duplicate(item: dict[str, Any]) -> None:
        removed = item["reads"].pop(4097)
        item["attempted_exact_reads"] -= 1
        item["report"]["admitted_total_bytes"] -= removed[3]
        witness = item["report"]["witnesses"][0]
        for name in ("successful_reads", "version_observations", "duplicate_body_deliveries"):
            witness[name] -= 1

    controls = {
        "wall-cleanup-regression": _rejection(
            lambda item: _assert_workers(item, allow_startup_expiry=True),
            cases["wall"],
            lambda item: item["workers"][0].update(selector_closed=False),
            "worker cleanup was not proved",
        ),
        "missing-required-b": _rejection(
            lambda item: _assert_scaling(item, witnesses),
            cases["scaling"],
            lambda item: item["report"]["witnesses"].pop(),
            "required-witness inventory differs",
        ),
        "dropped-duplicate-count": _rejection(
            lambda item: _assert_scaling(item, witnesses),
            cases["scaling"],
            lambda item: item["report"]["witnesses"][0].update(duplicate_body_deliveries=4997),
            "duplicate delivery accounting differs",
        ),
        "dropped-duplicate-delivery": _rejection(
            lambda item: _assert_scaling(item, witnesses),
            cases["scaling"],
            drop_duplicate,
            "raw-body delivery count differs",
        ),
        "accepted-cursor-cycle": _rejection(
            lambda item: _assert_cycle(item, witnesses),
            cases["cycle"],
            lambda item: item["report"].update(issues=[]),
            "cursor-cycle obligation disappeared",
        ),
    }
    return {
        "body_observations": 5002,
        "transport_kind": "synthetic-boundary",
        "cases": cases,
        "receipt_mutation_controls": controls,
        "elapsed_ms": result["fixture_elapsed_ms"],
    }


def _assert_memory_policy(value: dict[str, Any]) -> None:
    observation = value["observation"]
    regular = observation["writable_fds"]
    assert regular and all(
        row["target"].endswith("/spool.sqlite3") and not row["deleted"] for row in regular
    ), "disk journal or temporary descriptor observed"
    assert observation["directory_entries"] == ["spool.sqlite3"]


def _assert_resources(result: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    _assert_runtime(result)
    cases = result["cases"]
    assert set(cases) == {
        "memory",
        "descriptors",
        "file-limit",
        "cpu",
        "heap",
        "store",
        "disk-journal",
        "disk-sort",
    }
    for name, case in cases.items():
        assert case["observation"]["effective"] == _LIMITS
        for field in ("reaped", "pipes_closed", "directory_removed"):
            assert case[field] is True
        assert case["exit_code"] in {-9, -24} if name == "cpu" else case["exit_code"] == 0
    assert cases["memory"]["observation"]["outcome"] == "MemoryError"
    assert cases["descriptors"]["observation"]["errno"] == 24
    assert 0 < cases["descriptors"]["observation"]["opened"] < 32
    assert cases["file-limit"]["observation"]["errno"] == 27
    assert cases["file-limit"]["observation"]["bytes"] == 65_536
    assert 119_000 <= cases["cpu"]["child_cpu_ms"] <= 125_000
    heap = cases["heap"]["observation"]
    assert heap["original_heap"] == 67_108_864 and heap["outcome"] == "RESOURCE_LIMIT"
    assert heap["poisoned"] is heap["closed"] is True
    bodies = hashlib.sha256()
    for ordinal in range(1, 5001):
        body = ordinal.to_bytes(4, "big") + b"o" * 2044
        bodies.update(ordinal.to_bytes(8, "big") + len(body).to_bytes(8, "big") + body)
    for name in ("store", "disk-journal", "disk-sort"):
        observation = cases[name]["observation"]
        assert observation["settings"] == {
            "page_size": 4096,
            "max_page_count": 16384,
            "journal_mode": "memory",
            "temp_store": 2,
            "mmap_size": 0,
            "cache_size": -1024,
            "hard_heap_limit": 67_108_864,
            "synchronous": 0,
            "trusted_schema": 0,
            "threads": 0,
            "busy_timeout": 0,
        }
        assert observation["rows"] == 5000
        assert 10_240_000 < observation["database_bytes"] <= 67_108_864
        assert observation["retained_page_sha256"] == [
            hashlib.sha256(bytes.fromhex(raw)).hexdigest() for raw in config["store_pages"]
        ]
        assert observation["retained_body_sha256"] == bodies.hexdigest()
        assert observation["denied_operations"] == 8
        assert observation["connection_count"] == 1
        assert observation["columns"]["cursors"] == [
            ["witness", "INTEGER"],
            ["cursor_key", "BLOB"],
            ["cursor_version", "BLOB"],
        ]
        assert ["key", "BLOB"] in observation["columns"]["observations"] and [
            "version",
            "BLOB",
        ] in observation["columns"]["observations"]
        assert observation["indexes"] == [
            "CREATE INDEX observation_locator ON observations(witness,key,version,digest)"
        ]
    _assert_memory_policy(cases["store"])
    for name in ("disk-journal", "disk-sort"):
        with pytest.raises(AssertionError, match="disk journal or temporary descriptor observed"):
            _assert_memory_policy(cases[name])
    assert any(
        row["target"].endswith("spool.sqlite3-journal")
        for row in cases["disk-journal"]["observation"]["writable_fds"]
    )
    assert any(row["deleted"] for row in cases["disk-sort"]["observation"]["writable_fds"])
    death = result["open_transaction_death"]
    assert death["during_bytes"] > death["before_bytes"] > 0
    assert len(death["errors"]) == 2 and death["exit_code"] == -9
    for field in ("directory_removed", "pipes_closed", "selector_closed"):
        assert death[field] is True
    cleanup = result["cleanup_failure"]
    assert "CLEANUP_FAILED" in cleanup["codes"]
    assert cleanup["removal_attempt_exit_codes"] and all(
        code == 0 for code in cleanup["removal_attempt_exit_codes"]
    )
    for field in ("directory_removed", "pipes_closed", "selector_closed"):
        assert cleanup[field] is True
    upload = result["upload_deadline"]
    assert upload["code"] == "DEADLINE_EXCEEDED"
    assert upload["chunk_attempts"] == 2
    assert upload["chunks_sent"] == upload["distinct_deadlines"] == 1
    assert upload["write_bytes_by_attempt"] == [65_553, 0]
    assert 10_000 <= upload["elapsed_ms"] < 15_000
    assert upload["directory_removed"] is upload["pipes_closed"] is True
    expected_ipc = {
        "valid-result": "accepted",
        "oversized": "PROTOCOL_INVALID",
        "truncated": "WORKER_FAILED",
        "flood": "PROTOCOL_INVALID",
        "stale": "PROTOCOL_INVALID",
        "late-output": "PROTOCOL_INVALID",
        "withheld-eof": "DEADLINE_EXCEEDED",
    }
    assert set(result["ipc"]) == set(expected_ipc)
    for name, expected in expected_ipc.items():
        case = result["ipc"][name]
        assert case["code"] == expected and case["spawn_kind"] == "adversarial-ipc-producer"
        for field in ("reaped", "pipes_closed", "selector_closed", "directory_removed"):
            assert case[field] is True
    valid_final = result["ipc"]["valid-result"]["final_frames"]
    assert len(valid_final) == 1 and valid_final[0]["id"] == 4
    assert (
        result["ipc"]["late-output"]["final_frames"]
        == result["ipc"]["withheld-eof"]["final_frames"]
        == valid_final
    )
    assert result["ipc"]["valid-result"]["exit_code"] == 0
    assert set(result["raw_worker"]) == {
        "valid-empty",
        "oversized-input",
        "truncated-input",
        "stale-input",
        "duplicate-field",
        "chunk-sequence",
        "trailing-chunk",
        "entry-over",
        "xml-over",
        "body-over",
    }
    for name, case in result["raw_worker"].items():
        for field in ("startup_elapsed_ms", "exchange_elapsed_ms"):
            assert type(case[field]) is int and case[field] >= 0
        assert (
            case["exit_code"] == (0 if name == "valid-empty" else 1)
            and case["spawn_kind"] == "production-worker"
        )
        assert case["frames"][0] == {"op": "READY", "version": 1}
        if name != "valid-empty":
            assert all("summary" not in frame for frame in case["frames"])
        for field in ("reaped", "pipes_closed", "directory_removed"):
            assert case[field] is True
        if name in {"chunk-sequence", "entry-over", "xml-over", "body-over"}:
            assert case["frames"][-1] == {
                "op": "ERROR",
                "id": 4 if name == "body-over" else 3,
                "code": "PROTOCOL_INVALID",
            }
    valid_empty = result["raw_worker"]["valid-empty"]["frames"]
    assert (
        valid_empty[-1]["op"] == "ACK"
        and valid_empty[-1]["id"] == 4
        and "summary" in valid_empty[-1]
    )
    assert result["raw_worker"]["trailing-chunk"]["frames"] == valid_empty[:-1]
    return result


def _trace_response_header(response: dict[str, Any], name: str) -> str:
    headers = response.get("headers")
    assert type(headers) is dict, "provider trace response header invalid"
    assert all(type(key) is str for key in headers), "provider trace response header invalid"
    values = [value for key, value in headers.items() if key.lower() == name.lower()]
    assert len(values) == 1, "provider trace response header invalid"
    value = values[0]
    assert type(value) is str, "provider trace response header invalid"
    return value


def _assert_provider(
    result: dict[str, Any],
    events: list[dict[str, Any]],
    expected: list[list[Any]],
    witnesses: list[dict[str, Any]],
) -> dict[str, Any]:
    _assert_runtime(result)
    case = result["provider"]
    assert Counter(tuple(row) for row in case["reads"]) == Counter(
        tuple(row) for row in expected
    ), "provider exact-read multiset differs"
    report = _assert_report(case, witnesses, "traversed")
    assert report["issues"] == []
    assert [row["successful_reads"] for row in report["witnesses"]] == [1001, 2]
    assert [row["page_attempts"] for row in report["witnesses"]] == [2, 1]
    assert [row["duplicate_body_deliveries"] for row in report["witnesses"]] == [0, 0]
    assert len(case["reads"]) == case["attempted_exact_reads"] == 1003
    assert Counter(tuple(row) for row in case["reads"]) == Counter(
        tuple(row) for row in expected
    ), "provider exact-read multiset differs"
    assert len(case["pages"]) == case["list_attempts"] == 3
    listed = [(page["bucket"], row[1], row[2]) for page in case["pages"] for row in page["rows"]]
    assert Counter(listed) == Counter(tuple(row[:3]) for row in expected)
    assert all(row[0] == "version" for page in case["pages"] for row in page["rows"])
    trace_gets = []
    trace_pages = []
    for witness in witnesses:
        access, bucket = witness["access_key"], witness["bucket"]
        selected = [
            event
            for event in events
            if f"Credential={access}/"
            in event.get("request", {}).get("headers", {}).get("Authorization", "")
        ]
        matching_pages = [page for page in case["pages"] if page["bucket"] == bucket]
        list_index = 0
        for event in selected:
            request, response = event["request"], event["response"]
            assert request["method"] == "GET" and response["statusCode"] == 200
            assert request["headers"]["Host"] == "127.0.0.1:9000"
            assert "/us-east-1/s3/aws4_request" in request["headers"]["Authorization"]
            if event["api"] == "s3.ListObjectVersions":
                page = matching_pages[list_index]
                list_index += 1
                query = (
                    f"versions&prefix={quote(_PREFIX, safe='-_.~')}&max-keys=1000&encoding-type=url"
                )
                key, version = page["cursor"]
                if key is not None:
                    query += "&key-marker=" + quote(key, safe="-_.~")
                if version is not None:
                    query += "&version-id-marker=" + quote(version, safe="-_.~")
                assert request["path"] == "/" + bucket and request["rawQuery"] == query
                original = response["body"].encode()
                assert (
                    len(original)
                    == page["body_bytes"]
                    == int(_trace_response_header(response, "Content-Length"))
                )
                assert hashlib.sha256(original).hexdigest() == page["body_sha256"]
                trace_pages.append((bucket, list_index, len(page["rows"]), page["body_sha256"]))
            else:
                assert event["api"] == "s3.GetObject", "unexpected required-reader request"
                query = parse_qs(request["rawQuery"], strict_parsing=True, keep_blank_values=True)
                assert set(query) == {"versionId"} and len(query["versionId"]) == 1, (
                    "provider trace GET query differs"
                )
                assert request["path"].startswith("/" + bucket + "/")
                key = request["path"][len(bucket) + 2 :]
                assert key == _PREFIX + "history"
                version = query["versionId"][0]
                assert _trace_response_header(response, "X-Amz-Version-Id") == version, (
                    "provider trace GET version differs"
                )
                assert type(response.get("body")) is str and response["body"] == "<BLOB>", (
                    "provider trace GET body marker differs"
                )
                length = int(_trace_response_header(response, "Content-Length"))
                trace_gets.append((bucket, key, version, length))
        assert list_index == len(matching_pages)
        cursor: list[Any] = [None, None]
        for index, page in enumerate(matching_pages):
            assert page["cursor"] == cursor
            assert page["truncated"] is (index < len(matching_pages) - 1)
            assert len(page["rows"]) == (
                1000
                if bucket == witnesses[0]["bucket"] and index == 0
                else 1
                if bucket == witnesses[0]["bucket"]
                else 2
            )
            cursor = page["next"]
        assert cursor == [None, None]
    assert Counter(trace_gets) == Counter(tuple(row[:4]) for row in expected), (
        "provider trace GET multiset differs"
    )
    assert len(trace_pages) == 3
    return {
        "required_witnesses": 2,
        "version_observations": 1003,
        "actual_exact_reads": len(trace_gets),
        "actual_list_reads": len(trace_pages),
        "trace_get_metadata_sha256": hashlib.sha256(_json(sorted(trace_gets))).hexdigest(),
        "trace_get_metadata_fields": ["bucket", "key", "version_id", "content_length"],
        "trace_get_body_marker": "<BLOB>",
        "trace_get_body_bytes_available": False,
        "pages": trace_pages,
        "traversal": case,
        "elapsed_ms": result["fixture_elapsed_ms"],
    }


def _provider_trace_controls(
    check: Callable[[dict[str, Any], list[dict[str, Any]]], Any],
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Reject fixed receipt mutations through the caller's independently scoped checker."""
    first_get = next(
        index for index, event in enumerate(events) if event.get("api") == "s3.GetObject"
    )
    response = events[first_get]["response"]
    _trace_response_header(response, "X-Amz-Version-Id")
    _trace_response_header(response, "Content-Length")
    version_header = next(key for key in response["headers"] if key.lower() == "x-amz-version-id")
    length_header = next(key for key in response["headers"] if key.lower() == "content-length")
    messages = {
        "wrong-version": "provider trace GET version differs",
        "wrong-length": "provider trace GET multiset differs",
        "missing-get": "provider trace GET multiset differs",
        "duplicate-get": "provider trace GET multiset differs",
        "wrong-body-hash": "provider exact-read multiset differs",
        "invalid-redaction": "provider trace GET body marker differs",
        "missing-redaction": "provider trace GET body marker differs",
        "non-string-redaction": "provider trace GET body marker differs",
        "missing-version-header": "provider trace response header invalid",
        "duplicate-version-header": "provider trace response header invalid",
        "non-string-version-header": "provider trace response header invalid",
        "blank-query-parameter": "provider trace GET query differs",
    }

    def check_receipt(item: dict[str, Any]) -> None:
        check(item["result"], item["events"])

    def mutate(item: dict[str, Any], mode: str) -> None:
        event = item["events"][first_get]
        changed_response = event["response"]
        headers = changed_response["headers"]
        if mode == "wrong-version":
            headers[version_header] += "-wrong"
        elif mode == "wrong-length":
            headers[length_header] = str(int(headers[length_header]) + 1)
        elif mode == "missing-get":
            item["events"].pop(first_get)
        elif mode == "duplicate-get":
            item["events"].append(copy.deepcopy(event))
        elif mode == "wrong-body-hash":
            row = item["result"]["provider"]["reads"][0]
            row[4] = ("1" if row[4][0] == "0" else "0") + row[4][1:]
        elif mode == "invalid-redaction":
            changed_response["body"] = "<BLOB >"
        elif mode == "missing-redaction":
            changed_response.pop("body")
        elif mode == "non-string-redaction":
            changed_response["body"] = ["<BLOB>"]
        elif mode == "missing-version-header":
            headers.pop(version_header)
        elif mode == "duplicate-version-header":
            headers[version_header.swapcase()] = headers[version_header]
        elif mode == "non-string-version-header":
            headers[version_header] = [headers[version_header]]
        elif mode == "blank-query-parameter":
            event["request"]["rawQuery"] += "&unexpected="
        else:
            raise AssertionError("unknown fixed provider receipt mutation")

    receipt = {"result": result, "events": events}
    return {
        mode: _rejection(
            check_receipt, receipt, lambda item, mode=mode: mutate(item, mode), message
        )
        for mode, message in messages.items()
    }


def _cleanup_content_md5(request: Any, **_kwargs: Any) -> None:
    """Add the pinned provider's checksum over the SDK's serialized delete body."""
    body = request.body
    assert type(body) is bytes
    checksum = base64.b64encode(hashlib.md5(body, usedforsecurity=False).digest()).decode("ascii")
    if "Content-MD5" in request.headers:
        del request.headers["Content-MD5"]
    request.headers["Content-MD5"] = checksum


def _provider_acceptance(
    image: _ApiImage, material: Path, labels: dict[str, str], bundle: dict[str, Any]
) -> dict[str, Any]:
    resolver = DockerClient(timeout=15)
    client = resolver.client
    admin = None
    containers = []
    buckets = []
    created: dict[str, list[dict[str, str]]] = {}
    expected: list[list[Any]] = []
    setup_started = time.monotonic()
    outcome = None
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
        assert all(
            not mount["RW"]
            or (
                mount["Type"] == "tmpfs" and mount["Destination"] in {"/data", "/tmp"}  # noqa: S108 - owned provider tmpfs
            )
            for mount in provider.attrs["Mounts"]
        )
        port = provider.attrs["NetworkSettings"]["Ports"]["9000/tcp"][0]["HostPort"]
        endpoint = _provider_endpoint(host, port)
        admin = _provider_client(endpoint, root_access, root_secret, ca_file)
        admin.meta.events.register(
            "before-sign.s3.DeleteObjects",
            _cleanup_content_md5,
            unique_id="easysynq.history-collection.cleanup-md5",
        )
        while True:
            try:
                admin.list_buckets()
                break
            except (BotoCoreError, ClientError):
                assert time.monotonic() - setup_started < 20
                time.sleep(0.2)
        for index, count in enumerate((1001, 2)):
            bucket = _create_bucket(admin, locked=False)
            buckets.append(bucket)
            created[bucket] = []
            admin.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
            for revision in range(count):
                assert time.monotonic() - setup_started < 45, "provider setup deadline exceeded"
                key = _PREFIX + "history"
                body = f"opaque witness={index} revision={revision:04d}\n".encode()
                version = admin.put_object(Bucket=bucket, Key=key, Body=body)["VersionId"]
                assert type(version) is str and version
                created[bucket].append({"Key": key, "VersionId": version})
                expected.append([bucket, key, version, len(body), hashlib.sha256(body).hexdigest()])
        assert len(set(buckets)) == 2 and len(expected) == 1003
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
            (str(mc_config), "/tmp/mc/config.json", "ro"),  # noqa: S108 - private helper configuration
            (str(ca_file), "/tmp/mc/certs/CAs/ca.pem", "ro"),  # noqa: S108 - helper read-only CA
        ]
        with _TlsMc(
            identities["mc"],
            command=["-c", "sleep 550"],
            entrypoint="/bin/sh",
            network_mode="container:" + provider.id,
            volumes=mc_mounts,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            labels={name: value for name, value in labels.items() if name != LABEL_SESSION_ID},
        ).with_tmpfs_mount("/tmp", "rw,size=8388608,mode=1777") as helper:  # noqa: S108 - owned helper tmpfs
            helper_wrapped = helper.get_wrapped_container()
            assert helper_wrapped.attrs["Image"] == identities["mc"]
            with ExitStack() as readers:
                identities_read = [
                    readers.enter_context(_runtime_reader(helper, {}, (bucket,)))
                    for bucket in buckets
                ]
                assert len({access for access, _secret in identities_read}) == 2
                assert all(
                    access != root_access and secret != root_secret
                    for access, secret in identities_read
                )
                witnesses = [
                    _witness(index, bucket, *identities_read[index])
                    for index, bucket in enumerate(buckets)
                ]
                denied_operations = []
                for index, witness in enumerate(witnesses):
                    reader = _provider_client(
                        endpoint, witness["access_key"], witness["secret_key"], ca_file
                    )
                    try:
                        for operation in ("write", "cross-list", "cross-get"):
                            with pytest.raises(ClientError) as denied:
                                if operation == "write":
                                    reader.put_object(
                                        Bucket=buckets[index],
                                        Key=_PREFIX + "denied",
                                        Body=b"denied",
                                    )
                                elif operation == "cross-list":
                                    reader.list_object_versions(
                                        Bucket=buckets[1 - index], Prefix=_PREFIX
                                    )
                                else:
                                    reader.get_object(
                                        Bucket=buckets[1 - index], **created[buckets[1 - index]][0]
                                    )
                            assert denied.value.response["Error"]["Code"] == "AccessDenied"
                            denied_operations.append("AccessDenied")
                    finally:
                        reader.close()
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
                    volumes={
                        source: {"bind": destination, "mode": mode}
                        for source, destination, mode in mc_mounts
                    },
                    tmpfs={"/tmp": "rw,size=8388608,mode=1777"},  # noqa: S108 - owned trace tmpfs
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    labels=labels,
                )
                containers.append(trace)
                trace.start()
                deadline = time.monotonic() + 10
                while True:
                    admin.list_buckets()
                    events, _pending = _trace_snapshot(trace)
                    if any(event.get("api") == "s3.ListBuckets" for event in events):
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                setup_ms = int((time.monotonic() - setup_started) * 1000)
                assert setup_ms <= 45_000, "provider setup deadline exceeded"
                result = _exec(
                    image,
                    material,
                    labels,
                    "provider",
                    {"witnesses": witnesses, "writable": _WRITABLE},
                    mounts=((combined, bundle["certifi_path"]),),
                    network_mode="container:" + provider.id,
                )
                barrier = admin.list_buckets()["ResponseMetadata"]["RequestId"]
                deadline = time.monotonic() + 10
                while True:
                    events, pending = _trace_snapshot(trace)
                    if not pending and any(
                        event.get("api") == "s3.ListBuckets"
                        and event.get("response", {}).get("headers", {}).get("X-Amz-Request-Id")
                        == barrier
                        for event in events
                    ):
                        break
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                trace.stop(timeout=3)
                trace.reload()
                assert trace.status == "exited" and trace.attrs["Image"] == identities["mc"]
                events, pending = _trace_snapshot(trace)
                assert pending == ""
                observed = _assert_provider(result, events, expected, witnesses)
                # A provider receipt with B's GET omitted must fail the exact
                # producer inventory; this is distinct from synthetic controls.
                omission = _rejection(
                    lambda item: _assert_provider(item, events, expected, witnesses),
                    result,
                    lambda item: item["provider"]["reads"].pop(),
                    "provider exact-read multiset differs",
                )
                trace_controls = _provider_trace_controls(
                    lambda item, trace_events: _assert_provider(
                        item, trace_events, expected, witnesses
                    ),
                    result,
                    events,
                )
                outcome = observed | {
                    "provider_image_id": identities["minio"],
                    "trace_image_id": identities["mc"],
                    "setup_ms": setup_ms,
                    "separate_readonly_credentials": True,
                    "denied_operations": denied_operations,
                    "omitted_get_receipt_control": omission,
                    "trace_receipt_controls": trace_controls,
                }
    finally:
        failures = []
        if admin is not None:
            for bucket in reversed(buckets):
                try:
                    references = created[bucket]
                    for offset in range(0, len(references), 1000):
                        response = admin.delete_objects(
                            Bucket=bucket,
                            Delete={"Objects": references[offset : offset + 1000], "Quiet": True},
                        )
                        assert not response.get("Errors")
                    admin.delete_bucket(Bucket=bucket)
                except Exception as error:  # noqa: BLE001 - attempt each owned bucket cleanup
                    failures.append(error)
            try:
                admin.close()
            except Exception as error:  # noqa: BLE001 - preserve cleanup failure
                failures.append(error)
        for container in reversed(containers):
            try:
                container.remove(force=True, v=True)
            except Exception as error:  # noqa: BLE001 - attempt every owned container
                failures.append(error)
        try:
            assert (
                client.containers.list(
                    all=True, filters={"label": _FIXTURE_LABEL + "=" + labels[_FIXTURE_LABEL]}
                )
                == []
            )
        except Exception as error:  # noqa: BLE001 - independently verify owned cleanup
            failures.append(error)
        finally:
            client.close()
        if failures:
            raise ExceptionGroup("collection provider cleanup failed", failures)
    assert outcome is not None
    return outcome | {"all_owned_provider_resources_removed": True}


def test_history_collection_runtime_preserves_required_witnesses_and_resource_boundaries(
    _api_image: _ApiImage, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture_id = uuid.uuid4().hex
    labels = {
        _RUN_LABEL: os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"],
        LABEL_SESSION_ID: SESSION_ID,
        _FIXTURE_LABEL: fixture_id,
    }
    material = _owned_parent() / (fixture_id + "-history-collection")
    material.mkdir(mode=0o700)
    material.chmod(0o700)
    started = time.monotonic()
    phase = "identity"
    phase_started = started
    timings = {}
    resource_subcase_timings = {}
    client = DockerClient(timeout=15).client
    try:
        record = json.loads((_owned_parent() / "runner.json").read_text())
        assert record["run_id"] == labels[_RUN_LABEL] and record["image_id"] == _api_image.image_id
        image = client.images.get(_api_image.image_id)
        assert image.attrs["Config"]["Labels"][_SOURCE_LABEL] == record["build_input_sha256"]
        assert image.attrs["Config"]["Labels"][_RUN_LABEL] == labels[_RUN_LABEL]
        config = _synthetic_config()
        phase, phase_started = "synthetic", time.monotonic()
        synthetic = _assert_synthetic(
            _exec(_api_image, material, labels, "synthetic", config), config
        )
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "resources", time.monotonic()
        resource_result = _exec(_api_image, material, labels, "resources", config)
        resource_subcase_timings = resource_result["subcase_timings_ms"]
        resources = _assert_resources(resource_result, config)
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "certifi", time.monotonic()
        bundle = _exec(_api_image, material, labels, "certifi", {})
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
        phase, phase_started = "provider-budget", time.monotonic()
        assert phase_started - started + (45 + 460 + 40) <= 780, (
            "provider phase budget unavailable: required_seconds=545 total_seconds=780"
        )
        phase, phase_started = "provider", time.monotonic()
        provider = _provider_acceptance(_api_image, material, labels, bundle)
        timings[phase] = int((time.monotonic() - phase_started) * 1000)
    except BaseException:
        with capsys.disabled():
            print(
                _FAILURE_PREFIX
                + _json(
                    {
                        "phase": phase,
                        "phase_elapsed_ms": int((time.monotonic() - phase_started) * 1000),
                        "total_elapsed_ms": int((time.monotonic() - started) * 1000),
                        "completed_phases_ms": timings,
                        "resource_subcases_ms": resource_subcase_timings,
                    }
                ).decode()
            )
        raise
    finally:
        # This label belongs to this fixture only. Context managers normally
        # remove these; the final sweep also owns partial setup failures.
        failures = []
        try:
            for container in client.containers.list(
                all=True, filters={"label": _FIXTURE_LABEL + "=" + fixture_id}
            ):
                try:
                    container.remove(force=True, v=True)
                except Exception as error:  # noqa: BLE001 - finish every owned cleanup
                    failures.append(error)
            assert (
                client.containers.list(
                    all=True, filters={"label": _FIXTURE_LABEL + "=" + fixture_id}
                )
                == []
            )
        except Exception as error:  # noqa: BLE001 - independent inventory failure is fatal
            failures.append(error)
        finally:
            client.close()
            shutil.rmtree(material)
        if failures:
            raise ExceptionGroup("collection fixture cleanup failed", failures)
    assert not material.exists()
    elapsed = int((time.monotonic() - started) * 1000)
    assert elapsed <= 780_000, "collection acceptance allocation exceeded"
    proof = {
        "scope": "required-witness-provider-traversal",
        "source_sha256": record["build_input_sha256"],
        "proof_input_sha256": record["proof_input_sha256"],
        "commit": record["commit"],
        "application_image_id": _api_image.image_id,
        "run_id": record["run_id"],
        "fixture_id": fixture_id,
        "scaling": synthetic,
        "provider": provider,
        "resources": resources,
        "timings_ms": timings,
        "elapsed_ms": elapsed,
        "all_owned_resources_removed": True,
    }
    with capsys.disabled():
        print(_PROOF_PREFIX + _json(proof).decode())
