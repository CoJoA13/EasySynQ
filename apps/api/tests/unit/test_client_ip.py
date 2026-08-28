"""[Audit U21] The client-IP recorded into immutable evidence (acknowledgement rows, the
PACK_DOWNLOADED audit) must come from the RIGHTMOST X-Forwarded-For entry — the value the
trusted fronting proxy appended for the actual peer. The leftmost entry is client-controlled:
a forged header would write an arbitrary IP into Part-11-adjacent columns."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from easysynq_api.api.pack_share import _client_ip as pack_client_ip
from easysynq_api.services.ack.decide import _client_ip as ack_client_ip


def _request(headers: dict[str, str], client: tuple[str, int] | None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


@pytest.mark.parametrize("client_ip", [pack_client_ip, ack_client_ip])
def test_forged_leftmost_xff_is_ignored(client_ip) -> None:  # type: ignore[no-untyped-def]
    # Modern Caddy (>=2.5) REPLACES an untrusted client's XFF with the single real-peer entry;
    # older 2.x appended — the rightmost entry is the true client under both behaviors.
    req = _request({"x-forwarded-for": "198.51.100.66, 10.0.0.7"}, ("192.0.2.2", 40000))
    assert client_ip(req) == "10.0.0.7"


@pytest.mark.parametrize("client_ip", [pack_client_ip, ack_client_ip])
def test_single_xff_entry_is_used(client_ip) -> None:  # type: ignore[no-untyped-def]
    req = _request({"x-forwarded-for": "10.0.0.7"}, ("192.0.2.2", 40000))
    assert client_ip(req) == "10.0.0.7"


@pytest.mark.parametrize("client_ip", [pack_client_ip, ack_client_ip])
def test_no_header_falls_back_to_the_socket_peer(client_ip) -> None:  # type: ignore[no-untyped-def]
    assert client_ip(_request({}, ("192.0.2.5", 40000))) == "192.0.2.5"
    assert client_ip(_request({}, None)) is None
