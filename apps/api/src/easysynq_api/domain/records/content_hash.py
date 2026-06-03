"""``record_content_hash`` — the deterministic seal over a captured record's immutable identity
(slice S-rec-1, doc 06 §3/§4.4).

A record is sealed at capture: ``content_hash`` is a SHA-256 over the canonical serialization of the
record's structured content **and the manifest of its attached blob digests** (doc 06 §4.4). Frozen
at capture, re-verified on a schedule (the deferred integrity job). This is a SEPARATE serializer
from the FROZEN audit ``canonical_serialize`` (``services/audit/canonical.py``) — never touch that —
but it borrows the same two safety properties:

* **RFC 8785 JCS** (the ``rfc8785`` package the codebase already depends on) gives deterministic key
  ordering + number/string encoding, so ``form_field_values`` serializes identically regardless of
  insertion order.
* A **domain-separation preamble** (``b"easysynq.record.v1\\n"``) binds the version in, so a record
  digest can never collide with an audit digest or a future v2.

The ``evidence_manifest`` is ``sorted(set(...))`` of lowercased sha256s — re-attaching the blobs in
any order (or a duplicate) yields the identical seal. The preimage deliberately EXCLUDES the
mutable-by-design columns (``superseded_by_correction`` / ``disposition_state`` / ``legal_hold``) so
the correction pointer-flip and a future disposition advance never invalidate a sealed record.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from typing import Any

import rfc8785

CONTENT_HASH_VERSION = 1
PREAMBLE = b"easysynq.record.v1\n"


def record_content_hash(
    *,
    record_type: str,
    source_version_id: uuid.UUID | None,
    form_field_values: dict[str, Any] | None,
    evidence_sha256s: Iterable[str],
) -> str:
    """Return the ``"sha256:"``-prefixed content seal for a record (doc 06 §4.4).

    Deterministic and order-/duplicate-independent in the evidence manifest and the form values.
    """
    obj: dict[str, Any] = {
        "v": CONTENT_HASH_VERSION,
        "record_type": record_type,
        "source_version_id": (
            str(source_version_id).lower() if source_version_id is not None else None
        ),
        "form_field_values": form_field_values or None,
        "evidence_manifest": sorted({s.lower() for s in evidence_sha256s}),
    }
    payload = PREAMBLE + rfc8785.dumps(obj)
    return "sha256:" + hashlib.sha256(payload).hexdigest()
