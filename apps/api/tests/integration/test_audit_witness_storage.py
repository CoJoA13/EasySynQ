"""Pinned-MinIO proofs for retained audit-witness versions and restricted reader IAM."""

from __future__ import annotations

import base64
import copy
import datetime
import json
import re
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from testcontainers.core.container import DockerContainer

from easysynq_api.services.audit import checkpoint as checkpoint_service
from easysynq_api.services.audit import sink as sink_service
from easysynq_api.services.vault import storage as vault_storage

pytestmark = pytest.mark.integration

_BUCKET = "audit-checkpoints"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _locked_mc_image() -> str:
    for line in (_repo_root() / "infra/images.lock").read_text().splitlines():
        fields = line.split()
        if fields and fields[0] == "mc":
            return fields[1]
    raise AssertionError("mc image is absent from infra/images.lock")


def _policy_from_shipped_heredoc(filename: str) -> dict[str, Any]:
    source = (_repo_root() / "infra/compose/minio/minio-init.sh").read_text()
    match = re.search(
        rf"cat > /tmp/{re.escape(filename)} <<'EOF'\n(.*?)\nEOF",
        source,
        re.DOTALL,
    )
    assert match is not None
    policy = json.loads(match.group(1))
    assert isinstance(policy, dict)
    return policy


def _without_actions(policy: dict[str, Any], *actions: str) -> dict[str, Any]:
    narrowed = copy.deepcopy(policy)
    allowed = narrowed["Statement"][0]["Action"]
    for action in actions:
        allowed.remove(action)
    return narrowed


def _with_explicit_deny(policy: dict[str, Any], action: str) -> dict[str, Any]:
    denied = copy.deepcopy(policy)
    denied["Statement"].append(
        {
            "Effect": "Deny",
            "Action": [action],
            "Resource": copy.deepcopy(policy["Statement"][0]["Resource"]),
        }
    )
    return denied


def _mc_exec(container: DockerContainer, argv: list[str]) -> None:
    result = container.exec(argv)
    if result.exit_code != 0:
        raise AssertionError(f"mc command failed: {' '.join(argv[:4])}")


@pytest.fixture
def _mc(_minio: dict[str, str]) -> Iterator[DockerContainer]:
    helper = DockerContainer(
        _locked_mc_image(),
        command=["-c", "sleep 3600"],
        entrypoint="/bin/sh",
        network_mode=f"container:{_minio['container_id']}",
    )
    with helper as container:
        labels = container.get_wrapped_container().attrs["Config"]["Labels"]
        assert labels["org.testcontainers"] == "true"
        assert labels["org.testcontainers.session-id"]
        _mc_exec(
            container,
            [
                "mc",
                "alias",
                "set",
                "local",
                "http://127.0.0.1:9000",
                _minio["access_key"],
                _minio["secret_key"],
            ],
        )
        yield container


def _s3(_minio: dict[str, str], access_key: str, secret_key: str) -> Any:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=_minio["endpoint"],
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    vault_storage._register_retention_md5(client)
    return client


@pytest.fixture
def _admin_s3(_minio: dict[str, str]) -> Iterator[Any]:
    client = _s3(_minio, _minio["access_key"], _minio["secret_key"])
    try:
        yield client
    finally:
        client.close()


def _provision(
    container: DockerContainer,
    *,
    username: str,
    secret: str,
    policy_name: str,
    policy: dict[str, Any],
    created_users: list[str],
    created_policies: list[str],
) -> None:
    destination = f"/tmp/{policy_name}.json"  # noqa: S108 - path is inside disposable mc
    container.copy_into_container(json.dumps(policy).encode(), destination)
    _mc_exec(container, ["mc", "admin", "user", "add", "local", username, secret])
    created_users.append(username)
    _mc_exec(
        container,
        ["mc", "admin", "policy", "create", "local", policy_name, destination],
    )
    created_policies.append(policy_name)
    _mc_exec(
        container,
        ["mc", "admin", "policy", "attach", "local", policy_name, "--user", username],
    )


def _signed_body(
    key: Ed25519PrivateKey,
    org_id: uuid.UUID,
    latest_id: int,
    row_hash: bytes,
    timestamp: datetime.datetime,
) -> bytes:
    payload = checkpoint_service._payload(org_id, latest_id, row_hash, timestamp)
    return json.dumps(
        {
            "checkpoint": json.loads(payload),
            "signature": base64.b64encode(key.sign(payload)).decode(),
        }
    ).encode()


def _read_bytes(client: Any, **kwargs: Any) -> bytes:
    response = client.get_object(**kwargs)
    body = response["Body"]
    try:
        return bytes(body.read())
    finally:
        body.close()


def _assert_denied(call: Callable[[], Any]) -> None:
    with pytest.raises(ClientError) as exc:
        call()
    assert exc.value.response["Error"]["Code"] == "AccessDenied"
    assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403


def _assert_retention_protected(call: Callable[[], Any]) -> None:
    with pytest.raises(ClientError) as exc:
        call()
    assert exc.value.response["Error"]["Code"] == "InvalidRequest"
    assert exc.value.response["Error"]["Message"] == (
        "Object is WORM protected and cannot be overwritten"
    )
    assert exc.value.response["ResponseMetadata"]["HTTPStatusCode"] == 400


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _SinkResult:
    def __init__(self, configured: Any) -> None:
        self.configured = configured

    def scalars(self) -> _SinkResult:
        return self

    def all(self) -> list[Any]:
        return [self.configured]


class _Session:
    def __init__(self, configured: Any, stored: dict[int, bytes]) -> None:
        self.configured = configured
        self.stored = stored
        self.calls = 0

    async def execute(self, statement: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            return _SinkResult(self.configured)
        latest_id = next(
            value for value in statement.compile().params.values() if type(value) is int
        )
        return _ScalarResult(self.stored.get(latest_id))


class _RecordingClient:
    def __init__(
        self,
        client: Any,
        *,
        lists: list[dict[str, Any]],
        returned: list[tuple[str | None, str | None]],
        gets: list[dict[str, Any]],
        force_one: bool,
    ) -> None:
        self.client = client
        self.lists = lists
        self.returned = returned
        self.gets = gets
        self.force_one = force_one
        self.closed = False

    def list_object_versions(self, **kwargs: Any) -> dict[str, Any]:
        if self.force_one:
            kwargs["MaxKeys"] = 1
        self.lists.append(dict(kwargs))
        page = self.client.list_object_versions(**kwargs)
        self.returned.append((page.get("NextKeyMarker"), page.get("NextVersionIdMarker")))
        return page

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        assert "VersionId" in kwargs
        self.gets.append(dict(kwargs))
        return self.client.get_object(**kwargs)

    def list_objects_v2(self, **_kwargs: Any) -> None:
        raise AssertionError("version verification fell back to current-object listing")

    def get_paginator(self, _operation: str) -> None:
        raise AssertionError("version verification fell back to a current-object paginator")

    def close(self) -> None:
        self.closed = True


async def test_pinned_minio_versions_markers_and_shipped_restricted_policies(
    app_under_test: Any,
    _minio: dict[str, str],
    _mc: DockerContainer,
    _admin_s3: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert app_under_test is not None
    admin = _admin_s3
    salt = uuid.uuid4().hex
    org_id = uuid.uuid4()
    key_name = f"checkpoints/{org_id}/7-{salt}.json"
    signing_key = Ed25519PrivateKey.generate()
    row_hash = b"\xa5" * 32
    now = datetime.datetime.now(datetime.UTC)
    body_a = _signed_body(signing_key, org_id, 7, row_hash, now - datetime.timedelta(minutes=5))
    body_b = _signed_body(signing_key, org_id, 7, row_hash, now)
    retained = admin.put_object(
        Bucket=_BUCKET,
        Key=key_name,
        Body=body_a,
        ObjectLockMode="GOVERNANCE",
        ObjectLockRetainUntilDate=now + datetime.timedelta(days=1),
    )["VersionId"]
    shadow = admin.put_object(Bucket=_BUCKET, Key=key_name, Body=body_b)["VersionId"]
    assert retained != shadow
    assert _read_bytes(admin, Bucket=_BUCKET, Key=key_name) == body_b
    extended_until = now + datetime.timedelta(days=2)
    extended = admin.put_object_retention(
        Bucket=_BUCKET,
        Key=key_name,
        VersionId=retained,
        Retention={"Mode": "GOVERNANCE", "RetainUntilDate": extended_until},
    )
    assert extended["ResponseMetadata"]["HTTPStatusCode"] == 200
    assert _read_bytes(admin, Bucket=_BUCKET, Key=key_name, VersionId=retained) == body_a
    _assert_retention_protected(
        lambda: admin.delete_object(Bucket=_BUCKET, Key=key_name, VersionId=retained)
    )
    assert _read_bytes(admin, Bucket=_BUCKET, Key=key_name, VersionId=retained) == body_a
    configured = SimpleNamespace(
        id="provider-sink",
        kind=SimpleNamespace(value="worm_bucket"),
        connection={"bucket": _BUCKET, "off_host": True},
        last_anchored_at=now,
        enabled_at=now,
    )
    reader_policy = _policy_from_shipped_heredoc("audit-sink-readonly.json")
    writer_policy = _policy_from_shipped_heredoc("audit-sink-writeonly.json")
    policies = {
        "reader": reader_policy,
        "omit-list-version": _without_actions(reader_policy, "s3:ListBucketVersions"),
        "omit-get-version": _without_actions(reader_policy, "s3:GetObjectVersion"),
        "no-list-any": _without_actions(reader_policy, "s3:ListBucketVersions", "s3:ListBucket"),
        "no-get-any": _without_actions(reader_policy, "s3:GetObjectVersion", "s3:GetObject"),
        "version-only": _without_actions(reader_policy, "s3:ListBucket", "s3:GetObject"),
        "deny-list-version": _with_explicit_deny(reader_policy, "s3:ListBucketVersions"),
        "deny-get-version": _with_explicit_deny(reader_policy, "s3:GetObjectVersion"),
        "writer": writer_policy,
    }
    credentials: dict[str, tuple[str, str, str]] = {}
    created_users: list[str] = []
    created_policies: list[str] = []
    clients: list[Any] = []
    try:
        for role, policy in policies.items():
            username = f"hist-{role}-{salt[:8]}"
            secret = f"synthetic-{uuid.uuid4().hex}"
            policy_name = f"hist-{role}-{salt[8:16]}"
            _provision(
                _mc,
                username=username,
                secret=secret,
                policy_name=policy_name,
                policy=policy,
                created_users=created_users,
                created_policies=created_policies,
            )
            credentials[role] = (username, secret, policy_name)

        reader = _s3(_minio, *credentials["reader"][:2])
        clients.append(reader)
        listed = reader.list_object_versions(Bucket=_BUCKET, Prefix=key_name)
        listed_ids = {version["VersionId"] for version in listed.get("Versions", [])}
        assert {retained, shadow} <= listed_ids
        assert _read_bytes(reader, Bucket=_BUCKET, Key=key_name, VersionId=retained) == body_a

        list_calls: list[dict[str, Any]] = []
        returned_markers: list[tuple[str | None, str | None]] = []
        get_calls: list[dict[str, Any]] = []
        wrappers: list[_RecordingClient] = []

        def reader_factory(_connection: dict[str, Any] | None) -> _RecordingClient:
            wrapper = _RecordingClient(
                reader,
                lists=list_calls,
                returned=returned_markers,
                gets=get_calls,
                force_one=True,
            )
            wrappers.append(wrapper)
            return wrapper

        monkeypatch.setattr(sink_service, "_audit_history_read_client", reader_factory)
        healthy = await checkpoint_service.verify_offhost_checkpoint(
            _Session(configured, {7: row_hash}),
            org_id,
            verify_key=signing_key.public_key(),
            now=now,
        )
        assert healthy.verified is True
        assert healthy.read_failed is False
        assert healthy.attest_failures == 0
        assert {call["VersionId"] for call in get_calls} == {retained, shadow}
        assert all(wrapper.closed for wrapper in wrappers)
        for index, request in enumerate(list_calls[1:], start=1):
            previous = returned_markers[index - 1]
            assert request["KeyMarker"] == previous[0]
            if previous[1] is None:
                assert "VersionIdMarker" not in request
            else:
                assert request["VersionIdMarker"] == previous[1]

        marker = admin.delete_object(Bucket=_BUCKET, Key=key_name)
        assert marker["DeleteMarker"] is True
        list_calls.clear()
        returned_markers.clear()
        get_calls.clear()
        wrappers.clear()
        marked = await checkpoint_service.verify_offhost_checkpoint(
            _Session(configured, {7: row_hash}),
            org_id,
            verify_key=signing_key.public_key(),
            now=now,
        )
        assert marked.verified is False
        assert marked.read_failed is False
        assert marked.sinks_read == 1
        assert marked.attest_failures == 1
        assert any("delete marker" in reason for reason in marked.reasons)
        assert {call["VersionId"] for call in get_calls} == {retained, shadow}
        _assert_denied(lambda: reader.put_object(Bucket=_BUCKET, Key=key_name, Body=b"x"))
        _assert_denied(lambda: reader.delete_object(Bucket=_BUCKET, Key=key_name))
        _assert_denied(
            lambda: reader.delete_object(Bucket=_BUCKET, Key=key_name, VersionId=retained)
        )
        _assert_denied(
            lambda: reader.put_object_retention(
                Bucket=_BUCKET,
                Key=key_name,
                VersionId=retained,
                Retention={
                    "Mode": "GOVERNANCE",
                    "RetainUntilDate": now + datetime.timedelta(days=3),
                },
            )
        )
        _assert_denied(
            lambda: reader.delete_object(
                Bucket=_BUCKET,
                Key=key_name,
                VersionId=retained,
                BypassGovernanceRetention=True,
            )
        )

        async def verify_with(client: Any) -> checkpoint_service.OffHostCheckpointResult:
            owned_wrappers: list[_RecordingClient] = []

            def factory(_connection: dict[str, Any] | None) -> _RecordingClient:
                wrapper = _RecordingClient(client, lists=[], returned=[], gets=[], force_one=False)
                owned_wrappers.append(wrapper)
                return wrapper

            monkeypatch.setattr(sink_service, "_audit_history_read_client", factory)
            outcome = await checkpoint_service.verify_offhost_checkpoint(
                _Session(configured, {7: row_hash}),
                org_id,
                verify_key=signing_key.public_key(),
                now=now,
            )
            assert owned_wrappers and all(wrapper.closed for wrapper in owned_wrappers)
            return outcome

        compatible_roles = (
            "omit-list-version",
            "omit-get-version",
            "version-only",
            "deny-list-version",
        )
        for role in compatible_roles:
            compatible = _s3(_minio, *credentials[role][:2])
            clients.append(compatible)
            page = compatible.list_object_versions(Bucket=_BUCKET, Prefix=key_name)
            assert page["ResponseMetadata"]["HTTPStatusCode"] == 200
            assert {retained, shadow} <= {
                version["VersionId"] for version in page.get("Versions", [])
            }
            assert (
                _read_bytes(
                    compatible,
                    Bucket=_BUCKET,
                    Key=key_name,
                    VersionId=retained,
                )
                == body_a
            )
            compatible_result = await verify_with(compatible)
            assert compatible_result.verified is False
            assert compatible_result.read_failed is False
            assert compatible_result.sinks_read == 1
            assert compatible_result.attest_failures == 1
            assert any("delete marker" in reason for reason in compatible_result.reasons)

        no_list = _s3(_minio, *credentials["no-list-any"][:2])
        clients.append(no_list)
        _assert_denied(lambda: no_list.list_object_versions(Bucket=_BUCKET, Prefix=key_name))
        no_list_result = await verify_with(no_list)
        assert no_list_result.verified is False
        assert no_list_result.read_failed is True
        assert no_list_result.sinks_read == 0
        assert no_list_result.attest_failures == 0
        assert any("version listing failed" in reason for reason in no_list_result.reasons)

        for role in ("no-get-any", "deny-get-version"):
            no_get = _s3(_minio, *credentials[role][:2])
            clients.append(no_get)
            page = no_get.list_object_versions(Bucket=_BUCKET, Prefix=key_name)
            assert page["ResponseMetadata"]["HTTPStatusCode"] == 200
            _assert_denied(
                lambda no_get=no_get: no_get.get_object(
                    Bucket=_BUCKET, Key=key_name, VersionId=retained
                )
            )
            no_get_result = await verify_with(no_get)
            assert no_get_result.verified is False
            assert no_get_result.read_failed is True
            assert no_get_result.sinks_read == 0
            assert no_get_result.attest_failures == 1
            assert any("version read failed" in reason for reason in no_get_result.reasons)
            assert any("delete marker" in reason for reason in no_get_result.reasons)

        writer = _s3(_minio, *credentials["writer"][:2])
        clients.append(writer)
        _assert_denied(lambda: writer.get_object(Bucket=_BUCKET, Key=key_name, VersionId=retained))
        _assert_denied(
            lambda: writer.delete_object(Bucket=_BUCKET, Key=key_name, VersionId=retained)
        )
        _assert_denied(
            lambda: writer.put_object_retention(
                Bucket=_BUCKET,
                Key=key_name,
                VersionId=retained,
                Retention={
                    "Mode": "GOVERNANCE",
                    "RetainUntilDate": now + datetime.timedelta(days=3),
                },
            )
        )
    finally:
        cleanup_errors: list[Exception] = []
        for client in clients:
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001 - preserve later cleanup attempts
                cleanup_errors.append(exc)
        for username in reversed(created_users):
            try:
                _mc_exec(_mc, ["mc", "admin", "user", "rm", "local", username])
            except Exception as exc:  # noqa: BLE001 - clean every exact owned resource
                cleanup_errors.append(exc)
        for policy_name in reversed(created_policies):
            try:
                _mc_exec(_mc, ["mc", "admin", "policy", "rm", "local", policy_name])
            except Exception as exc:  # noqa: BLE001 - clean every exact owned resource
                cleanup_errors.append(exc)
        if cleanup_errors:
            raise cleanup_errors[0]
