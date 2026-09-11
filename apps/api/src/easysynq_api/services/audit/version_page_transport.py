"""Read one original version-listing page before the SDK parses its XML."""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any, NoReturn
from urllib.parse import quote, urlsplit

import boto3.session
import botocore.session
from botocore.config import Config
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
from botocore.hooks import HierarchicalEmitter

from . import version_page
from .sink import CheckpointVersionsPage, ExplicitHistoryReader
from .trust import (
    TrustConfigurationError,
    _bucket,
    _endpoint,
    _region,
    _required_environment_value,
)

_DEADLINE_SECONDS = 25.0
_GROUP_TEXT = "checkpoint version page operation and cleanup failed"
_READ_CODES = frozenset(
    {
        "PROVIDER_FAILURE",
        "TRANSPORT_FAILURE",
        "RESPONSE_INVALID",
        "BODY_LIMIT",
        "PAGE_LIMIT",
        "SCOPE_MISMATCH",
        "CURSOR_INVALID",
        "LENGTH_MISMATCH",
        "ROUTING_REJECTED",
        "DEADLINE_EXCEEDED",
        "CLEANUP_FAILED",
    }
)
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
class RawCheckpointVersionPage:
    body: bytes = dataclasses.field(repr=False)
    page: CheckpointVersionsPage


class VersionPageInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("invalid checkpoint version page read input")


class VersionPageReadError(Exception):
    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _READ_CODES:
            raise ValueError("invalid checkpoint version page read code")
        self.code = code
        super().__init__("checkpoint version page read failed")


class VersionPageReadCancelled(BaseException):
    def __init__(self) -> None:
        super().__init__("checkpoint version page read cancelled")


class _PageCaptured(BaseException):
    """Only the instance owned by this operation can terminate capture successfully."""


def _input_invalid() -> NoReturn:
    raise VersionPageInputError() from None


def _read_error(code: str) -> VersionPageReadError:
    error = VersionPageReadError(code)
    error.__suppress_context__ = True
    return error


def _validate_inputs(
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    key_marker: str | None,
    version_id_marker: str | None,
    cancel: threading.Event | None,
) -> None:
    if type(reader) is not ExplicitHistoryReader:
        _input_invalid()
    if cancel is not None and type(cancel) is not threading.Event:
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
        # Reuse R81's pure argument validation; an empty response is never decoded.
        version_page._validate_inputs(b"", reader.bucket, org_id, key_marker, version_id_marker)
    except (TrustConfigurationError, UnicodeError, ValueError, TypeError):
        _input_invalid()
    if normalized_endpoint != reader.endpoint:
        _input_invalid()


def _checkpoint(cancel: threading.Event | None, deadline: float) -> None:
    if cancel is not None and cancel.is_set():
        raise VersionPageReadCancelled()
    if _monotonic() >= deadline:
        raise _read_error("DEADLINE_EXCEEDED")


def _exact_request_guard(
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    key_marker: str | None,
    version_id_marker: str | None,
) -> Callable[..., None]:
    endpoint = urlsplit(reader.endpoint)
    expected_query = (
        "versions&prefix="
        + quote(f"checkpoints/{org_id}/", safe="-_.~")
        + "&max-keys=1000&encoding-type=url"
    )
    if key_marker is not None:
        expected_query += "&key-marker=" + quote(key_marker, safe="-_.~")
    if version_id_marker is not None:
        expected_query += "&version-id-marker=" + quote(version_id_marker, safe="-_.~")
    sent = False

    def guard(*, request: Any, **_kwargs: Any) -> None:
        nonlocal sent
        if sent:
            raise _read_error("ROUTING_REJECTED")
        sent = True
        try:
            target = urlsplit(request.url)
            valid = (
                request.method == "GET"
                and target.scheme == endpoint.scheme
                and target.netloc == endpoint.netloc
                and target.path == f"/{reader.bucket}"
                and target.query == expected_query
                and not target.fragment
                and target.username is None
                and target.password is None
            )
        except (AttributeError, TypeError, ValueError):
            valid = False
        if not valid:
            raise _read_error("ROUTING_REJECTED")

    return guard


def _create_client(
    reader: ExplicitHistoryReader, capture_original_page: Callable[..., None]
) -> Any:
    hooks = HierarchicalEmitter()
    hooks.register_first("before-parse.s3.*", capture_original_page)
    core = botocore.session.Session(event_hooks=hooks)
    session = boto3.session.Session(
        botocore_session=core,
        aws_access_key_id=reader.access_key,
        aws_secret_access_key=reader.secret_key,
        aws_session_token=None,
        region_name=reader.region,
    )
    return session.client(
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


def _classify_operation(error: BaseException) -> BaseException:
    if isinstance(error, version_page.CheckpointVersionPageDecodeError):
        return _read_error(error.code)
    if isinstance(error, ClientError):
        return _read_error("PROVIDER_FAILURE")
    if isinstance(error, _TRANSPORT_EXCEPTIONS):
        return _read_error("TRANSPORT_FAILURE")
    if isinstance(error, IncompleteReadError):
        return _read_error("LENGTH_MISMATCH")
    if isinstance(error, FlexibleChecksumError):
        return _read_error("RESPONSE_INVALID")
    return error


def _raise_outcomes(outcomes: Sequence[BaseException]) -> NoReturn:
    if len(outcomes) == 1:
        raise outcomes[0]
    raise BaseExceptionGroup(_GROUP_TEXT, list(outcomes))


def read_raw_checkpoint_version_page(
    reader: ExplicitHistoryReader,
    org_id: uuid.UUID,
    *,
    key_marker: str | None = None,
    version_id_marker: str | None = None,
    cancel: threading.Event | None = None,
) -> RawCheckpointVersionPage:
    """Admit the original page only after the owned client has closed."""
    _validate_inputs(reader, org_id, key_marker, version_id_marker, cancel)
    deadline = _monotonic() + _DEADLINE_SECONDS
    _checkpoint(cancel, deadline)
    sentinel = _PageCaptured()
    captured = False
    result: RawCheckpointVersionPage | None = None

    def capture_original_page(
        *, operation_model: Any = None, response_dict: Any = None, **_kwargs: Any
    ) -> None:
        nonlocal captured, result
        if captured or getattr(operation_model, "name", None) != "ListObjectVersions":
            raise _read_error("RESPONSE_INVALID")
        captured = True
        if (
            type(response_dict) is not dict
            or type(response_dict.get("status_code")) is not int
            or type(response_dict.get("body")) is not bytes
        ):
            raise _read_error("RESPONSE_INVALID")
        if response_dict["status_code"] != 200:
            raise _read_error("PROVIDER_FAILURE")
        body = response_dict["body"]
        page = version_page.decode_checkpoint_version_page(
            body,
            bucket=reader.bucket,
            org_id=org_id,
            key_marker=key_marker,
            version_id_marker=version_id_marker,
        )
        result = RawCheckpointVersionPage(body, page)
        raise sentinel

    client: Any = None
    primary: BaseException | None = None
    cleanup_faults: list[BaseException] = []
    checkpoint_faults: list[BaseException] = []
    try:
        client = _create_client(reader, capture_original_page)
        client.meta.events.register(
            "before-send.s3",
            _exact_request_guard(reader, org_id, key_marker, version_id_marker),
            unique_id="easysynq.version-page-transport.exact-target",
        )
        _checkpoint(cancel, deadline)
        request: dict[str, Any] = {
            "Bucket": reader.bucket,
            "Prefix": f"checkpoints/{org_id}/",
            "MaxKeys": 1_000,
            "EncodingType": "url",
        }
        if key_marker is not None:
            request["KeyMarker"] = key_marker
        if version_id_marker is not None:
            request["VersionIdMarker"] = version_id_marker
        try:
            client.list_object_versions(**request)
        except BaseException as error:  # admit only our exact capture sentinel
            if error is not sentinel:
                raise
        else:
            raise _read_error("RESPONSE_INVALID")
        _checkpoint(cancel, deadline)
    except BaseException as error:  # noqa: BLE001 - retain unexpected and fatal identities
        primary = _classify_operation(error)
    finally:
        try:
            _checkpoint(cancel, deadline)
        except BaseException as error:  # noqa: BLE001 - observations must not prevent cleanup
            checkpoint_faults.append(error)
        if client is not None:
            try:
                client.close()
            except BaseException as error:  # noqa: BLE001 - always preserve cleanup outcomes
                cleanup_faults.append(
                    _read_error("CLEANUP_FAILED")
                    if isinstance(error, _KNOWN_SDK_EXCEPTIONS)
                    else error
                )
        try:
            _checkpoint(cancel, deadline)
        except BaseException as error:  # noqa: BLE001 - final cooperative check after cleanup
            checkpoint_faults.append(error)

    outcomes: list[BaseException] = []
    cancellation = next(
        (fault for fault in checkpoint_faults if isinstance(fault, VersionPageReadCancelled)),
        None,
    )
    if cancellation is not None:
        if primary is not None and not isinstance(
            primary, (VersionPageReadError, VersionPageReadCancelled)
        ):
            outcomes.append(primary)
        outcomes.append(primary if isinstance(primary, VersionPageReadCancelled) else cancellation)
    elif primary is not None:
        outcomes.append(primary)
    elif checkpoint_faults and not cleanup_faults:
        deadline_fault = next(
            (fault for fault in checkpoint_faults if isinstance(fault, VersionPageReadError)),
            None,
        )
        if deadline_fault is not None:
            outcomes.append(deadline_fault)
    outcomes.extend(cleanup_faults)
    for fault in checkpoint_faults:
        if not isinstance(fault, (VersionPageReadError, VersionPageReadCancelled)) and not any(
            fault is existing for existing in outcomes
        ):
            outcomes.append(fault)
    if outcomes:
        _raise_outcomes(outcomes)
    if result is None:
        raise _read_error("RESPONSE_INVALID")
    return result
