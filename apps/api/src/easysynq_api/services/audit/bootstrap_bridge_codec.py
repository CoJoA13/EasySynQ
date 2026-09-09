"""Private strict legacy-bridge wire decoding; no storage or producer interface."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
from typing import Any, NoReturn, cast
from uuid import UUID

import rfc8785

from easysynq_api.services.audit.lineage import AuditHead

_MAX_BIGINT = 9_223_372_036_854_775_807
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"ed25519-sha256:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_ROOT_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/root\0"
_PAGE_DOMAIN = b"EasySynQ/AuditLegacyBridge/v1/page\0"


class _Invalid(ValueError):
    pass


@dataclasses.dataclass(frozen=True, slots=True)
class _Witness:
    witness_id: UUID
    namespace_hash: str
    entry_count: int
    lowest_head: AuditHead
    highest_head: AuditHead


@dataclasses.dataclass(frozen=True, slots=True)
class _PageRef:
    page_index: int
    entry_count: int
    page_hash: str


@dataclasses.dataclass(frozen=True, slots=True)
class _Root:
    org_id: UUID
    stream_id: UUID
    initial_key_id: str
    initial_public_key: bytes
    initial_key_epoch: int
    audit_boundary: AuditHead
    legacy_key_ids: tuple[str, ...]
    witnesses: tuple[_Witness, ...]
    entry_count: int
    pages: tuple[_PageRef, ...]
    commitment_hash: str


type _Locator = tuple[UUID, str, str]


@dataclasses.dataclass(frozen=True, slots=True)
class _Entry:
    locator: _Locator
    body_hash: str
    body_bytes: int


@dataclasses.dataclass(frozen=True, slots=True)
class _Page:
    org_id: UUID
    stream_id: UUID
    page_index: int
    entries: tuple[_Entry, ...]
    canonical: bytes
    page_hash: str


def _reject() -> NoReturn:
    raise _Invalid from None


def _label(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 1024:
        return False
    if any(
        ord(char) < 32 or 127 <= ord(char) <= 159 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        return False
    return len(value.encode("utf-8")) <= 1024


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            _reject()
        result[name] = value
    return result


def _integer(decimal_text: str) -> int:
    if decimal_text != "1":
        _reject()
    return 1


def _token(_value: str) -> NoReturn:
    _reject()


def _decode(raw: bytes, maximum: int) -> dict[str, Any]:
    if not raw or len(raw) > maximum:
        _reject()
    depth = 0
    containers: list[tuple[int, int]] = []
    maximum_array = 1024 if maximum == 262144 else 512
    quoted = escaped = False
    token_length = 0
    for byte in raw:
        if quoted:
            token_length += 1
            # A 1024-byte label can use six transport bytes per escaped ASCII scalar.
            if token_length > 6146:
                _reject()
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
            token_length = 0
        elif byte in (91, 123):
            depth += 1
            containers.append((byte, 0))
            if depth > 6:
                _reject()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _reject()
            containers.pop()
        elif byte == 44 and containers and containers[-1][0] == 91:
            opening, commas = containers[-1]
            if commas + 1 >= maximum_array:
                _reject()
            containers[-1] = (opening, commas + 1)
    if depth or quoted:
        _reject()
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_int=_integer,
            parse_float=_token,
            parse_constant=_token,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _reject()
    if type(value) is not dict:
        _reject()
    return cast(dict[str, Any], value)


def _shape(value: Any, fields: str) -> dict[str, Any]:
    if type(value) is not dict or value.keys() != set(fields.split()):
        _reject()
    return cast(dict[str, Any], value)


def _string(value: Any) -> str:
    if type(value) is not str:
        _reject()
    return value


def _digest(value: Any, *, key: bool = False) -> str:
    text = _string(value)
    if (_KEY_ID if key else _HEX).fullmatch(text) is None:
        _reject()
    return text


def _uuid(value: Any) -> UUID:
    text = _string(value)
    try:
        parsed = UUID(text)
    except ValueError:
        _reject()
    if str(parsed) != text:
        _reject()
    return parsed


def _decimal(value: Any, minimum: int, maximum: int = _MAX_BIGINT) -> int:
    text = _string(value)
    if len(text) > 19 or _DECIMAL.fullmatch(text) is None:
        _reject()
    number = int(text)
    if not minimum <= number <= maximum:
        _reject()
    return number


def _array(value: Any, maximum: int) -> list[Any]:
    if type(value) is not list or not 1 <= len(value) <= maximum:
        _reject()
    return value


def _head(value: Any) -> AuditHead:
    obj = _shape(value, "latest_id latest_row_hash")
    return AuditHead(_decimal(obj["latest_id"], 1), _digest(obj["latest_row_hash"]))


def _canonical(obj: dict[str, Any], maximum: int) -> bytes:
    try:
        canonical = rfc8785.dumps(obj)
    except (rfc8785.CanonicalizationError, UnicodeEncodeError):
        _reject()
    if len(canonical) > maximum:
        _reject()
    return canonical


def _common(obj: dict[str, Any], kind: str) -> tuple[UUID, UUID]:
    if type(obj["format_version"]) is not int or obj["format_version"] != 1 or obj["kind"] != kind:
        _reject()
    return _uuid(obj["org_id"]), _uuid(obj["stream_id"])


def _decode_root(raw: bytes) -> _Root:
    obj = _shape(
        _decode(raw, 262144),
        "format_version kind org_id stream_id initial_key_id initial_public_key "
        "initial_key_epoch audit_boundary legacy_key_ids witnesses entry_count pages",
    )
    org, stream = _common(obj, "legacy_bridge")
    initial_id = _digest(obj["initial_key_id"], key=True)
    encoded = _string(obj["initial_public_key"])
    try:
        public = base64.b64decode(encoded, validate=True)
    except ValueError:
        _reject()
    if len(public) != 32 or base64.b64encode(public).decode("ascii") != encoded:
        _reject()
    boundary = _head(obj["audit_boundary"])
    key_ids = tuple(_digest(value, key=True) for value in _array(obj["legacy_key_ids"], 8))
    if key_ids != tuple(sorted(set(key_ids))):
        _reject()
    witnesses = []
    for value in _array(obj["witnesses"], 4):
        item = _shape(value, "witness_id namespace_hash entry_count lowest_head highest_head")
        witness = _Witness(
            _uuid(item["witness_id"]),
            _digest(item["namespace_hash"]),
            _decimal(item["entry_count"], 1, 524288),
            _head(item["lowest_head"]),
            _head(item["highest_head"]),
        )
        if witness.highest_head != boundary or witness.lowest_head.latest_id > boundary.latest_id:
            _reject()
        if witness.lowest_head.latest_id == boundary.latest_id and witness.lowest_head != boundary:
            _reject()
        witnesses.append(witness)
    ids = [str(item.witness_id) for item in witnesses]
    if ids != sorted(set(ids)):
        _reject()
    pages = []
    for index, value in enumerate(_array(obj["pages"], 1024)):
        item = _shape(value, "page_index entry_count page_hash")
        page = _PageRef(
            _decimal(item["page_index"], 0, 1023),
            _decimal(item["entry_count"], 1, 512),
            _digest(item["page_hash"]),
        )
        if page.page_index != index:
            _reject()
        pages.append(page)
    canonical = _canonical(obj, 262144)
    return _Root(
        org,
        stream,
        initial_id,
        public,
        _decimal(obj["initial_key_epoch"], 0),
        boundary,
        key_ids,
        tuple(witnesses),
        _decimal(obj["entry_count"], 1, 524288),
        tuple(pages),
        hashlib.sha256(_ROOT_DOMAIN + canonical).hexdigest(),
    )


def _decode_page(raw: bytes) -> _Page:
    obj = _shape(_decode(raw, 2097152), "format_version kind org_id stream_id page_index entries")
    org, stream = _common(obj, "legacy_bridge_page")
    entries = []
    for value in _array(obj["entries"], 512):
        item = _shape(value, "witness_id object_key version_id body_hash body_bytes")
        if not _label(item["object_key"]) or not _label(item["version_id"]):
            _reject()
        entries.append(
            _Entry(
                (_uuid(item["witness_id"]), item["object_key"], item["version_id"]),
                _digest(item["body_hash"]),
                _decimal(item["body_bytes"], 1, 65536),
            )
        )
    canonical = _canonical(obj, 2097152)
    return _Page(
        org,
        stream,
        _decimal(obj["page_index"], 0, 1023),
        tuple(entries),
        canonical,
        hashlib.sha256(_PAGE_DOMAIN + canonical).hexdigest(),
    )
