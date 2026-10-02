"""The negative TLS fixture may lose its peer before receiving any HTTP request."""

from __future__ import annotations

import io
import ssl
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.integration import audit_version_page_transport_runtime_probe as probe


class _FailedRead:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def readline(self, _limit: int) -> bytes:
        raise self.error


def _handler(error: Exception, *, expect_rejection: bool) -> Any:
    server = object.__new__(probe._Server)
    server.expect_tls_rejection = expect_rejection
    server.requests = []
    server.disconnected = False
    server.finished = threading.Event()
    handler = object.__new__(probe._Handler)
    handler.server = server
    handler.rfile = _FailedRead(error)
    handler.wfile = io.BytesIO()
    handler.close_connection = False
    return handler


def test_expected_tls_rejection_can_close_before_the_first_http_read() -> None:
    # Match the measured kernel failure: TLS accepted, botocore rejected the hostname, then the
    # server's first buffered read raised BrokenPipeError before assigning raw_requestline.
    handler = _handler(BrokenPipeError("synthetic peer closed"), expect_rejection=True)

    handler.handle_one_request()

    assert handler.close_connection
    assert handler.server.disconnected
    assert handler.server.finished.is_set()
    assert handler.server.requests == []


def test_broken_pipe_in_a_normal_fixture_still_fails() -> None:
    error = BrokenPipeError("synthetic unexpected close")
    handler = _handler(error, expect_rejection=False)

    with pytest.raises(BrokenPipeError) as caught:
        handler.handle_one_request()

    assert caught.value is error
    assert not handler.server.disconnected


@pytest.mark.parametrize("request_observed", ["raw-line", "recorded-request"])
def test_tls_rejection_does_not_hide_a_disconnect_after_http_started(request_observed: str) -> None:
    error = BrokenPipeError("synthetic close after request")
    handler = _handler(error, expect_rejection=True)
    if request_observed == "raw-line":
        handler.raw_requestline = b"GET /synthetic HTTP/1.1\r\n"
    else:
        handler.server.requests.append({"method": "GET"})

    with pytest.raises(BrokenPipeError) as caught:
        handler.handle_one_request()

    assert caught.value is error


@pytest.mark.parametrize("error", [ssl.SSLError("synthetic unexpected TLS error"), OSError("I/O")])
def test_other_tls_or_io_failures_are_not_accepted(error: Exception) -> None:
    handler = _handler(error, expect_rejection=True)

    with pytest.raises(type(error)) as caught:
        handler.handle_one_request()

    assert caught.value is error


def test_rejection_opt_in_requires_tls() -> None:
    with pytest.raises(ValueError, match="TLS rejection requires"):
        with probe._server(b"", expect_tls_rejection=True):
            pytest.fail("plaintext fixture accepted a TLS rejection exception")


@pytest.mark.parametrize(
    ("host", "trusted"), [("127.0.0.1", True), ("127.0.0.2", True), ("127.0.0.1", False)]
)
def test_real_tls_still_requires_trust_and_no_http_on_hostname_rejection(
    host: str, trusted: bool, tmp_path: Path
) -> None:
    from contextlib import closing

    import boto3
    from botocore.config import Config
    from botocore.exceptions import SSLError

    from tests.integration.audit_raw_runtime_acceptance import _certificate_material

    ca, certificate, private = _certificate_material()
    if not trusted:
        _, certificate, private = _certificate_material()
    ca_path = tmp_path / "ca.pem"
    certificate_path = tmp_path / "certificate.pem"
    private_path = tmp_path / "private.pem"
    ca_path.write_bytes(ca)
    certificate_path.write_bytes(certificate)
    private_path.write_bytes(private)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate_path, private_path)
    reject = host == "127.0.0.2" or not trusted
    with probe._server(probe._page(), tls=tls, host=host, expect_tls_rejection=reject) as server:
        with closing(
            boto3.client(
                "s3",
                endpoint_url=f"https://{host}:{server.server_port}",
                aws_access_key_id="synthetic-access",
                aws_secret_access_key="synthetic-secret",
                region_name="us-east-1",
                verify=str(ca_path),
                config=Config(proxies={}, retries={"total_max_attempts": 1}),
            )
        ) as client:
            if reject:
                with pytest.raises(SSLError):
                    client.list_object_versions(Bucket="synthetic-bucket")
                assert server.requests == []
            else:
                response = client.list_object_versions(Bucket="synthetic-bucket")
                assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
                assert len(server.requests) == 1
    assert server.errors == []
