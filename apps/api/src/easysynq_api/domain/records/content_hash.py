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
* A **domain-separation preamble** binds the version in, so a record digest can never collide with
  an audit digest or another record-hash version.

**v1 compatibility.** The original v1 serializer normalized a falsey
``form_field_values`` value with ``value or None``. Existing records therefore retain
``content_hash_version=1`` and must continue to collapse ``{}`` to ``null`` when re-verified. New
captures use v2, whose preimage preserves ``{}`` as the valid empty structured-form submission and
keeps it distinct from the ``null`` ad-hoc-record sentinel. Migration ``0078`` persists that
algorithm choice beside every record so verification never has to guess from the digest.

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

CONTENT_HASH_VERSION_V1 = 1
CONTENT_HASH_VERSION = 2
PREAMBLE_V1 = b"easysynq.record.v1\n"
PREAMBLE_V2 = b"easysynq.record.v2\n"
PREAMBLE = PREAMBLE_V2


def record_content_hash(
    *,
    record_type: str,
    source_version_id: uuid.UUID | None,
    form_field_values: dict[str, Any] | None,
    evidence_sha256s: Iterable[str],
    version: int = CONTENT_HASH_VERSION,
) -> str:
    """Return the ``"sha256:"``-prefixed content seal for a record (doc 06 §4.4).

    Deterministic and order-/duplicate-independent in the evidence manifest and the form values.
    Re-verification must pass the record's persisted ``content_hash_version``; v1 deliberately
    retains its historical ``{}``-to-``null`` normalization, while v2 preserves ``{}``.
    """
    if version == CONTENT_HASH_VERSION_V1:
        preamble = PREAMBLE_V1
        canonical_form_values = form_field_values or None
    elif version == CONTENT_HASH_VERSION:
        preamble = PREAMBLE_V2
        canonical_form_values = form_field_values
    else:
        raise ValueError(f"unsupported record content hash version: {version}")

    obj: dict[str, Any] = {
        "v": version,
        "record_type": record_type,
        "source_version_id": (
            str(source_version_id).lower() if source_version_id is not None else None
        ),
        "form_field_values": canonical_form_values,
        "evidence_manifest": sorted({s.lower() for s in evidence_sha256s}),
    }
    payload = preamble + rfc8785.dumps(obj)
    return "sha256:" + hashlib.sha256(payload).hexdigest()
