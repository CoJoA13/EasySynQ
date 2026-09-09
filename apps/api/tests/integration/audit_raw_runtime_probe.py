"""Actual-image probe for the owned raw checkpoint transport acceptance."""

from __future__ import annotations

import argparse
import base64
import datetime
import gzip
import hashlib
import http.server
import json
import os
import ssl
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import boto3
import certifi
import rfc8785
from botocore.exceptions import ReadTimeoutError
from botocore.response import StreamingBody
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from easysynq_api.services.audit import bootstrap_bridge as bridge
from easysynq_api.services.audit import raw_transport
from easysynq_api.services.audit.lineage import AuditHead, BootstrapPin, StreamEnrollment
from easysynq_api.services.audit.sink import CheckpointVersionRef, ExplicitHistoryReader

_BODY_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/raw-body\0"
_NAMESPACE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/namespace\0"
_PAGE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/page\0"
_ROOT_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/root\0"
_ESTABLISHED = (
    "external-root-content-binding",
    "committed-page-and-locator-closure",
    "retained-legacy-signature-authentication",
    "supplied-observation-reconciliation",
    "per-witness-signed-boundary-binding",
)
_UNPROVED = (
    "operational-legacy-history-completeness",
    "witness-collection-completeness",
    "witness-custody",
    "audit-chain-comparison",
    "v2-lineage-consistency",
    "freshness",
    "rollback-memory-continuity",
    "operational-key-activation",
)
_PROVIDER_ERROR_PREFIX = b"<Error><Code>NoSuchVersion</Code>"
_PROVIDER_ERROR_SUFFIX = b"</Error>"
_PROVIDER_ERROR_BODY = (
    _PROVIDER_ERROR_PREFIX
    + b"x" * (65_536 - len(_PROVIDER_ERROR_PREFIX) - len(_PROVIDER_ERROR_SUFFIX))
    + _PROVIDER_ERROR_SUFFIX
)


def _load(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    assert type(value) is dict
    return value


def _reader(config: dict[str, Any], *, bucket: str | None = None) -> ExplicitHistoryReader:
    return ExplicitHistoryReader(
        config["endpoint"],
        config["bucket"] if bucket is None else bucket,
        config["region"],
        os.environ[config["access_env"]],
        os.environ[config["secret_env"]],
    )


def _read(config: dict[str, Any], key: str, version_id: str, *, bucket: str | None = None) -> bytes:
    return raw_transport.read_raw_checkpoint_version(
        _reader(config, bucket=bucket), CheckpointVersionRef(key, version_id)
    ).body


def _failure(
    config: dict[str, Any],
    key: str,
    version_id: str,
    code: str,
    *,
    bucket: str | None = None,
) -> None:
    try:
        _read(config, key, version_id, bucket=bucket)
    except raw_transport.RawVersionReadError as error:
        assert error.code == code
        assert str(error) == f"raw checkpoint version read failed: {code}"
        assert error.__cause__ is None and error.__suppress_context__ is True
    else:
        raise AssertionError(f"expected {code}")


def _verify_vectors(reference: dict[str, Any], names: tuple[str, ...]) -> None:
    for name in names:
        vector = reference["legacy_vectors"][name]
        raw = bytes.fromhex(vector["body_hex"])
        assert len(raw) == vector["body_bytes"]
        assert hashlib.sha256(_BODY_DOMAIN + raw).hexdigest() == vector["body_hash"]
        envelope = json.loads(raw)
        payload = envelope["checkpoint"]
        timestamp = datetime.datetime.fromisoformat(payload["timestamp"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime.UTC)
        normalized = {
            "org_id": payload["org_id"],
            "latest_id": payload["latest_id"],
            "latest_row_hash": bytes.fromhex(payload["latest_row_hash"]).hex(),
            "timestamp": timestamp.astimezone(datetime.UTC).isoformat(),
        }
        canonical = rfc8785.dumps(normalized)
        assert canonical == bytes.fromhex(vector["canonical_payload_hex"])
        signature = base64.b64decode(envelope["signature"], validate=True)
        assert signature == bytes.fromhex(vector["signature_hex"])
        material = reference["materials"][vector["material"]]
        public = bytes.fromhex(material["public_key_hex"])
        assert material["key_id"] == "ed25519-sha256:" + hashlib.sha256(public).hexdigest()
        key = Ed25519PublicKey.from_public_bytes(public)
        if vector["crypto_valid"]:
            key.verify(signature, canonical)
        else:
            try:
                key.verify(signature, canonical)
            except InvalidSignature:
                pass
            else:
                raise AssertionError("invalid vector signature was accepted")


def _namespace_hash(endpoint: str, bucket: str, region: str, prefix: str) -> str:
    namespace = {
        "kind": "worm_bucket",
        "endpoint": endpoint,
        "bucket": bucket,
        "region": region,
        "prefix": prefix,
    }
    return hashlib.sha256(_NAMESPACE_DOMAIN + rfc8785.dumps(namespace)).hexdigest()


def _package(
    config: dict[str, Any],
    reference: dict[str, Any],
    committed: tuple[bridge.LegacyBodyObservation, ...],
    witnesses: tuple[UUID, ...],
) -> tuple[bridge.BridgeEnrollment, bytes, tuple[bridge.BridgePageObservation, ...]]:
    org_id = UUID(config["org_id"])
    stream_id = UUID(config["stream_id"])
    boundary_vector = reference["legacy_vectors"]["boundary"]
    old_vector = reference["legacy_vectors"]["old"]
    entries = sorted(
        (
            {
                "witness_id": str(item.witness_id),
                "object_key": item.object_key,
                "version_id": item.version_id,
                "body_hash": hashlib.sha256(_BODY_DOMAIN + item.body).hexdigest(),
                "body_bytes": str(len(item.body)),
            }
            for item in committed
        ),
        key=lambda item: (item["witness_id"], item["object_key"], item["version_id"]),
    )
    page_document = {
        "format_version": 1,
        "kind": "legacy_bridge_page",
        "org_id": str(org_id),
        "stream_id": str(stream_id),
        "page_index": "0",
        "entries": entries,
    }
    page_raw = rfc8785.dumps(page_document)
    witness_documents = []
    for witness_id in witnesses:
        count = sum(item.witness_id == witness_id for item in committed)
        witness_documents.append(
            {
                "witness_id": str(witness_id),
                "namespace_hash": _namespace_hash(
                    config["endpoint"], config["bucket"], config["region"], config["prefix"]
                ),
                "entry_count": str(count),
                "lowest_head": {
                    "latest_id": str(old_vector["head"]["latest_id"]),
                    "latest_row_hash": old_vector["head"]["latest_row_hash"],
                },
                "highest_head": {
                    "latest_id": str(boundary_vector["head"]["latest_id"]),
                    "latest_row_hash": boundary_vector["head"]["latest_row_hash"],
                },
            }
        )
    initial = reference["materials"]["v2_initial"]
    legacy = sorted(
        (reference["materials"]["legacy_old"], reference["materials"]["legacy_current"]),
        key=lambda item: item["key_id"],
    )
    root_document = {
        "format_version": 1,
        "kind": "legacy_bridge",
        "org_id": str(org_id),
        "stream_id": str(stream_id),
        "initial_key_id": initial["key_id"],
        "initial_public_key": base64.b64encode(bytes.fromhex(initial["public_key_hex"])).decode(),
        "initial_key_epoch": "0",
        "audit_boundary": {
            "latest_id": str(boundary_vector["head"]["latest_id"]),
            "latest_row_hash": boundary_vector["head"]["latest_row_hash"],
        },
        "legacy_key_ids": [item["key_id"] for item in legacy],
        "witnesses": sorted(witness_documents, key=lambda item: item["witness_id"]),
        "entry_count": str(len(entries)),
        "pages": [
            {
                "page_index": "0",
                "entry_count": str(len(entries)),
                "page_hash": hashlib.sha256(_PAGE_DOMAIN + page_raw).hexdigest(),
            }
        ],
    }
    root_raw = rfc8785.dumps(root_document)
    pin = BootstrapPin(
        hashlib.sha256(_ROOT_DOMAIN + root_raw).hexdigest(),
        initial["key_id"],
        bytes.fromhex(initial["public_key_hex"]),
        0,
        AuditHead(
            boundary_vector["head"]["latest_id"],
            boundary_vector["head"]["latest_row_hash"],
        ),
    )
    enrollment = bridge.BridgeEnrollment(
        StreamEnrollment(org_id, stream_id, pin, None),
        tuple(
            bridge.BridgeWitnessPin(
                witness_id,
                _namespace_hash(
                    config["endpoint"], config["bucket"], config["region"], config["prefix"]
                ),
            )
            for witness_id in sorted(witnesses, key=str)
        ),
        tuple(
            bridge.LegacyPublicMaterial(item["key_id"], bytes.fromhex(item["public_key_hex"]))
            for item in legacy
        ),
    )
    return enrollment, root_raw, (bridge.BridgePageObservation(page_raw),)


def _evaluate(
    enrollment: bridge.BridgeEnrollment,
    root: bytes,
    pages: tuple[bridge.BridgePageObservation, ...],
    observations: tuple[bridge.LegacyObservation, ...],
) -> bridge.BridgeEvaluation:
    return bridge.evaluate_bootstrap_bridge(
        enrollment,
        root,
        pages,
        observations,
        limits=bridge.BridgeLimits(4096, 16 * 1024 * 1024, 32),
    )


def _provider(config: dict[str, Any]) -> dict[str, Any]:
    reference = _load(config["fixture"])
    names = (
        "old",
        "old_transport",
        "boundary",
        "unlisted_old",
        "old_head_conflict",
        "above_boundary",
    )
    _verify_vectors(reference, names)
    witness_a = UUID(config["witness_a"])
    refs = config["refs"]
    actual = {name: _read(config, refs[name]["key"], refs[name]["version_id"]) for name in names}
    for name in actual:
        expected = bytes.fromhex(reference["legacy_vectors"][name]["body_hex"])
        assert actual[name] == expected
    committed = tuple(
        bridge.LegacyBodyObservation(
            witness_a, refs[name]["key"], refs[name]["version_id"], actual[name]
        )
        for name in ("old", "old_transport", "boundary")
    )
    enrollment, root, pages = _package(config, reference, committed, (witness_a,))
    result = _evaluate(enrollment, root, pages, committed)
    assert result.status == "consistent"
    assert result.established_checks == _ESTABLISHED
    assert result.unproved_checks == _UNPROVED
    assert result.usable_bootstrap_pin is enrollment.stream.bootstrap

    changed = list(committed)
    changed[0] = bridge.LegacyBodyObservation(
        witness_a, refs["old"]["key"], refs["old"]["version_id"], actual["old_transport"]
    )
    assert _evaluate(enrollment, root, pages, tuple(changed)).status == "failed"

    expected_negative_codes = {
        "unlisted_old": {"UNLISTED_AUTHENTIC_LEGACY"},
        "old_head_conflict": {"SIGNED_HEAD_CONFLICT", "UNLISTED_AUTHENTIC_LEGACY"},
        "above_boundary": {"ABOVE_BOOTSTRAP_BOUNDARY", "UNLISTED_AUTHENTIC_LEGACY"},
    }
    negative_codes: dict[str, set[str]] = {}
    for name in ("unlisted_old", "old_head_conflict", "above_boundary"):
        extra = bridge.LegacyBodyObservation(
            witness_a,
            refs[name]["key"],
            refs[name]["version_id"],
            actual[name],
        )
        failed = _evaluate(enrollment, root, pages, (*committed, extra))
        assert failed.status == "failed" and failed.failed_issues > 0
        negative_codes[name] = {issue.code for issue in failed.issues}
        assert negative_codes[name] == expected_negative_codes[name]

    witness_b = UUID(config["witness_b"])
    b_key = f"{config['prefix']}0000000042-required-b.json"
    b_version = "required-b-version"
    b_body = bytes.fromhex(reference["legacy_vectors"]["boundary"]["body_hex"])
    b_committed = bridge.LegacyBodyObservation(witness_b, b_key, b_version, b_body)
    incomplete_enrollment, incomplete_root, incomplete_pages = _package(
        config,
        reference,
        (*committed, b_committed),
        (witness_a, witness_b),
    )
    incomplete = _evaluate(
        incomplete_enrollment,
        incomplete_root,
        incomplete_pages,
        (
            *committed,
            bridge.LegacyUnavailableObservation(witness_b, b_key, b_version),
            bridge.WitnessCollectionGap(witness_b, "listing-unavailable"),
        ),
    )
    assert incomplete.status == "incomplete"
    assert {issue.code for issue in incomplete.issues} >= {
        "LEGACY_BODY_UNAVAILABLE",
        "WITNESS_COLLECTION_GAP",
    }

    _failure(config, refs["old"]["key"], "missing-version", "PROVIDER_FAILURE")
    _failure(
        config,
        config["denied_key"],
        config["denied_version"],
        "PROVIDER_FAILURE",
        bucket=config["denied_bucket"],
    )
    _failure(
        config,
        config["marker_key"],
        config["marker_version"],
        "PROVIDER_FAILURE",
    )
    _failure(
        config,
        config["null_key"],
        "null",
        "VERSION_MISMATCH",
        bucket=config["null_bucket"],
    )
    return {
        "case_count": 21,
        "bridge": {
            "consistent": result.status,
            "negative_categories": {name: sorted(codes) for name, codes in negative_codes.items()},
            "required_witness": incomplete.status,
        },
        "literal_null": "VERSION_MISMATCH",
        "delete_marker": "PROVIDER_FAILURE",
        "missing_version": "PROVIDER_FAILURE",
    }


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, handler: type[http.server.BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler)
        self.requests: list[dict[str, str]] = []
        self.stall_done = threading.Event()
        self.handler_errors: list[Exception] = []
        self.expected_peer_disconnects: list[str] = []

    def handle_error(self, _request: object, _client_address: object) -> None:
        error = sys.exception()
        if isinstance(error, Exception):
            self.handler_errors.append(error)
        else:
            self.handler_errors.append(RuntimeError("runtime fixture handler failed"))


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_HEAD(self) -> None:
        self.close_connection = True
        self.server.requests.append({"method": "HEAD", "target": self.path})
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self) -> None:
        self.close_connection = True
        server = self.server
        server.requests.append(
            {
                "method": "GET",
                "target": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "checksum_mode": self.headers.get("x-amz-checksum-mode", ""),
            }
        )
        route = getattr(server, "route", "success")
        version_id = getattr(server, "version_id", "runtime-version")
        if route.startswith("redirect"):
            self.send_response(301)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Location", server.redirect)
            if route == "redirect-region":
                self.send_header("x-amz-bucket-region", "us-west-2")
            body = b"<Error><Code>PermanentRedirect</Code></Error>"
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "provider-error":
            body = _PROVIDER_ERROR_BODY
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        payload = getattr(server, "payload", b"runtime-body")
        declared = getattr(server, "declared", len(payload))
        self.send_response(200)
        self.send_header("x-amz-version-id", version_id)
        if route == "chunked":
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(declared))
        if route == "encoded":
            self.send_header("Content-Encoding", "gzip")
        checksum = getattr(server, "checksum", None)
        if checksum is not None:
            self.send_header("x-amz-checksum-sha256", checksum)
        self.send_header("Connection", "close")
        self.end_headers()
        if route == "stall":
            self.wfile.flush()
            time.sleep(6.2)
            server.stall_done.set()
            return
        if route == "chunked":
            try:
                for chunk in (payload[:1], payload[1:]):
                    self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError) as error:
                server.expected_peer_disconnects.append(type(error).__name__)
            return
        self.wfile.write(payload)
        self.wfile.flush()


@contextmanager
def _server(
    *,
    route: str = "success",
    payload: bytes = b"runtime-body",
    declared: int | None = None,
    version_id: str = "runtime-version",
    tls: ssl.SSLContext | None = None,
) -> Iterator[_Server]:
    server = _Server(_Handler)
    server.route = route
    server.payload = payload
    server.declared = len(payload) if declared is None else declared
    server.version_id = version_id
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()
        if route == "stall":
            assert server.stall_done.wait(3)
        if server.handler_errors:
            raise AssertionError("runtime fixture handler failed") from None


def _local_config(server: _Server, *, tls: bool = False) -> dict[str, Any]:
    return {
        "endpoint": f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}",
        "bucket": "runtime-bucket",
        "region": "us-east-1",
        "access_env": "RAW_ACCESS_KEY",
        "secret_env": "RAW_SECRET_KEY",
    }


@contextmanager
def _observe_actual_client_events(
    registrations: tuple[tuple[str, str, Any], ...],
) -> Iterator[None]:
    original_create_client = raw_transport._create_client

    def create_client(
        reader: ExplicitHistoryReader,
        ref: CheckpointVersionRef,
    ) -> Any:
        client = original_create_client(reader, ref)
        for event_name, unique_id, handler in registrations:
            client.meta.events.register(event_name, handler, unique_id=unique_id)
        return client

    raw_transport._create_client = create_client
    try:
        yield
    finally:
        raw_transport._create_client = original_create_client


def _routing_cases(config: dict[str, Any]) -> dict[str, Any]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(config["certificate"], config["private_key"])
    version_id = "null"
    key = "opaque/%2F+雪/../q?#fragment"
    with _server(route="success", payload=b"trusted", version_id=version_id, tls=context) as server:
        local = _local_config(server, tls=True)
        if not config["trusted"]:
            _failure(local, key, version_id, "TRANSPORT_FAILURE")
            assert server.requests == []
            return {
                "case_count": 1,
                "tls": "untrusted-rejected",
                "handler_requests": 0,
                "certifi_path": certifi.where(),
                "certifi_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
            }
        assert _read(local, key, version_id) == b"trusted"
        assert len(server.requests) == 1
        expected = (
            f"/runtime-bucket/{quote(key, safe='/~')}?versionId={quote(version_id, safe='-_.~')}"
        )
        assert server.requests[0]["method"] == "GET"
        assert server.requests[0]["target"] == expected
        assert f"Credential={os.environ['RAW_ACCESS_KEY']}/" in server.requests[0]["authorization"]
        assert "/us-east-1/s3/aws4_request" in server.requests[0]["authorization"]
        assert server.requests[0]["checksum_mode"] == "ENABLED"

    non_null_version = "v%2F+opaque/1"
    non_null_key = "dots/.././雪/%2F+?#"
    with _server(payload=b"opaque", version_id=non_null_version, tls=context) as server:
        local = _local_config(server, tls=True)
        assert _read(local, non_null_key, non_null_version) == b"opaque"
        assert len(server.requests) == 1
        assert server.requests[0]["target"] == (
            f"/runtime-bucket/{quote(non_null_key, safe='/~')}"
            f"?versionId={quote(non_null_version, safe='-_.~')}"
        )
        assert server.requests[0]["checksum_mode"] == "ENABLED"

    unicode_version = "v%2F+版本/1"
    with _server(payload=b"mismatch", version_id="representable-mismatch", tls=context) as server:
        local = _local_config(server, tls=True)
        _failure(local, non_null_key, unicode_version, "VERSION_MISMATCH")
        assert len(server.requests) == 1
        assert server.requests[0]["target"] == (
            f"/runtime-bucket/{quote(non_null_key, safe='/~')}"
            f"?versionId={quote(unicode_version, safe='-_.~')}"
        )
        assert server.requests[0]["checksum_mode"] == "ENABLED"

    redirect_counts = []
    for route in ("redirect-region", "redirect-no-region"):
        with _server() as target, _server(route=route) as original:
            original.redirect = f"http://127.0.0.1:{target.server_port}/forbidden"
            local = _local_config(original)
            _failure(local, "redirect-key", "redirect-version", "ROUTING_REJECTED")
            assert len(original.requests) <= 1
            assert target.requests == []
            redirect_counts.append(len(original.requests))
    return {
        "case_count": 5,
        "tls": "trusted",
        "opaque_target": "exact",
        "redirect_original_counts": redirect_counts,
        "redirect_target_count": 0,
        "certifi_path": certifi.where(),
        "certifi_sha256": hashlib.sha256(Path(certifi.where()).read_bytes()).hexdigest(),
    }


def _routing(config: dict[str, Any]) -> dict[str, Any]:
    original_default_session = boto3.DEFAULT_SESSION
    result: dict[str, Any] | None = None
    try:
        boto3.setup_default_session(
            aws_access_key_id="cached-default-poison-access",
            aws_secret_access_key="cached-default-poison-secret",
            aws_session_token="cached-default-poison-token",
            region_name="ap-southeast-2",
        )
        hostile_default_session = boto3.DEFAULT_SESSION
        assert hostile_default_session is not None
        assert hostile_default_session is not original_default_session
        credentials = hostile_default_session.get_credentials()
        assert credentials is not None
        frozen = credentials.get_frozen_credentials()
        assert frozen.access_key == "cached-default-poison-access"
        assert frozen.secret_key == "cached-default-poison-secret"
        assert frozen.token == "cached-default-poison-token"
        assert hostile_default_session.region_name == "ap-southeast-2"
        result = _routing_cases(config)
    finally:
        boto3.DEFAULT_SESSION = original_default_session
    assert boto3.DEFAULT_SESSION is original_default_session
    assert result is not None
    result["default_session"] = {
        "populated": True,
        "distinct_credentials": True,
        "distinct_region": True,
        "restored": True,
    }
    return result


class _Body:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        block: tuple[threading.Event, threading.Event] | None = None,
    ) -> None:
        self.chunks = iter(chunks)
        self.block = block
        self.closed = False
        self.read_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if self.block is not None:
            entered, release = self.block
            self.block = None
            entered.set()
            assert release.wait(3)
        return next(self.chunks, b"")

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, body: _Body, length: int, version_id: str) -> None:
        self.body = body
        self.length = length
        self.version_id = version_id
        self.closed = False

    def get_object(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "Body": self.body,
            "ContentLength": self.length,
            "VersionId": self.version_id,
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

    def close(self) -> None:
        self.closed = True


def _stream(_config: dict[str, Any]) -> dict[str, Any]:
    cases = 0
    with _server(payload=b"") as server:
        assert _read(_local_config(server), "zero", "runtime-version") == b""
        cases += 1
    with _server(payload=b"x" * 65_536) as server:
        assert len(_read(_local_config(server), "maximum", "runtime-version")) == 65_536
        cases += 1
    with _server(payload=b"x", declared=2) as server:
        _failure(_local_config(server), "early", "runtime-version", "TRANSPORT_FAILURE")
        cases += 1
    with _server(payload=b"", declared=65_537) as server:
        _failure(_local_config(server), "overlimit", "runtime-version", "BODY_LIMIT")
        cases += 1
    encoded = gzip.compress(b"encoded-body")
    with _server(route="encoded", payload=encoded) as server:
        assert _read(_local_config(server), "encoded", "runtime-version") == encoded
        cases += 1
    with _server(route="chunked", payload=b"chunked") as server:
        _failure(_local_config(server), "chunked", "runtime-version", "RESPONSE_INVALID")
        cases += 1
    chunked_peer_disconnects = list(server.expected_peer_disconnects)
    error_buffer_observations: list[dict[str, Any]] = []

    def observe_error_buffer(**kwargs: Any) -> None:
        assert kwargs["operation_model"].name == "GetObject"
        response_dict = kwargs["response_dict"]
        assert type(response_dict) is dict
        body = response_dict["body"]
        assert type(body) is bytes
        error_buffer_observations.append(
            {
                "body_bytes": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
        )

    with _observe_actual_client_events(
        (
            (
                "before-parse.s3.GetObject",
                "audit-raw-observe-error-buffer",
                observe_error_buffer,
            ),
        )
    ):
        with _server(route="provider-error") as server:
            _failure(_local_config(server), "error", "runtime-version", "PROVIDER_FAILURE")
            cases += 1
    expected_error_buffer = {
        "body_bytes": len(_PROVIDER_ERROR_BODY),
        "body_sha256": hashlib.sha256(_PROVIDER_ERROR_BODY).hexdigest(),
    }
    assert error_buffer_observations == [expected_error_buffer]
    checksum = base64.b64encode(hashlib.sha256(b"checksum").digest()).decode()
    with _server(payload=b"checksum") as server:
        server.checksum = checksum
        assert _read(_local_config(server), "checksum", "runtime-version") == b"checksum"
        cases += 1
    bad = base64.b64encode(b"x" * 32).decode()
    with _server(payload=b"checksum") as server:
        server.checksum = bad
        _failure(_local_config(server), "checksum-bad", "runtime-version", "RESPONSE_INVALID")
        cases += 1
    timeout_errors: list[str] = []
    original_stream_read = StreamingBody.read

    def observe_stream_read(
        body: StreamingBody,
        amount: int | None = None,
    ) -> bytes:
        try:
            return original_stream_read(body, amount)
        except ReadTimeoutError as error:
            assert type(error) is ReadTimeoutError
            timeout_errors.append(type(error).__name__)
            raise

    StreamingBody.read = observe_stream_read
    try:
        with _server(route="stall", payload=b"x", declared=1) as server:
            started = time.monotonic()
            _failure(_local_config(server), "stall", "runtime-version", "TRANSPORT_FAILURE")
            timeout_elapsed_ms = round((time.monotonic() - started) * 1_000)
            cases += 1
    finally:
        StreamingBody.read = original_stream_read
    assert timeout_errors == ["ReadTimeoutError"]
    assert timeout_elapsed_ms >= 4_000

    visible = _Body([b"ab"])
    visible_client = _Client(visible, 1, "visible-overrun")
    original = raw_transport._create_client
    raw_transport._create_client = lambda _reader, _ref: visible_client
    try:
        try:
            raw_transport.read_raw_checkpoint_version(
                ExplicitHistoryReader(
                    "http://127.0.0.1:1", "runtime-bucket", "us-east-1", "a", "s"
                ),
                CheckpointVersionRef("visible", "visible-overrun"),
            )
        except raw_transport.RawVersionReadError as error:
            assert error.code == "RESPONSE_INVALID"
        else:
            raise AssertionError("visible stream overrun was accepted")
        assert visible.closed and visible_client.closed
    finally:
        raw_transport._create_client = original
    cases += 1

    with _server(payload=b"ab", declared=1) as server:
        try:
            hidden_result = _read(_local_config(server), "hidden", "runtime-version")
        except raw_transport.RawVersionReadError as error:
            hidden_framing = error.code
        else:
            assert hidden_result == b"a"
            hidden_framing = "not-exposed-by-http-framing"
        cases += 1

    cancel = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    body = _Body([b"x"], block=(entered, release))
    client = _Client(body, 1, "cancel-version")
    original = raw_transport._create_client
    raw_transport._create_client = lambda _reader, _ref: client
    outcomes: list[BaseException] = []

    def owner() -> None:
        try:
            raw_transport.read_raw_checkpoint_version(
                ExplicitHistoryReader(
                    "http://127.0.0.1:1", "runtime-bucket", "us-east-1", "a", "s"
                ),
                CheckpointVersionRef("cancel-key", "cancel-version"),
                cancel=cancel,
            )
        except BaseException as error:  # noqa: BLE001 - prove cancellation base exception
            outcomes.append(error)

    thread = threading.Thread(target=owner)
    try:
        thread.start()
        assert entered.wait(3)
        cancel.set()
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert len(outcomes) == 1
        assert type(outcomes[0]) is raw_transport.RawVersionReadCancelled
        assert body.closed and client.closed
        assert body.read_sizes == [1]
    finally:
        release.set()
        thread.join(3)
        raw_transport._create_client = original
    cases += 1
    return {
        "case_count": cases,
        "categories": [
            "bounds",
            "checksum",
            "content-encoding",
            "error-buffering",
            "framing",
            "read-timeout",
            "released-joined-cancellation",
        ],
        "cleanup_complete": body.closed and client.closed and not thread.is_alive(),
        "chunked_peer_disconnects": chunked_peer_disconnects,
        "error_buffering": {
            "event": "before-parse.s3.GetObject",
            **expected_error_buffer,
            "observed_before_api_return": True,
        },
        "hidden_framing": hidden_framing,
        "socket_timeout": {
            "configured_read_timeout_seconds": 5,
            "elapsed_ms": timeout_elapsed_ms,
            "observation_boundary": "StreamingBody.read",
            "observed_exception": timeout_errors[0],
            "server_hold_seconds": 6.2,
        },
    }


def _certifi_info() -> dict[str, Any]:
    path = Path(certifi.where())
    return {
        "certifi_path": str(path),
        "certifi_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("provider", "routing", "stream", "certifi"))
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    config = _load(arguments.config)
    if arguments.mode == "provider":
        result = _provider(config)
    elif arguments.mode == "routing":
        result = _routing(config)
    elif arguments.mode == "stream":
        result = _stream(config)
    else:
        result = _certifi_info()
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
