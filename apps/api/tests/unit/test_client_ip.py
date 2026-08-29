"""The trusted-proxy client-IP resolution table.

Supersedes the Audit-U21 rule that the RIGHTMOST ``X-Forwarded-For`` entry is always the client.
That reading is correct only behind a proxy that appends, and this application cannot assume one
is there: on a direct connection the entire header is caller-supplied, so U21's rule let any
caller write an address of its choosing into immutable acknowledgement and pack-download
evidence. The trust decision now comes first, and the same decision also repairs the opposite
failure — reading the socket peer everywhere else meant the PDP's ``ip_allow`` predicate saw the
proxy's own address and denied universally instead of narrowing.

Every assertion below asks the same question — which address do we attribute this request to —
under a different topology. Addresses are the sanctioned R61 placeholder ranges only.
"""

import ipaddress
import logging
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from easysynq_api.config import Settings, parse_trusted_proxy_cidrs
from easysynq_api.services.common import client_ip as client_ip_module
from easysynq_api.services.common.client_ip import client_ip, resolve_client_ip

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[4]
# The subnet infra/compose/compose.yml pins for the `internal` network. Named once here so the
# exclusion probes below are derived from it rather than hand-written.
_COMPOSE_SUBNET = ipaddress.ip_network("172.16.0.0/24")

PROXY = "10.0.0.1"
OTHER_PROXY = "10.0.0.2"
CLIENT = "203.0.113.7"
FORGED = "198.51.100.9"

TRUSTED = parse_trusted_proxy_cidrs("10.0.0.0/24")
NOTHING_TRUSTED: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = ()


# --- the default posture: nothing is trusted, so the header is not evidence -------------------


def test_forwarded_header_is_ignored_when_no_proxy_is_trusted():
    # The whole header is caller-supplied on a direct connection. With an empty allowlist the
    # socket peer is the only thing the server observed itself.
    assert resolve_client_ip(CLIENT, FORGED, NOTHING_TRUSTED) == CLIENT


def test_forwarded_header_is_ignored_from_an_untrusted_peer():
    # A proxy the operator has not vouched for is indistinguishable from a client that decided
    # to send the header.
    assert resolve_client_ip(OTHER_PROXY, FORGED, parse_trusted_proxy_cidrs("192.0.2.0/24")) == (
        OTHER_PROXY
    )


def test_no_peer_is_unknown():
    assert resolve_client_ip(None, CLIENT, TRUSTED) is None


# --- behind a trusted proxy: the chain becomes readable ---------------------------------------


def test_trusted_proxy_without_a_chain_yields_the_proxy():
    assert resolve_client_ip(PROXY, None, TRUSTED) == PROXY
    assert resolve_client_ip(PROXY, "", TRUSTED) == PROXY


def test_trusted_proxy_yields_the_forwarded_client():
    assert resolve_client_ip(PROXY, CLIENT, TRUSTED) == CLIENT


def test_a_client_forged_prefix_cannot_displace_the_address_the_proxy_appended():
    # Caddy APPENDS the peer it saw, so a caller who sends "X-Forwarded-For: <forged>" arrives as
    # "<forged>, <real>". Reading the chain right-to-left is what makes the forgery inert; the
    # leftmost entry — which the old hand-rolled helpers' sibling reading would have taken — is
    # exactly the attacker's value.
    assert resolve_client_ip(PROXY, f"{FORGED}, {CLIENT}", TRUSTED) == CLIENT


def test_multiple_trusted_hops_are_skipped_to_the_outermost_untrusted_address():
    # An operator front-door plus Caddy: both are declared, so neither is the client.
    assert resolve_client_ip(PROXY, f"{FORGED}, {CLIENT}, {OTHER_PROXY}", TRUSTED) == CLIENT


def test_a_chain_of_only_trusted_hops_reports_the_proxy():
    # Nothing untrusted was ever observed, so there is no client address to report.
    assert resolve_client_ip(PROXY, OTHER_PROXY, TRUSTED) == PROXY


# --- ambiguity resolves to unknown, never to a guess ------------------------------------------


def test_a_malformed_hop_makes_the_whole_chain_unusable():
    # Past a garbage entry we cannot tell proxies from forgeries, so nothing beyond it is
    # trustworthy. None is fail-safe: the PDP refuses an ip_allow grant on a null source_ip.
    assert resolve_client_ip(PROXY, f"{CLIENT}, not-an-address, {OTHER_PROXY}", TRUSTED) is None


def test_padding_cannot_turn_a_readable_chain_into_an_unknown_address():
    # The load-bearing case, and the one the earlier header-length bound got wrong. A caller can
    # only PREPEND entries, and the walk reads from the right, so a padded chain is still readable
    # at the end that matters. Refusing it outright would have handed any caller a way to force
    # "unknown" — which is not a neutral outcome: the PDP drops an ip_allow grant it cannot
    # satisfy, DENY grants included, so an unknown address suppresses an ip_allow DENY.
    padded = ", ".join([FORGED] * 64 + [CLIENT])
    assert resolve_client_ip(PROXY, padded, TRUSTED) == CLIENT
    assert resolve_client_ip(PROXY, ("198.51.100.9," * 4096) + CLIENT, TRUSTED) == CLIENT


def test_the_walk_stops_at_its_budget_rather_than_reading_an_unbounded_chain():
    """Bounded work on caller-influenced input.

    The budget is only observable when something untrusted lies BEYOND it: a chain of trusted hops
    alone exhausts either way and yields the proxy, so asserting that shape would pass against no
    bound at all. Here the untrusted entry sits past the budget and must stay unread — the answer
    is the proxy, the last thing actually examined.
    """
    beyond_budget = ", ".join([CLIENT] + [OTHER_PROXY] * 64)
    assert resolve_client_ip(PROXY, beyond_budget, TRUSTED) == PROXY
    # Anti-vacuity: the same entry inside the budget IS read.
    within_budget = ", ".join([CLIENT] + [OTHER_PROXY] * 4)
    assert resolve_client_ip(PROXY, within_budget, TRUSTED) == CLIENT


def test_bounds_admit_a_realistic_chain():
    # Anti-tautology for the two bounds above: an ordinary multi-hop chain still resolves.
    chain = ", ".join([OTHER_PROXY] * 4 + [CLIENT] + [OTHER_PROXY] * 4)
    assert resolve_client_ip(PROXY, chain, TRUSTED) == CLIENT


# --- representation is preserved, because ip_allow matches by exact string --------------------


def test_a_non_address_peer_is_returned_verbatim_and_suppresses_the_chain():
    # A test transport's placeholder peer is not an address, so it can never be inside a trusted
    # network — the header stays ignored and the peer is reported exactly as the socket gave it.
    assert resolve_client_ip("testclient", CLIENT, TRUSTED) == "testclient"


def test_a_forwarded_address_is_not_canonicalized():
    # ip_allow compares strings and the evidence-pack replay column stores this value losslessly
    # for that reason; normalizing here would silently change what an allowlist must contain.
    expanded = "2001:db8:0:0:0:0:0:1"
    assert resolve_client_ip(PROXY, expanded, TRUSTED) == expanded


def test_a_bracketed_ipv6_hop_is_unwrapped():
    assert resolve_client_ip(PROXY, "[2001:db8::1]", TRUSTED) == "2001:db8::1"


def test_an_ipv4_network_does_not_admit_an_ipv6_peer():
    # Mixed-family containment must not accidentally trust a v6 peer against a v4 allowlist.
    assert resolve_client_ip("2001:db8::1", CLIENT, TRUSTED) == "2001:db8::1"


def test_an_ipv6_proxy_can_be_trusted_by_an_ipv6_network():
    trusted = parse_trusted_proxy_cidrs("2001:db8::/32")
    assert resolve_client_ip("2001:db8::1", CLIENT, trusted) == CLIENT


# --- the setting -------------------------------------------------------------------------------


def test_parse_accepts_networks_bare_addresses_and_whitespace():
    parsed = parse_trusted_proxy_cidrs(" 10.0.0.0/24 , 192.0.2.5 ,, 2001:db8::/32 ")
    assert parsed == (
        ipaddress.ip_network("10.0.0.0/24"),
        ipaddress.ip_network("192.0.2.5/32"),
        ipaddress.ip_network("2001:db8::/32"),
    )


def test_parse_empty_trusts_nothing():
    assert parse_trusted_proxy_cidrs("") == ()
    assert parse_trusted_proxy_cidrs("  ,  ") == ()


def test_a_bare_address_trusts_only_that_host():
    trusted = parse_trusted_proxy_cidrs("10.0.0.1")
    assert resolve_client_ip(PROXY, CLIENT, trusted) == CLIENT
    assert resolve_client_ip(OTHER_PROXY, CLIENT, trusted) == OTHER_PROXY


def test_a_host_address_carrying_a_prefix_is_masked_to_its_network():
    assert parse_trusted_proxy_cidrs("10.0.0.7/24") == (ipaddress.ip_network("10.0.0.0/24"),)


def test_a_malformed_entry_refuses_the_settings_object():
    # Loud at construction — and Settings is built at import time, so this is a startup failure.
    # A silently-narrowed allowlist would leave the proxy untrusted and reproduce exactly the
    # universally-denying ip_allow this setting exists to fix.
    with pytest.raises(ValueError):
        parse_trusted_proxy_cidrs("10.0.0.0/24, not-a-cidr")
    with pytest.raises(ValidationError):
        Settings(trusted_proxy_cidrs="not-a-cidr")


# --- the FastAPI adapter: peer, header assembly, and settings wiring ---------------------------


def _request(headers: dict[str, str] | list[tuple[str, str]], client: tuple[str, int] | None):
    """A minimal ASGI request. ``headers`` may be a list so one field name can repeat."""
    items = headers if isinstance(headers, list) else list(headers.items())
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in items],
            "client": client,
        }
    )


@pytest.fixture
def trusting(monkeypatch):
    """Point the adapter at a chosen allowlist without touching the process environment."""

    def _apply(spec: str) -> None:
        settings = Settings(trusted_proxy_cidrs=spec)
        monkeypatch.setattr(client_ip_module, "get_settings", lambda: settings)

    return _apply


def test_the_adapter_reads_the_peer_when_the_allowlist_is_empty(trusting):
    trusting("")
    assert client_ip(_request({"x-forwarded-for": FORGED}, (CLIENT, 40000))) == CLIENT
    assert client_ip(_request({}, (CLIENT, 40000))) == CLIENT
    assert client_ip(_request({}, None)) is None


def test_the_adapter_believes_a_trusted_proxys_chain(trusting):
    trusting("10.0.0.0/24")
    assert client_ip(_request({"x-forwarded-for": f"{FORGED}, {CLIENT}"}, (PROXY, 40000))) == CLIENT


def test_a_repeated_header_field_cannot_shadow_the_proxys_line(trusting):
    # RFC 7230 makes repeated field lines equivalent to one comma-joined line, and a caller can
    # send its own line while the proxy appends to another. Reading only the first line — what
    # headers.get() returns — would hand the caller the whole answer, so every line is joined
    # and the walk still ends at the address the proxy itself observed.
    trusting("10.0.0.0/24")
    request = _request(
        [("x-forwarded-for", FORGED), ("x-forwarded-for", CLIENT)],
        (PROXY, 40000),
    )
    assert client_ip(request) == CLIENT


def test_an_unreadable_chain_from_a_trusted_peer_is_reported(trusting, caplog):
    # Only our own edge can produce this, and downstream it silently denies every ip_allow grant
    # and blanks the audit attribution — so it must not pass in silence.
    trusting("10.0.0.0/24")
    with caplog.at_level(logging.WARNING):
        assert client_ip(_request({"x-forwarded-for": "junk"}, (PROXY, 40000))) is None
    assert "client_ip.unreadable_forwarded_chain" in caplog.text


def test_an_ordinary_resolution_stays_silent(trusting, caplog):
    trusting("10.0.0.0/24")
    with caplog.at_level(logging.WARNING):
        assert client_ip(_request({"x-forwarded-for": CLIENT}, (PROXY, 40000))) == CLIENT
    assert caplog.text == ""


# --- the shipped default ------------------------------------------------------------------------


def test_the_shipped_default_is_exactly_the_pinned_compose_subnet_and_loopback():
    # An empty default would attribute every acknowledgement and pack download to the proxy on any
    # deployment that upgrades without editing .env — worse than the behaviour it replaces — so
    # the default must be non-empty AND must name the network Caddy is actually on.
    networks = Settings().trusted_proxy_networks
    assert ipaddress.ip_address("127.0.0.1") in networks[0]
    assert any(ipaddress.ip_address("::1") in n for n in networks)
    assert _COMPOSE_SUBNET in networks


def test_the_shipped_default_does_not_spill_into_neighbouring_private_space():
    """The property that matters is narrowness, and it has to be proved against addresses the
    default might plausibly have swallowed — not against ranges it obviously excludes.

    An address inside a trusted network is read as a proxy hop and SKIPPED, so a default one size
    too wide silently discards the real client address of any site whose clients or VPN sit in
    that space. The probes are derived from the pinned subnet rather than written as literals, so
    widening the default to its enclosing /16 or /12 fails here immediately.
    """
    networks = Settings().trusted_proxy_networks
    base = int(_COMPOSE_SUBNET.network_address)
    probes = {
        "the next /24 up": base + 256,
        "elsewhere in the enclosing /16": base + (200 << 8),
        "the top of the enclosing /12 (a common cloud VPC range)": base + (15 << 16),
    }
    for label, value in probes.items():
        address = ipaddress.ip_address(value)
        assert not any(address in n for n in networks), f"default is too wide: {label}"


def test_the_default_agrees_with_the_subnet_compose_actually_creates():
    """Two files have to say the same thing, and nothing else would notice if they drifted.

    The subnet is pinned in Compose precisely so the trusted set can name it; if someone changes
    one and not the other, Caddy stops being trusted and every request silently reverts to being
    attributed to the proxy, behind a green health check.
    """
    compose = (_REPO_ROOT / "infra/compose/compose.yml").read_text()
    declared = re.search(r"^\s*- subnet:\s*(\S+)\s*$", compose, re.MULTILINE)
    assert declared is not None, "infra/compose/compose.yml no longer pins the internal subnet"
    assert ipaddress.ip_network(declared.group(1)) == _COMPOSE_SUBNET
    assert ipaddress.ip_network(declared.group(1)) in Settings().trusted_proxy_networks


def test_the_test_transports_peer_resolves_under_the_shipped_default():
    # httpx's ASGITransport reports 127.0.0.1 and sends no forwarded header, so the whole
    # integration suite depends on a trusted peer with no chain returning that peer.
    assert resolve_client_ip("127.0.0.1", None, Settings().trusted_proxy_networks) == "127.0.0.1"
    # And the expanded-IPv6 loopback the pack replay test pins must survive byte-identically.
    expanded = "0:0:0:0:0:0:0:1"
    assert resolve_client_ip(expanded, None, Settings().trusted_proxy_networks) == expanded


def test_a_dual_stack_peer_is_still_recognised_as_the_proxy():
    # A listener bound to :: reports an IPv4 peer in mapped form, which no IPv4 network contains.
    # Without normalising the comparison the proxy would quietly stop being trusted and every
    # request would revert to being attributed to it. The returned value stays lossless.
    assert resolve_client_ip("::ffff:10.0.0.1", CLIENT, TRUSTED) == CLIENT
    assert resolve_client_ip("::ffff:10.0.0.1", None, TRUSTED) == "::ffff:10.0.0.1"


def test_a_forwarded_header_we_are_ignoring_is_reported_once(trusting, caplog):
    """The misconfiguration that actually happens, and that was previously silent.

    If the fronting proxy is not on the allowlist the header is correctly ignored — and every
    request is then attributed to the proxy while every ip_allow grant denies, with nothing in
    the logs and a green health check. The opposite branch (a trusted peer forwarding an
    unreadable chain) cannot occur behind the shipped edge, so on its own it warned about the
    case that never happens.
    """
    trusting("")
    client_ip_module._WARNED_UNTRUSTED_FORWARDER = False
    with caplog.at_level(logging.WARNING):
        assert client_ip(_request({"x-forwarded-for": FORGED}, (CLIENT, 40000))) == CLIENT
    assert "client_ip.forwarded_header_from_untrusted_peer" in caplog.text

    # Latched: an untrusted caller can trigger this at will, so it must not flood the log.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert client_ip(_request({"x-forwarded-for": FORGED}, (CLIENT, 40000))) == CLIENT
    assert caplog.text == ""


def test_no_warning_when_the_edge_is_configured_correctly(trusting, caplog):
    trusting("10.0.0.0/24")
    client_ip_module._WARNED_UNTRUSTED_FORWARDER = False
    with caplog.at_level(logging.WARNING):
        assert client_ip(_request({"x-forwarded-for": CLIENT}, (PROXY, 40000))) == CLIENT
    assert caplog.text == ""
