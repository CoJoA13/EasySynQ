"""Private pure retained-object compatibility; legacy keys keep historical admission."""

from __future__ import annotations

import base64
import dataclasses
import datetime
import json
from typing import Any
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from easysynq_api.services.audit.lineage import AuditHead


class _InvalidLegacy(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class _Payload:
    head: AuditHead
    canonical: bytes
    signature: bytes


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise _InvalidLegacy from None
        result[name] = value
    return result


def _decode(raw: bytes, org_id: UUID) -> _Payload:
    if len(raw) > 65536:
        raise _InvalidLegacy from None
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        if type(doc) is not dict or doc.keys() != {"checkpoint", "signature"}:
            raise _InvalidLegacy
        payload = doc["checkpoint"]
        if type(payload) is not dict or payload.keys() != {
            "org_id",
            "latest_id",
            "latest_row_hash",
            "timestamp",
        }:
            raise _InvalidLegacy
        if (
            type(payload["org_id"]) is not str
            or payload["org_id"] != str(org_id)
            or type(payload["latest_id"]) is not int
            or payload["latest_id"] < 1
            or type(payload["latest_row_hash"]) is not str
            or len(payload["latest_row_hash"]) != 64
            or type(payload["timestamp"]) is not str
            or type(doc["signature"]) is not str
        ):
            raise _InvalidLegacy
        row_hash = bytes.fromhex(payload["latest_row_hash"])
        timestamp = datetime.datetime.fromisoformat(payload["timestamp"])
        signature = base64.b64decode(doc["signature"], validate=True)
        if len(row_hash) != 32 or len(signature) != 64:
            raise _InvalidLegacy
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime.UTC)
        normalized_time = timestamp.astimezone(datetime.UTC).isoformat()
        canonical = rfc8785.dumps(
            {
                "org_id": payload["org_id"],
                "latest_id": payload["latest_id"],
                "latest_row_hash": row_hash.hex(),
                "timestamp": normalized_time,
            }
        )
    except (ValueError, OverflowError, RecursionError):
        raise _InvalidLegacy from None
    return _Payload(AuditHead(payload["latest_id"], row_hash.hex()), canonical, signature)


def _authenticate(payload: _Payload, keys: tuple[Ed25519PublicKey, ...]) -> bool:
    for key in keys:
        try:
            key.verify(payload.signature, payload.canonical)
        except (InvalidSignature, ValueError):
            continue
        return True
    return False
