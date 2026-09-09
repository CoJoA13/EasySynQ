from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

import boto3
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy.ext import asyncio as sqlalchemy_asyncio

from easysynq_api import config
from easysynq_api.services.audit import checkpoint as checkpoint_service
from easysynq_api.services.audit import trust

pytestmark = pytest.mark.unit

_DESCRIPTOR_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_ORG_ID = "11111111-1111-4111-8111-11111111111a"
_WITNESS_ID = "22222222-2222-4222-8222-22222222222b"
_TS = datetime.datetime(2026, 9, 8, 12, 0, tzinfo=datetime.UTC)
_HASH = b"\xa5" * 32


def _key(seed: int = 1) -> tuple[Ed25519PrivateKey, dict[str, str]]:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return private_key, {
        "key_id": "ed25519-sha256:" + hashlib.sha256(raw).hexdigest(),
        "public_key": base64.b64encode(raw).decode("ascii"),
    }


def _descriptor() -> dict[str, Any]:
    _, public_key = _key()
    return {
        "format_version": 1,
        "descriptor_id": _DESCRIPTOR_ID,
        "organizations": [
            {
                "org_id": _ORG_ID,
                "public_keys": [public_key],
                "witnesses": [
                    {
                        "witness_id": _WITNESS_ID,
                        "kind": "worm_bucket",
                        "endpoint": "https://WITNESS.example.test/",
                        "bucket": "audit.checkpoints",
                        "region": "us-central-1",
                    }
                ],
            }
        ],
    }


def _organization(index: int, *, key_count: int = 1, witness_count: int = 1) -> dict[str, Any]:
    return {
        "org_id": str(uuid.UUID(int=1_000 + index)),
        "public_keys": [_key(seed)[1] for seed in range(1, key_count + 1)],
        "witnesses": [
            {
                "witness_id": str(uuid.UUID(int=10_000 + index * 10 + witness)),
                "kind": "worm_bucket",
                "endpoint": "https://witness.example.test",
                "bucket": "audit.checkpoints",
                "region": "us-central-1",
            }
            for witness in range(witness_count)
        ],
    }


def _write(path: Path, descriptor: object, *, size: int | None = None) -> bytes:
    body = json.dumps(descriptor, separators=(",", ":")).encode("utf-8")
    if size is not None:
        assert len(body) <= size
        body += b" " * (size - len(body))
    path.write_bytes(body)
    path.chmod(0o600)
    return body


def test_descriptor_parses_exact_bytes_into_immutable_typed_enrollment(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    body = _write(path, _descriptor())

    parsed = trust.load_trust_descriptor(path)

    private_key, key_doc = _key()
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert parsed.descriptor_id == uuid.UUID(_DESCRIPTOR_ID)
    assert parsed.sha256 == hashlib.sha256(body).hexdigest()
    assert parsed.organizations[0].org_id == uuid.UUID(_ORG_ID)
    assert parsed.organizations[0].public_keys[0].key_id == key_doc["key_id"]
    assert (
        parsed.organizations[0]
        .public_keys[0]
        .public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
        == raw
    )
    assert parsed.organizations[0].witnesses[0] == trust.TrustedWitness(
        witness_id=uuid.UUID(_WITNESS_ID),
        kind="worm_bucket",
        endpoint="https://witness.example.test",
        bucket="audit.checkpoints",
        region="us-central-1",
    )
    with pytest.raises((AttributeError, TypeError)):
        parsed.sha256 = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(("size", "accepted"), [(65_536, True), (65_537, False)])
def test_descriptor_file_size_boundary(tmp_path: Path, size: int, accepted: bool) -> None:
    path = tmp_path / "trust.json"
    _write(path, _descriptor(), size=size)

    if accepted:
        assert trust.load_trust_descriptor(path).descriptor_id == uuid.UUID(_DESCRIPTOR_ID)
    else:
        with pytest.raises(ValueError, match="descriptor"):
            trust.load_trust_descriptor(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc.__setitem__("format_version", True),
        lambda doc: doc.__setitem__("format_version", 2),
        lambda doc: doc.__setitem__("descriptor_id", _DESCRIPTOR_ID.upper()),
        lambda doc: doc.__setitem__("extra", "rejected"),
        lambda doc: doc.pop("organizations"),
        lambda doc: doc.__setitem__("organizations", []),
        lambda doc: doc["organizations"][0].__setitem__("extra", "rejected"),
        lambda doc: doc["organizations"][0].__setitem__("org_id", _ORG_ID.upper()),
        lambda doc: doc["organizations"][0].__setitem__("public_keys", []),
        lambda doc: doc["organizations"][0]["public_keys"][0].pop("key_id"),
        lambda doc: doc["organizations"][0].__setitem__("witnesses", []),
        lambda doc: doc["organizations"][0]["witnesses"][0].__setitem__(
            "witness_id", _WITNESS_ID.upper()
        ),
        lambda doc: doc["organizations"][0]["witnesses"][0].__setitem__("extra", "rejected"),
        lambda doc: doc["organizations"][0]["witnesses"][0].pop("bucket"),
    ],
    ids=[
        "boolean-version",
        "unsupported-version",
        "noncanonical-uuid",
        "root-unknown",
        "root-missing",
        "zero-orgs",
        "org-unknown",
        "org-noncanonical-uuid",
        "zero-keys",
        "key-missing",
        "zero-witnesses",
        "witness-noncanonical-uuid",
        "witness-unknown",
        "witness-missing",
    ],
)
def test_descriptor_rejects_frozen_shape_and_count_boundaries(tmp_path: Path, mutate: Any) -> None:
    descriptor = _descriptor()
    mutate(descriptor)
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)


@pytest.mark.parametrize(
    ("organization_count", "key_count", "witness_count", "accepted"),
    [
        (16, 1, 1, True),
        (17, 1, 1, False),
        (1, 8, 1, True),
        (1, 9, 1, False),
        (1, 1, 4, True),
        (1, 1, 5, False),
    ],
    ids=[
        "sixteen-orgs",
        "seventeen-orgs",
        "eight-keys",
        "nine-keys",
        "four-witnesses",
        "five-witnesses",
    ],
)
def test_descriptor_count_maxima_use_unique_otherwise_valid_entries(
    tmp_path: Path,
    organization_count: int,
    key_count: int,
    witness_count: int,
    accepted: bool,
) -> None:
    descriptor = _descriptor()
    descriptor["organizations"] = [
        _organization(index, key_count=key_count, witness_count=witness_count)
        for index in range(organization_count)
    ]
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    if accepted:
        assert len(trust.load_trust_descriptor(path).organizations) == organization_count
    else:
        with pytest.raises(ValueError, match="descriptor"):
            trust.load_trust_descriptor(path)


def test_descriptor_rejects_duplicate_identities_within_their_scopes(tmp_path: Path) -> None:
    duplicate_org = _descriptor()
    duplicate_org["organizations"].append(dict(duplicate_org["organizations"][0]))
    duplicate_witness = _descriptor()
    duplicate_witness["organizations"].append(
        {
            **duplicate_witness["organizations"][0],
            "org_id": "33333333-3333-4333-8333-333333333333",
        }
    )
    duplicate_key = _descriptor()
    duplicate_key["organizations"][0]["public_keys"] *= 2

    for index, descriptor in enumerate((duplicate_org, duplicate_witness, duplicate_key)):
        path = tmp_path / f"duplicate-{index}.json"
        _write(path, descriptor)
        with pytest.raises(ValueError, match="descriptor"):
            trust.load_trust_descriptor(path)


def test_descriptor_allows_same_legacy_public_key_in_different_orgs(tmp_path: Path) -> None:
    descriptor = _descriptor()
    descriptor["organizations"].append(
        {
            **descriptor["organizations"][0],
            "org_id": "33333333-3333-4333-8333-333333333333",
            "witnesses": [
                {
                    **descriptor["organizations"][0]["witnesses"][0],
                    "witness_id": "44444444-4444-4444-8444-444444444444",
                }
            ],
        }
    )
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    parsed = trust.load_trust_descriptor(path)

    assert len(parsed.organizations) == 2
    assert (
        parsed.organizations[0].public_keys[0].key_id
        == parsed.organizations[1].public_keys[0].key_id
    )


@pytest.mark.parametrize(
    "raw",
    [
        '{"format_version":1,"format_version":1}',
        (
            '{"format_version":1,"descriptor_id":"'
            + _DESCRIPTOR_ID
            + '","organizations":[{"org_id":"'
            + _ORG_ID
            + '","org_id":"'
            + _ORG_ID
            + '"}]}'
        ),
        '{"format_version":NaN}',
        '{"format_version":Infinity}',
        "\xff",
    ],
)
def test_descriptor_rejects_duplicate_members_nonfinite_numbers_and_invalid_utf8(
    tmp_path: Path, raw: str
) -> None:
    path = tmp_path / "trust.json"
    path.write_bytes(raw.encode("latin-1"))
    path.chmod(0o600)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda key: key.__setitem__("extra", "rejected"),
        lambda key: key.__setitem__("key_id", key["key_id"].upper()),
        lambda key: key.__setitem__("key_id", "ed25519-sha256:" + "0" * 64),
        lambda key: key.__setitem__("public_key", key["public_key"].rstrip("=")),
        lambda key: key.__setitem__("public_key", base64.b64encode(b"short").decode("ascii")),
        lambda key: key.__setitem__("public_key", "not-base64!"),
    ],
)
def test_descriptor_rejects_invalid_key_identity_and_encoding(tmp_path: Path, mutate: Any) -> None:
    descriptor = _descriptor()
    mutate(descriptor["organizations"][0]["public_keys"][0])
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)


@pytest.mark.parametrize(
    ("endpoint", "normalized"),
    [
        ("https://EXAMPLE.test", "https://example.test"),
        ("https://192.0.2.4:9443/", "https://192.0.2.4:9443"),
        ("https://[2001:db8::1]/", "https://[2001:db8::1]"),
        ("http://127.12.3.4:9000/", "http://127.12.3.4:9000"),
        ("http://[::1]:9000", "http://[::1]:9000"),
    ],
)
def test_descriptor_accepts_and_normalizes_explicit_endpoints(
    tmp_path: Path, endpoint: str, normalized: str
) -> None:
    descriptor = _descriptor()
    descriptor["organizations"][0]["witnesses"][0]["endpoint"] = endpoint
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    assert trust.load_trust_descriptor(path).organizations[0].witnesses[0].endpoint == normalized


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.test",
        "http://localhost:9000",
        "https://user:password@example.test",
        "https://example.test/path",
        "https://example.test?query=1",
        "https://example.test#fragment",
        "https://exam%70le.test",
        "https://example.test\\other",
        "https://example.test:99999",
        "https://example.test:0",
        "https://example.test:",
        "https://example.test?",
        "https://example.test#",
        "https://[2001:db8::1%25eth0]",
        "https://éxample.test",
        " https://example.test",
    ],
)
def test_descriptor_rejects_ambiguous_or_unsafe_endpoints(tmp_path: Path, endpoint: str) -> None:
    descriptor = _descriptor()
    descriptor["organizations"][0]["witnesses"][0]["endpoint"] = endpoint
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "ordinary_bucket"),
        ("bucket", "ab"),
        ("bucket", "Uppercase"),
        ("bucket", "192.0.2.1"),
        ("bucket", "two..dots"),
        ("region", ""),
        ("region", "-central"),
        ("region", "region_with_underscore"),
    ],
)
def test_descriptor_rejects_invalid_witness_fields(tmp_path: Path, field: str, value: str) -> None:
    descriptor = _descriptor()
    descriptor["organizations"][0]["witnesses"][0][field] = value
    path = tmp_path / "trust.json"
    _write(path, descriptor)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)


def test_descriptor_rejects_relative_symlink_and_writable_paths(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    _write(target, _descriptor())
    symlink = tmp_path / "link.json"
    symlink.symlink_to(target)
    writable = tmp_path / "writable.json"
    _write(writable, _descriptor())
    writable.chmod(0o620)
    world_writable = tmp_path / "world-writable.json"
    _write(world_writable, _descriptor())
    world_writable.chmod(0o602)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(Path("relative.json"))
    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(symlink)
    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(writable)
    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(world_writable)


def test_descriptor_rejects_fifo_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "trust.fifo"
    os.mkfifo(fifo, 0o600)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(fifo)


def test_deeply_nested_json_is_a_controlled_descriptor_failure(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    raw = '{"nested":' + "[" * 2_000 + "0" + "]" * 2_000 + "}"
    path.write_text(raw, encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match=r"^invalid trust descriptor$"):
        trust.load_trust_descriptor(path)


def test_oversize_failure_reads_one_open_file_descriptor_without_echoing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "sensitive-pathname.json"
    path.write_bytes(b"sensitive-body" + b" " * 65_537)
    path.chmod(0o600)
    actual_open = os.open
    opened: list[int] = []

    def tracked_open(supplied_path: object, flags: int) -> int:
        fd = actual_open(supplied_path, flags)
        opened.append(fd)
        return fd

    monkeypatch.setattr(trust.os, "open", tracked_open)

    with pytest.raises(ValueError) as caught:
        trust.load_trust_descriptor(path)

    assert len(opened) == 1
    assert str(caught.value) == "invalid trust descriptor"
    assert "sensitive" not in str(caught.value)


def test_descriptor_open_flags_and_regular_file_check_precede_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "trust.json"
    _write(path, _descriptor())
    actual_open = os.open
    actual_fstat = os.fstat
    opened: list[tuple[object, int]] = []
    read_called = False

    def tracked_open(supplied_path: object, flags: int) -> int:
        opened.append((supplied_path, flags))
        return actual_open(supplied_path, flags)

    def nonregular(fd: int) -> os.stat_result:
        original = actual_fstat(fd)
        values = list(original)
        values[stat.ST_MODE] = stat.S_IFIFO | 0o600
        return os.stat_result(values)

    def forbidden_read(_fd: int, _size: int) -> bytes:
        nonlocal read_called
        read_called = True
        raise AssertionError("descriptor body was read before regular-file validation")

    monkeypatch.setattr(trust.os, "open", tracked_open)
    monkeypatch.setattr(trust.os, "fstat", nonregular)
    monkeypatch.setattr(trust.os, "read", forbidden_read)

    with pytest.raises(ValueError, match="descriptor"):
        trust.load_trust_descriptor(path)

    expected_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    assert opened == [(path, expected_flags)]
    assert read_called is False


def test_external_credentials_use_only_explicit_mapping_and_suppress_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://wrong:wrong@wrong.invalid/wrong\n"
        "AWS_ACCESS_KEY_ID=wrong\nAWS_SECRET_ACCESS_KEY=wrong\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    def forbidden_settings() -> None:
        raise AssertionError("external credentials consulted Settings or .env")

    monkeypatch.setattr(config, "get_settings", forbidden_settings)
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: forbidden_settings())
    monkeypatch.setattr(
        sqlalchemy_asyncio, "create_async_engine", lambda *_args, **_kwargs: forbidden_settings()
    )
    credentials = trust.load_external_credentials(
        {
            "DATABASE_URL": "postgresql+psycopg://reader:synthetic@db.example.test/audit",
            "AUDIT_SINK_READ_ACCESS_KEY": "explicit-access",
            "AUDIT_SINK_READ_SECRET_KEY": "explicit-secret",
            "AWS_ACCESS_KEY_ID": "ordinary-access",
            "AWS_SECRET_ACCESS_KEY": "ordinary-secret",
        }
    )

    assert credentials.database_url.endswith("/audit?connect_timeout=5")
    assert credentials.access_key == "explicit-access"
    assert credentials.secret_key == "explicit-secret"
    assert "synthetic" not in repr(credentials)
    assert "explicit-access" not in repr(credentials)
    assert "explicit-secret" not in repr(credentials)


def test_external_credential_length_upper_bound_is_accepted() -> None:
    prefix = "postgresql+psycopg://reader:"
    suffix = "@db.example.test/audit"
    database_url = prefix + "p" * (8_192 - len(prefix) - len(suffix)) + suffix

    credentials = trust.load_external_credentials(
        {
            "DATABASE_URL": database_url,
            "AUDIT_SINK_READ_ACCESS_KEY": "a" * 4_096,
            "AUDIT_SINK_READ_SECRET_KEY": "s" * 4_096,
        }
    )

    assert credentials.database_url == database_url + "?connect_timeout=5"
    assert len(credentials.access_key) == 4_096
    assert len(credentials.secret_key) == 4_096


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DATABASE_URL", ""),
        ("DATABASE_URL", "x" * 8_193),
        ("DATABASE_URL", " postgresql+psycopg://u:p@db/audit"),
        ("DATABASE_URL", "postgresql://u:p@db/audit"),
        ("DATABASE_URL", "postgresql+psycopg://db/audit"),
        ("DATABASE_URL", "postgresql+psycopg://u:@db/audit"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@/audit"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db/"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db:99999/audit"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db/audit?application_name=x"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db/audit?connect_timeout=0"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db/audit?connect_timeout=31"),
        ("DATABASE_URL", "postgresql+psycopg://u:p@db/audit?connect_timeout=1.5"),
        ("AUDIT_SINK_READ_ACCESS_KEY", " short-space"),
        ("AUDIT_SINK_READ_ACCESS_KEY", "x" * 4097),
        ("AUDIT_SINK_READ_SECRET_KEY", ""),
        ("AUDIT_SINK_READ_SECRET_KEY", "x" * 4097),
    ],
)
def test_external_credentials_reject_invalid_values_without_echoing_them(
    name: str, value: str
) -> None:
    environ = {
        "DATABASE_URL": "postgresql+psycopg://reader:password@db.example.test/audit",
        "AUDIT_SINK_READ_ACCESS_KEY": "explicit-access",
        "AUDIT_SINK_READ_SECRET_KEY": "explicit-secret",
    }
    environ[name] = value

    with pytest.raises(ValueError) as caught:
        trust.load_external_credentials(environ)

    message = str(caught.value)
    assert "reader" not in message
    assert "password" not in message
    assert "db.example.test" not in message
    assert "explicit-secret" not in message


@pytest.mark.parametrize(
    "missing",
    ["DATABASE_URL", "AUDIT_SINK_READ_ACCESS_KEY", "AUDIT_SINK_READ_SECRET_KEY"],
)
def test_external_credentials_require_all_three_explicit_values(missing: str) -> None:
    environ = {
        "DATABASE_URL": "postgresql+psycopg://reader:password@db.example.test/audit",
        "AUDIT_SINK_READ_ACCESS_KEY": "explicit-access",
        "AUDIT_SINK_READ_SECRET_KEY": "explicit-secret",
    }
    del environ[missing]

    with pytest.raises(ValueError, match=missing):
        trust.load_external_credentials(environ)


def test_missing_explicit_values_do_not_fall_back_to_process_env_or_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://dotenv:secret@dotenv.invalid/audit\n"
        "AUDIT_SINK_READ_ACCESS_KEY=dotenv-access\n"
        "AUDIT_SINK_READ_SECRET_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://process:secret@process.invalid/audit")
    monkeypatch.setenv("AUDIT_SINK_READ_ACCESS_KEY", "process-access")
    monkeypatch.setenv("AUDIT_SINK_READ_SECRET_KEY", "process-secret")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ordinary-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ordinary-secret")

    def forbidden_settings() -> None:
        raise AssertionError("external credentials consulted Settings or .env")

    monkeypatch.setattr(config, "get_settings", forbidden_settings)
    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: forbidden_settings())
    monkeypatch.setattr(
        sqlalchemy_asyncio, "create_async_engine", lambda *_args, **_kwargs: forbidden_settings()
    )

    with pytest.raises(ValueError, match="DATABASE_URL"):
        trust.load_external_credentials(
            {
                "AWS_ACCESS_KEY_ID": "ordinary-access",
                "AWS_SECRET_ACCESS_KEY": "ordinary-secret",
            }
        )


def test_legacy_verifier_accepts_only_enrolled_keys_and_unchanged_payload() -> None:
    key_a, doc_a = _key(1)
    key_b, doc_b = _key(2)
    key_c, _ = _key(3)
    trusted = (
        trust.TrustedLegacyKey(doc_a["key_id"], key_a.public_key()),
        trust.TrustedLegacyKey(doc_b["key_id"], key_b.public_key()),
    )
    resolver = trust.legacy_verifier(trusted)
    payload = checkpoint_service._payload(_ORG_ID, 7, _HASH, _TS)

    assert resolver(
        org_id=_ORG_ID,
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=key_a.sign(payload),
    )
    assert resolver(
        org_id=_ORG_ID,
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=key_b.sign(payload),
    )
    assert not resolver(
        org_id=_ORG_ID,
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=key_c.sign(payload),
    )
    assert not resolver(
        org_id="33333333-3333-4333-8333-333333333333",
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=key_a.sign(payload),
    )
    assert not resolver(
        org_id=_ORG_ID,
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=None,
    )
    assert not resolver(
        org_id=_ORG_ID,
        latest_id=7,
        latest_row_hash=_HASH,
        timestamp=_TS,
        signature=b"corrupt",
    )


def test_legacy_verifier_rejects_unbounded_direct_key_sets() -> None:
    private_key, key_doc = _key()
    item = trust.TrustedLegacyKey(key_doc["key_id"], private_key.public_key())

    with pytest.raises(ValueError, match="legacy keys"):
        trust.legacy_verifier(())
    with pytest.raises(ValueError, match="legacy keys"):
        trust.legacy_verifier((item,) * 9)
