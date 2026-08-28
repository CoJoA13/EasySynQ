"""The address a request actually came from, resolved across a configured proxy chain.

Every authorization and audit consumer of a "source IP" reads it from here, so the whole
application agrees on one answer to one question: *which address do we attribute this request
to?* Two failures made a single shared answer necessary.

**The forwarded header was believed unconditionally.** Two hand-rolled copies of this logic took
the rightmost ``X-Forwarded-For`` entry whenever the header was present. That is right behind a
proxy that appends, and wrong everywhere else: on a direct connection the whole header is
attacker-supplied, so any caller could write an arbitrary address into an immutable download or
acknowledgement evidence row.

**The socket peer was believed unconditionally everywhere else.** The remaining call sites read
``request.client.host`` directly. Behind the shipped Caddy that is always the proxy's own
address, so the PDP's ``ip_allow`` predicate — a *narrowing* ABAC predicate an administrator
attaches to a grant — could never match a real client. It did not merely fail to narrow; it
denied universally, which reads to an operator as a broken grant rather than a broken topology.

The rule is therefore symmetric, and the trust decision comes first: **believe the forwarded
chain only from a peer the operator has declared a proxy** (``TRUSTED_PROXY_CIDRS``). Ambiguity
resolves to ``None``, which is fail-safe in both directions — the PDP already refuses an
``ip_allow`` grant when ``source_ip`` is ``None``, and ``None`` is an honest "unknown" in an
audit row where a guess would be a lie.
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

from ...config import IpNetwork, get_settings

logger = logging.getLogger(__name__)

# A forged X-Forwarded-For is unbounded attacker input and the walk below is linear in the
# number of hops. Real chains are a handful; anything past these bounds is refused outright
# rather than trusted or truncated (truncating would let padding hide the real entry).
_MAX_FORWARDED_LENGTH = 2048
_MAX_FORWARDED_HOPS = 32


def _is_trusted(text: str, trusted: tuple[IpNetwork, ...]) -> bool:
    """Whether ``text`` parses as an address inside one of the trusted networks.

    An unparseable value is never trusted. Mixed address families simply do not match
    (``ipaddress`` containment is False across versions), so an IPv6 peer is not accidentally
    admitted by an IPv4 network.
    """
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(address in network for network in trusted)


def _as_address(entry: str) -> str | None:
    """A forwarded-chain hop as a bare address, or ``None`` if it is not one.

    Bracketed IPv6 (``[2001:db8::1]``) is unwrapped; everything else must already be a bare
    address. The text is returned as written rather than canonicalized: ``ip_allow`` matches
    by exact string and the evidence-pack replay column stores this value losslessly for that
    reason, so normalizing here would silently change what an operator's allowlist must contain.
    """
    token = entry.strip()
    if token.startswith("[") and token.endswith("]"):
        token = token[1:-1]
    if not token:
        return None
    try:
        ipaddress.ip_address(token)
    except ValueError:
        return None
    return token


def resolve_client_ip(
    peer: str | None,
    forwarded: str | None,
    trusted: tuple[IpNetwork, ...],
) -> str | None:
    """Attribute a request to an address, given its socket peer and forwarded chain.

    Pure and session-free so the whole decision table is unit-testable without a request,
    a database, or settings.
    """
    if peer is None:
        return None

    if not _is_trusted(peer, trusted):
        # The peer is the client itself, or a hop the operator has not vouched for. Either way
        # the forwarded header is unverifiable here, so it is ignored rather than believed.
        # The peer is returned exactly as the socket reported it — including a non-address
        # placeholder such as a test transport's, which is honest and cannot match an
        # ip_allow entry.
        return peer

    if not forwarded:
        # A trusted proxy that forwarded nothing: the proxy is all we know.
        return peer
    if len(forwarded) > _MAX_FORWARDED_LENGTH:
        return None

    hops = forwarded.split(",")
    if len(hops) > _MAX_FORWARDED_HOPS:
        return None

    # Walk right-to-left: each proxy APPENDS the peer it saw, so the rightmost entry is the one
    # written by the nearest proxy and the leftmost is whatever the original caller sent. Skipping
    # trusted hops and stopping at the first untrusted one yields the outermost address that a
    # proxy we trust actually observed — never a value the client chose.
    for hop in reversed(hops):
        address = _as_address(hop)
        if address is None:
            # A malformed hop makes the rest of the chain unreadable: we cannot tell whether the
            # entries beyond it are proxies or forgeries, so the chain is refused entirely.
            return None
        if _is_trusted(address, trusted):
            continue
        return address

    # Every hop was a trusted proxy, so no client address was ever recorded.
    return peer


def client_ip(request: Request) -> str | None:
    """The address to attribute ``request`` to, under the configured proxy allowlist.

    ``X-Forwarded-For`` may legitimately arrive as several header lines, which RFC 7230 defines
    as equivalent to one comma-joined line. Reading only the first — what ``headers.get`` returns
    — would be exploitable rather than merely incomplete: a caller can send its own line and let
    the proxy append to another, so the line we read would be entirely of its choosing.
    """
    peer = request.client.host if request.client else None
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    resolved = resolve_client_ip(peer, forwarded, get_settings().trusted_proxy_networks)
    if resolved is None and peer is not None:
        # Only reachable when a peer we DO trust forwarded a chain we cannot read, so this is a
        # signal about our own edge rather than about the caller. Worth a warning: downstream the
        # unknown address silently denies every ip_allow grant and blanks the audit attribution.
        logger.warning(
            "client_ip.unreadable_forwarded_chain",
            extra={"peer": peer, "forwarded_hops": len(forwarded.split(","))},
        )
    return resolved
