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
chain only from a peer the operator has declared a proxy** (``TRUSTED_PROXY_CIDRS``).

Where the chain cannot be read at all the answer is ``None`` — an honest "unknown" in an audit
row, where a guess would be a lie. ``None`` is **not** universally fail-safe, and it must not be
reachable from caller-controlled input: the PDP drops any grant whose ``ip_allow`` cannot be
satisfied, and it applies that filter to DENY grants too, so an unknown address suppresses an
``ip_allow``-carrying DENY exactly as it fails an ALLOW. Every branch below that yields ``None``
is therefore reserved for input only our own edge can produce.
"""

from __future__ import annotations

import ipaddress
import logging

from fastapi import Request

from ...config import IpNetwork, get_settings

logger = logging.getLogger(__name__)

# Latched: this reports a standing deployment fact, and the condition is caller-reachable.
_WARNED_UNTRUSTED_FORWARDER = False
_WARNED_ALL_HOPS_TRUSTED = False

# The walk is linear in the number of hops and the header is caller-influenced, so the work is
# bounded. The bound applies to the WALK rather than to the header's length: reading right-to-left
# means a caller can only prepend entries that are never reached, so refusing a long header would
# let pure padding turn a perfectly readable chain into "unknown" — and an unknown address is not
# a neutral outcome (see the module docstring). Real chains are a handful of hops.
_MAX_FORWARDED_HOPS = 32


class _AllHopsTrusted(str):
    """The peer, returned because every forwarded hop was itself a trusted proxy.

    A plain ``str`` so every consumer treats it as the address it is; the distinct type exists
    only so the adapter can recognise the one remaining case where the application attributes a
    request to the proxy and say so, instead of failing silently.
    """

    __slots__ = ()


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
    # A dual-stack listener reports an IPv4 peer as ``::ffff:10.0.0.1``, which no IPv4 network
    # contains. Comparing the mapped form keeps such a peer trusted; the normalisation is confined
    # to this decision and never touches the value returned, which must stay lossless.
    mapped = getattr(address, "ipv4_mapped", None)
    candidates = (address, mapped) if mapped is not None else (address,)
    return any(c in network for network in trusted for c in candidates)


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

    # Walk right-to-left over at most the last _MAX_FORWARDED_HOPS entries: each proxy writes the
    # peer it saw at the right-hand end, so the rightmost entry is the nearest proxy's own
    # observation and anything further left is progressively less vouched for. Skipping trusted
    # hops and stopping at the first untrusted one yields the outermost address that a proxy we
    # trust actually observed — never a value the caller chose, since a caller can only prepend.
    for hop in reversed(forwarded.split(",")[-_MAX_FORWARDED_HOPS:]):
        address = _as_address(hop)
        if address is None:
            # A malformed hop makes the rest of the chain unreadable: we cannot tell whether the
            # entries beyond it are proxies or forgeries, so the chain is refused entirely.
            return None
        if _is_trusted(address, trusted):
            continue
        return address

    # Every hop we read was a trusted proxy, so no client address was ever observed and the
    # nearest thing we know is the peer. ``None`` marks this so the caller can report it: a
    # healthy topology does not reach here, and attributing a request to the proxy is exactly the
    # misconfiguration this module exists to remove.
    return _AllHopsTrusted(peer)


def client_ip(request: Request) -> str | None:
    """The address to attribute ``request`` to, under the configured proxy allowlist.

    ``X-Forwarded-For`` may legitimately arrive as several header lines, which RFC 7230 defines
    as equivalent to one comma-joined line. Reading only the first — what ``headers.get`` returns
    — would be exploitable rather than merely incomplete: a caller can send its own line and let
    the proxy append to another, so the line we read would be entirely of its choosing.
    """
    trusted = get_settings().trusted_proxy_networks
    peer = request.client.host if request.client else None
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    resolved = resolve_client_ip(peer, forwarded, trusted)

    if resolved is None and peer is not None:
        # A peer we DO trust forwarded a chain we cannot read: a signal about our own edge, not
        # about the caller. Downstream the unknown address blanks the audit attribution and stops
        # an ip_allow grant of either effect from matching.
        logger.warning(
            "client_ip.unreadable_forwarded_chain",
            extra={"peer": peer, "forwarded_hops": len(forwarded.split(","))},
        )
    elif isinstance(resolved, _AllHopsTrusted):
        # Every hop the edge forwarded was itself inside the trusted set, so the request is being
        # attributed to the proxy. Either the trusted set is wider than the actual proxies — and
        # is therefore discarding real client addresses — or the edge is not forwarding what we
        # think. Both are silent otherwise, and both defeat ip_allow entirely.
        global _WARNED_ALL_HOPS_TRUSTED
        if not _WARNED_ALL_HOPS_TRUSTED:
            _WARNED_ALL_HOPS_TRUSTED = True
            logger.warning("client_ip.every_forwarded_hop_is_trusted", extra={"peer": peer})
    elif forwarded and peer is not None and not _is_trusted(peer, trusted):
        # The far more likely misconfiguration, and the one that was previously silent: something
        # is forwarding a client address and we are ignoring it because the peer is not on the
        # allowlist. That is correct behaviour for an unknown caller and a serious deployment
        # fault if the peer is in fact the fronting proxy — every request then gets attributed to
        # the proxy and every ip_allow grant denies, with a green health check throughout. Warned
        # once per process because an untrusted caller can trigger it at will.
        global _WARNED_UNTRUSTED_FORWARDER
        if not _WARNED_UNTRUSTED_FORWARDER:
            _WARNED_UNTRUSTED_FORWARDER = True
            logger.warning(
                "client_ip.forwarded_header_from_untrusted_peer",
                extra={"peer": peer},
            )
    return resolved
