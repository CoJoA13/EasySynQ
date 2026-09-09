"""Exact, bounded transport for one explicitly selected retained checkpoint version."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, NoReturn
from urllib.parse import quote, urlsplit

from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    FlexibleChecksumError,
    IncompleteReadError,
    ProxyConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    SSLError,
)

from .sink import CheckpointVersionRef, ExplicitHistoryReader
from .trust import (
    TrustConfigurationError,
    _bucket,
    _endpoint,
    _region,
    _required_environment_value,
)

_BODY_MAX_BYTES = 65_536
_CHUNK_MAX_BYTES = 8_192
_DEADLINE_SECONDS = 15.0
_INPUT_TEXT = "invalid raw checkpoint version input"
_CANCELLED_TEXT = "raw checkpoint version read cancelled"
_GROUP_TEXT = "raw checkpoint version operation and cleanup failed"
_READ_CODES = frozenset(
    {
        "PROVIDER_FAILURE",
        "TRANSPORT_FAILURE",
        "RESPONSE_INVALID",
        "VERSION_MISMATCH",
        "DELETE_MARKER",
        "LENGTH_MISMATCH",
        "BODY_LIMIT",
        "DEADLINE_EXCEEDED",
        "ROUTING_REJECTED",
        "CLEANUP_FAILED",
    }
)
_MISSING = object()
_TRANSPORT_EXCEPTIONS = (
    EndpointConnectionError,
    SSLError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ProxyConnectionError,
    ConnectionClosedError,
    ResponseStreamingError,
)
_KNOWN_SDK_EXCEPTIONS = (
    ClientError,
    *_TRANSPORT_EXCEPTIONS,
    IncompleteReadError,
    FlexibleChecksumError,
)

_monotonic = time.monotonic


@dataclasses.dataclass(frozen=True, slots=True)
class RawCheckpointVersion:
    key: str
    version_id: str
    body: bytes = dataclasses.field(repr=False)


class RawVersionInputError(ValueError):
    """The explicit reader/reference input is outside the fixed transport contract."""

    def __init__(self) -> None:
        super().__init__(_INPUT_TEXT)


class RawVersionReadError(Exception):
    """A controlled, secret-free provider, transport, response, or cleanup failure."""

    def __init__(self, code: str) -> None:
        if code not in _READ_CODES:
            raise ValueError("invalid raw checkpoint version read code")
        self.code = code
        super().__init__(f"raw checkpoint version read failed: {code}")


class RawVersionReadCancelled(BaseException):
    """The cooperative cancellation event was observed before a result was published."""

    def __init__(self) -> None:
        super().__init__(_CANCELLED_TEXT)


def _input_invalid() -> NoReturn:
    raise RawVersionInputError() from None


def _read_error(code: str) -> RawVersionReadError:
    error = RawVersionReadError(code)
    error.__suppress_context__ = True
    return error


def _valid_label(value: Any) -> bool:
    if type(value) is not str or not value or len(value) > 1_024:
        return False
    if any(ord(character) <= 31 or 127 <= ord(character) <= 159 for character in value):
        return False
    try:
        return len(value.encode("utf-8")) <= 1_024
    except UnicodeEncodeError:
        return False


def _validate_inputs(
    reader: ExplicitHistoryReader,
    ref: CheckpointVersionRef,
    cancel: threading.Event | None,
) -> None:
    if type(reader) is not ExplicitHistoryReader or type(ref) is not CheckpointVersionRef:
        _input_invalid()
    if cancel is not None and type(cancel) is not threading.Event:
        _input_invalid()
    if not _valid_label(ref.key) or not _valid_label(ref.version_id):
        _input_invalid()
    if (
        type(reader.endpoint) is not str
        or not reader.endpoint
        or len(reader.endpoint) > 267
        or not reader.endpoint.isascii()
        or type(reader.bucket) is not str
        or len(reader.bucket) > 63
        or type(reader.region) is not str
        or len(reader.region) > 63
        or type(reader.access_key) is not str
        or not 1 <= len(reader.access_key) <= 4_096
        or type(reader.secret_key) is not str
        or not 1 <= len(reader.secret_key) <= 4_096
    ):
        _input_invalid()
    try:
        normalized_endpoint = _endpoint(reader.endpoint)
        _bucket(reader.bucket)
        _region(reader.region)
        _required_environment_value({"access_key": reader.access_key}, "access_key", maximum=4_096)
        _required_environment_value({"secret_key": reader.secret_key}, "secret_key", maximum=4_096)
    except (TrustConfigurationError, UnicodeError, ValueError, TypeError):
        _input_invalid()
    if normalized_endpoint != reader.endpoint:
        _input_invalid()


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _checkpoint(cancel: threading.Event | None, deadline: float) -> None:
    if _cancelled(cancel):
        raise RawVersionReadCancelled()
    if _monotonic() >= deadline:
        raise _read_error("DEADLINE_EXCEEDED")


def _expected_path(reader: ExplicitHistoryReader, ref: CheckpointVersionRef) -> str:
    return quote(f"/{reader.bucket}/{ref.key}", safe="/~")


def _expected_query(ref: CheckpointVersionRef) -> str:
    return "versionId=" + quote(ref.version_id, safe="-_.~")


def _exact_request_guard(
    reader: ExplicitHistoryReader,
    ref: CheckpointVersionRef,
) -> Callable[..., None]:
    endpoint = urlsplit(reader.endpoint)
    expected_path = _expected_path(reader, ref)
    expected_query = _expected_query(ref)
    sent = False

    def guard(*, request: Any, **_kwargs: Any) -> None:
        nonlocal sent
        if sent:
            raise _read_error("ROUTING_REJECTED")
        sent = True
        try:
            target = urlsplit(request.url)
        except (AttributeError, TypeError, ValueError):
            raise _read_error("ROUTING_REJECTED") from None
        if (
            request.method != "GET"
            or target.scheme != endpoint.scheme
            or target.netloc != endpoint.netloc
            or target.path != expected_path
            or target.query != expected_query
            or target.fragment
            or target.username is not None
            or target.password is not None
        ):
            raise _read_error("ROUTING_REJECTED")

    return guard


def _classify_operation(error: BaseException) -> BaseException:
    if isinstance(error, (RawVersionReadError, RawVersionReadCancelled)):
        return error
    if isinstance(error, ClientError):
        return _read_error("PROVIDER_FAILURE")
    if isinstance(error, _TRANSPORT_EXCEPTIONS):
        return _read_error("TRANSPORT_FAILURE")
    if isinstance(error, IncompleteReadError):
        return _read_error("LENGTH_MISMATCH")
    if isinstance(error, FlexibleChecksumError):
        return _read_error("RESPONSE_INVALID")
    return error


def _classify_cleanup(error: BaseException) -> BaseException:
    if isinstance(error, _KNOWN_SDK_EXCEPTIONS):
        return _read_error("CLEANUP_FAILED")
    return error


def _raise_outcomes(outcomes: Sequence[BaseException]) -> NoReturn:
    if len(outcomes) == 1:
        raise outcomes[0]
    raise BaseExceptionGroup(_GROUP_TEXT, list(outcomes))


def _create_client(reader: ExplicitHistoryReader, ref: CheckpointVersionRef) -> Any:
    """Create and guard the one privately owned client used by a single public call."""
    import boto3
    from botocore.config import Config

    session = boto3.session.Session(
        aws_access_key_id=reader.access_key,
        aws_secret_access_key=reader.secret_key,
        aws_session_token=None,
        region_name=reader.region,
    )
    client = session.client(
        "s3",
        endpoint_url=reader.endpoint,
        verify=True,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path", "use_accelerate_endpoint": False},
            use_dualstack_endpoint=False,
            use_fips_endpoint=False,
            inject_host_prefix=False,
            ignore_configured_endpoint_urls=True,
            proxies={},
            retries={"mode": "standard", "total_max_attempts": 1},
            connect_timeout=3,
            read_timeout=5,
            request_checksum_calculation="when_supported",
            response_checksum_validation="when_supported",
        ),
    )
    try:
        client.meta.events.register(
            "before-send.s3",
            _exact_request_guard(reader, ref),
            unique_id="easysynq.raw-transport.exact-target",
        )
    except BaseException as operation_error:  # noqa: BLE001 - preserve fatal fault identity
        outcomes = [_classify_operation(operation_error)]
        try:
            client.close()
        except BaseException as cleanup_error:  # noqa: BLE001 - attempt cleanup for all faults
            outcomes.append(_classify_cleanup(cleanup_error))
        _raise_outcomes(outcomes)
    return client


def _validate_response(
    response: Any,
    expected_version_id: str,
) -> tuple[Callable[[int], Any], Callable[[], Any], int]:
    if type(response) is not dict:
        raise _read_error("RESPONSE_INVALID")
    candidate = response.get("Body")
    close = getattr(candidate, "close", None)
    if not callable(close):
        raise _read_error("RESPONSE_INVALID")
    read = getattr(candidate, "read", None)
    if not callable(read):
        raise _read_error("RESPONSE_INVALID")
    metadata = response.get("ResponseMetadata")
    if type(metadata) is not dict:
        raise _read_error("RESPONSE_INVALID")
    status = metadata.get("HTTPStatusCode")
    if type(status) is not int or status != 200:
        raise _read_error("RESPONSE_INVALID")
    marker = response.get("DeleteMarker", _MISSING)
    if marker is True:
        raise _read_error("DELETE_MARKER")
    if marker is not _MISSING and marker is not False:
        raise _read_error("RESPONSE_INVALID")
    version_id = response.get("VersionId")
    if type(version_id) is not str or not version_id or version_id != expected_version_id:
        raise _read_error("VERSION_MISMATCH")
    content_length = response.get("ContentLength")
    if type(content_length) is not int or content_length < 0:
        raise _read_error("RESPONSE_INVALID")
    if content_length > _BODY_MAX_BYTES:
        raise _read_error("BODY_LIMIT")
    return read, close, content_length


def _read_body(
    read: Callable[[int], Any],
    declared_length: int,
    *,
    cancel: threading.Event | None,
    deadline: float,
) -> bytes:
    output = bytearray()
    remaining = declared_length
    while remaining:
        requested = min(_CHUNK_MAX_BYTES, remaining)
        _checkpoint(cancel, deadline)
        chunk = read(requested)
        _checkpoint(cancel, deadline)
        if type(chunk) is not bytes or len(chunk) > requested:
            raise _read_error("RESPONSE_INVALID")
        if not chunk:
            raise _read_error("LENGTH_MISMATCH")
        output.extend(chunk)
        remaining -= len(chunk)
    _checkpoint(cancel, deadline)
    probe = read(1)
    _checkpoint(cancel, deadline)
    if type(probe) is not bytes or len(probe) > 1:
        raise _read_error("RESPONSE_INVALID")
    if probe:
        code = "BODY_LIMIT" if declared_length == _BODY_MAX_BYTES else "LENGTH_MISMATCH"
        raise _read_error(code)
    return bytes(output)


def _cleanup_checkpoint(
    cancel: threading.Event | None,
    deadline: float,
) -> tuple[bool, bool]:
    cancelled = _cancelled(cancel)
    expired = False if cancelled else _monotonic() >= deadline
    return cancelled, expired


def read_raw_checkpoint_version(
    reader: ExplicitHistoryReader,
    ref: CheckpointVersionRef,
    *,
    cancel: threading.Event | None = None,
) -> RawCheckpointVersion:
    """Read one exact retained version without parsing or normalizing its bounded entity bytes."""
    _validate_inputs(reader, ref, cancel)
    if _cancelled(cancel):
        raise RawVersionReadCancelled()
    deadline = _monotonic() + _DEADLINE_SECONDS

    client: Any | None = None
    client_close: Callable[[], Any] | None = None
    body_close: Callable[[], Any] | None = None
    primary: BaseException | None = None
    cleanup_faults: list[BaseException] = []
    result: RawCheckpointVersion | None = None
    cancellation_seen = False
    deadline_seen = False
    try:
        client = _create_client(reader, ref)
        client_close = client.close
        _checkpoint(cancel, deadline)
        response = client.get_object(
            Bucket=reader.bucket,
            Key=ref.key,
            VersionId=ref.version_id,
        )
        if type(response) is dict:
            candidate = response.get("Body")
            candidate_close = getattr(candidate, "close", None)
            if callable(candidate_close):
                body_close = candidate_close
        _checkpoint(cancel, deadline)
        read, adopted_close, content_length = _validate_response(response, ref.version_id)
        body_close = adopted_close
        raw = _read_body(read, content_length, cancel=cancel, deadline=deadline)
        _checkpoint(cancel, deadline)
        result = RawCheckpointVersion(ref.key, ref.version_id, raw)
    except BaseException as error:  # noqa: BLE001 - contract preserves unexpected identities
        primary = _classify_operation(error)
    finally:
        observed = _cleanup_checkpoint(cancel, deadline)
        cancellation_seen |= observed[0]
        deadline_seen |= observed[1]
        if body_close is not None:
            try:
                body_close()
            except BaseException as error:  # noqa: BLE001 - preserve cleanup fault identity
                cleanup_faults.append(_classify_cleanup(error))
            observed = _cleanup_checkpoint(cancel, deadline)
            cancellation_seen |= observed[0]
            deadline_seen |= observed[1]
        if client_close is not None:
            try:
                client_close()
            except BaseException as error:  # noqa: BLE001 - preserve cleanup fault identity
                cleanup_faults.append(_classify_cleanup(error))
            observed = _cleanup_checkpoint(cancel, deadline)
            cancellation_seen |= observed[0]
            deadline_seen |= observed[1]

    outcomes: list[BaseException] = []
    if cancellation_seen:
        cancellation = (
            primary if isinstance(primary, RawVersionReadCancelled) else RawVersionReadCancelled()
        )
        if primary is not None and not isinstance(
            primary, (RawVersionReadError, RawVersionReadCancelled)
        ):
            outcomes.extend((primary, cancellation))
        else:
            outcomes.append(cancellation)
    elif primary is not None:
        outcomes.append(primary)
    elif not cleanup_faults and deadline_seen:
        outcomes.append(_read_error("DEADLINE_EXCEEDED"))
    outcomes.extend(cleanup_faults)
    if outcomes:
        _raise_outcomes(outcomes)
    if result is None:
        raise RuntimeError("raw checkpoint version read produced no outcome")
    return result
