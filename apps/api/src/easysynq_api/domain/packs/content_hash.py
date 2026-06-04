"""``pack_content_hash`` — the deterministic seal over an evidence pack's content (S-pack-1, §7.4).

An evidence pack is "immutable & self-verifying": the cover sheet carries the pack's "own SHA-256",
and an auditor can re-verify item hashes against the manifest. This computes that seal over the
pack's **logical content list** (NOT the assembled ZIP bytes — ZIP layout is non-deterministic) so
two builds of the same content produce the identical hash and the cover can embed the value without
a chicken-and-egg.

This is a SEPARATE serializer from both the frozen audit ``canonical_serialize`` and the records
``record_content_hash`` — it borrows the same two safety properties:

* **RFC 8785 JCS** (the ``rfc8785`` dep) for deterministic key ordering + number/string encoding.
* A **distinct domain-separation preamble** (``b"easysynq.evidencepack.v1\\n"``) so a pack digest
  can never collide with a record digest, an audit digest, or a future v2.

The preimage is the resolved, scope-bounded membership: the scope definition + period, the sorted
included record ids, the sorted pinned governing version ids, the sorted-set evidence manifest
(every included record-evidence sha + each pinned version's source/rendition sha), and the R28
exclusion classification (so a pack that drops items is sealed *as* a pack that dropped those
items). All id/sha collections are ``sorted(set(...))`` lowercased — order/dup independent.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

import rfc8785

PACK_CONTENT_HASH_VERSION = 1
PREAMBLE = b"easysynq.evidencepack.v1\n"


def _sorted_lower(values: Iterable[str]) -> list[str]:
    return sorted({str(v).lower() for v in values})


def _canonical_selector(scope_selector: dict[str, Any]) -> dict[str, Any]:
    """Normalise a scope selector so id-list order never perturbs the seal (rfc8785 orders keys; we
    sort list values here)."""
    out: dict[str, Any] = {}
    for key, value in scope_selector.items():
        out[key] = _sorted_lower(value) if isinstance(value, list) else value
    return out


def pack_content_hash(
    *,
    scope_kind: str,
    scope_selector: dict[str, Any],
    period_start: str | None,
    period_end: str | None,
    included_record_ids: Iterable[str],
    pinned_version_ids: Iterable[str],
    evidence_sha256s: Iterable[str],
    excluded_permission_record_ids: Iterable[str],
    excluded_absence_record_ids: Iterable[str],
) -> str:
    """Return the ``"sha256:"``-prefixed manifest seal for an evidence pack (doc 06 §7.4).

    Deterministic and order-/duplicate-independent in every id/sha collection.
    """
    obj: dict[str, Any] = {
        "v": PACK_CONTENT_HASH_VERSION,
        "scope_kind": scope_kind,
        "scope_selector": _canonical_selector(scope_selector),
        "period": [period_start, period_end],
        "included_record_ids": _sorted_lower(included_record_ids),
        "pinned_version_ids": _sorted_lower(pinned_version_ids),
        "evidence_manifest": _sorted_lower(evidence_sha256s),
        "excluded_permission": _sorted_lower(excluded_permission_record_ids),
        "excluded_absence": _sorted_lower(excluded_absence_record_ids),
    }
    payload = PREAMBLE + rfc8785.dumps(obj)
    return "sha256:" + hashlib.sha256(payload).hexdigest()
