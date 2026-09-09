"""Externally custodied enrollment for the legacy audit verifier.

The descriptor is a public trust input.  It is intentionally loaded without settings, source
discovery, network access, or mutation so database-controlled state cannot select what is checked.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import datetime
import hashlib
import ipaddress
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy.engine import make_url

from .checkpoint import LegacySignatureVerifier, verify_checkpoint_signature

_DESCRIPTOR_MAX_BYTES = 65_536
_KEY_PREFIX = "ed25519-sha256:"
_KEY_ID_RE = re.compile(r"ed25519-sha256:[0-9a-f]{64}\Z")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]\Z")
_REGION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}\Z")
_DNS_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_ALLOWED_DB_QUERY = frozenset({"sslmode", "sslrootcert", "connect_timeout"})


class TrustConfigurationError(ValueError):
    """A controlled, secret-free external verifier configuration failure."""


class _DuplicateMember(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedLegacyKey:
    key_id: str
    public_key: Ed25519PublicKey


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedWitness:
    witness_id: uuid.UUID
    kind: str
    endpoint: str
    bucket: str
    region: str


@dataclasses.dataclass(frozen=True, slots=True)
class TrustedOrganization:
    org_id: uuid.UUID
    public_keys: tuple[TrustedLegacyKey, ...]
    witnesses: tuple[TrustedWitness, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class TrustDescriptor:
    descriptor_id: uuid.UUID
    sha256: str
    organizations: tuple[TrustedOrganization, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ExternalCredentials:
    database_url: str = dataclasses.field(repr=False)
    access_key: str = dataclasses.field(repr=False)
    secret_key: str = dataclasses.field(repr=False)


def _descriptor_invalid() -> NoReturn:
    raise TrustConfigurationError("invalid trust descriptor")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateMember
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError


def _read_descriptor(path: Path) -> bytes:
    if not path.is_absolute():
        _descriptor_invalid()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        _descriptor_invalid()
    try:
        try:
            metadata = os.fstat(fd)
        except OSError:
            _descriptor_invalid()
        if not stat.S_ISREG(metadata.st_mode):
            _descriptor_invalid()
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            _descriptor_invalid()

        chunks: list[bytes] = []
        total = 0
        while total <= _DESCRIPTOR_MAX_BYTES:
            try:
                chunk = os.read(fd, _DESCRIPTOR_MAX_BYTES + 1 - total)
            except OSError:
                _descriptor_invalid()
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > _DESCRIPTOR_MAX_BYTES:
            _descriptor_invalid()
        return b"".join(chunks)
    finally:
        os.close(fd)


def _exact_object(value: Any, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        _descriptor_invalid()
    return value


def _canonical_uuid(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        _descriptor_invalid()
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        _descriptor_invalid()
    if str(parsed) != value:
        _descriptor_invalid()
    return parsed


def _parse_key(value: Any) -> TrustedLegacyKey:
    item = _exact_object(value, frozenset({"key_id", "public_key"}))
    key_id = item["key_id"]
    encoded = item["public_key"]
    if not isinstance(key_id, str) or _KEY_ID_RE.fullmatch(key_id) is None:
        _descriptor_invalid()
    if not isinstance(encoded, str):
        _descriptor_invalid()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        _descriptor_invalid()
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != encoded:
        _descriptor_invalid()
    expected_id = _KEY_PREFIX + hashlib.sha256(raw).hexdigest()
    if key_id != expected_id:
        _descriptor_invalid()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        _descriptor_invalid()
    return TrustedLegacyKey(key_id=key_id, public_key=public_key)


def _dns_hostname_valid(hostname: str) -> bool:
    if len(hostname) > 253 or hostname.endswith("."):
        return False
    labels = hostname.split(".")
    return bool(labels) and all(_DNS_LABEL_RE.fullmatch(label) is not None for label in labels)


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or not value:
        _descriptor_invalid()
    if (
        "%" in value
        or "\\" in value
        or "?" in value
        or "#" in value
        or any(ord(char) <= 32 or ord(char) >= 127 for char in value)
    ):
        _descriptor_invalid()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _descriptor_invalid()
    if parsed.scheme not in {"http", "https"}:
        _descriptor_invalid()
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        _descriptor_invalid()
    if parsed.netloc.endswith(":") or (port is not None and not 1 <= port <= 65_535):
        _descriptor_invalid()
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        _descriptor_invalid()
    hostname = parsed.hostname
    if hostname is None:
        _descriptor_invalid()
    if ":" in hostname and not parsed.netloc.startswith("["):
        _descriptor_invalid()
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
        if not _dns_hostname_valid(hostname):
            _descriptor_invalid()
    if parsed.scheme == "http" and not (
        (
            isinstance(address, ipaddress.IPv4Address)
            and address in ipaddress.ip_network("127.0.0.0/8")
        )
        or (isinstance(address, ipaddress.IPv6Address) and address == ipaddress.IPv6Address("::1"))
    ):
        _descriptor_invalid()

    normalized_host = hostname.lower()
    if isinstance(address, ipaddress.IPv6Address):
        normalized_host = f"[{address.compressed}]"
    elif address is not None:
        normalized_host = str(address)
    suffix = "" if port is None else f":{port}"
    return f"{parsed.scheme}://{normalized_host}{suffix}"


def _bucket(value: Any) -> str:
    if not isinstance(value, str) or _BUCKET_RE.fullmatch(value) is None:
        _descriptor_invalid()
    if ".." in value or any(not label for label in value.split(".")):
        _descriptor_invalid()
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    _descriptor_invalid()


def _region(value: Any) -> str:
    if not isinstance(value, str) or _REGION_RE.fullmatch(value) is None:
        _descriptor_invalid()
    return value


def _parse_witness(value: Any) -> TrustedWitness:
    item = _exact_object(
        value,
        frozenset({"witness_id", "kind", "endpoint", "bucket", "region"}),
    )
    if item["kind"] != "worm_bucket":
        _descriptor_invalid()
    return TrustedWitness(
        witness_id=_canonical_uuid(item["witness_id"]),
        kind="worm_bucket",
        endpoint=_endpoint(item["endpoint"]),
        bucket=_bucket(item["bucket"]),
        region=_region(item["region"]),
    )


def _bounded_array(value: Any, *, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _descriptor_invalid()
    return value


def _parse_organization(value: Any) -> TrustedOrganization:
    item = _exact_object(value, frozenset({"org_id", "public_keys", "witnesses"}))
    keys = tuple(
        _parse_key(key) for key in _bounded_array(item["public_keys"], minimum=1, maximum=8)
    )
    if len({key.key_id for key in keys}) != len(keys):
        _descriptor_invalid()
    witnesses = tuple(
        _parse_witness(witness)
        for witness in _bounded_array(item["witnesses"], minimum=1, maximum=4)
    )
    if len({witness.witness_id for witness in witnesses}) != len(witnesses):
        _descriptor_invalid()
    return TrustedOrganization(
        org_id=_canonical_uuid(item["org_id"]),
        public_keys=keys,
        witnesses=witnesses,
    )


def load_trust_descriptor(path: Path) -> TrustDescriptor:
    """Read and strictly validate one protected public trust descriptor."""
    body = _read_descriptor(path)
    try:
        decoded = body.decode("utf-8")
        raw = json.loads(
            decoded,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateMember,
        RecursionError,
        ValueError,
        TypeError,
    ):
        _descriptor_invalid()
    root = _exact_object(raw, frozenset({"format_version", "descriptor_id", "organizations"}))
    if type(root["format_version"]) is not int or root["format_version"] != 1:
        _descriptor_invalid()
    organizations = tuple(
        _parse_organization(item)
        for item in _bounded_array(root["organizations"], minimum=1, maximum=16)
    )
    if len({organization.org_id for organization in organizations}) != len(organizations):
        _descriptor_invalid()
    witness_ids = [
        witness.witness_id for organization in organizations for witness in organization.witnesses
    ]
    if len(set(witness_ids)) != len(witness_ids):
        _descriptor_invalid()
    return TrustDescriptor(
        descriptor_id=_canonical_uuid(root["descriptor_id"]),
        sha256=hashlib.sha256(body).hexdigest(),
        organizations=organizations,
    )


def _required_environment_value(environ: Mapping[str, str], name: str, *, maximum: int) -> str:
    value = environ.get(name)
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise TrustConfigurationError(f"{name} is missing or invalid")
    return value


def _database_url(value: str) -> str:
    if "#" in value:
        raise TrustConfigurationError("DATABASE_URL is invalid")
    try:
        parsed = make_url(value)
        port = parsed.port
    except Exception:  # noqa: BLE001 - parser errors must not echo a credential-bearing DSN
        raise TrustConfigurationError("DATABASE_URL is invalid") from None
    if (
        parsed.drivername != "postgresql+psycopg"
        or parsed.username is None
        or not parsed.username
        or parsed.password is None
        or not parsed.password
        or parsed.host is None
        or not parsed.host
        or parsed.database is None
        or not parsed.database
        or (port is not None and not 1 <= port <= 65_535)
        or not set(parsed.query).issubset(_ALLOWED_DB_QUERY)
    ):
        raise TrustConfigurationError("DATABASE_URL is invalid")
    for option_value in parsed.query.values():
        if not isinstance(option_value, str):
            raise TrustConfigurationError("DATABASE_URL is invalid")
    timeout = parsed.query.get("connect_timeout")
    if timeout is not None:
        if not isinstance(timeout, str):
            raise TrustConfigurationError("DATABASE_URL is invalid")
        if not timeout.isdecimal() or not 1 <= int(timeout) <= 30:
            raise TrustConfigurationError("DATABASE_URL is invalid")
    if timeout is None:
        separator = "&" if "?" in value else "?"
        return f"{value}{separator}connect_timeout=5"
    return value


def load_external_credentials(environ: Mapping[str, str]) -> ExternalCredentials:
    """Load strict reader credentials only from the explicitly supplied process environment."""
    database_url = _required_environment_value(environ, "DATABASE_URL", maximum=8_192)
    access_key = _required_environment_value(environ, "AUDIT_SINK_READ_ACCESS_KEY", maximum=4_096)
    secret_key = _required_environment_value(environ, "AUDIT_SINK_READ_SECRET_KEY", maximum=4_096)
    return ExternalCredentials(
        database_url=_database_url(database_url),
        access_key=access_key,
        secret_key=secret_key,
    )


def legacy_verifier(keys: tuple[TrustedLegacyKey, ...]) -> LegacySignatureVerifier:
    """Return a verifier bounded to the one-to-eight externally enrolled legacy keys."""
    if not 1 <= len(keys) <= 8:
        raise ValueError("legacy keys must contain between 1 and 8 entries")

    def verify(
        *,
        org_id: Any,
        latest_id: int,
        latest_row_hash: bytes,
        timestamp: datetime.datetime,
        signature: bytes | None,
    ) -> bool:
        return any(
            verify_checkpoint_signature(
                item.public_key,
                org_id=org_id,
                latest_id=latest_id,
                latest_row_hash=latest_row_hash,
                timestamp=timestamp,
                signature=signature,
            )
            for item in keys
        )

    return verify
