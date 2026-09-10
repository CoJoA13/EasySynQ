"""Strictly decode one supplied S3 ``ListObjectVersions`` XML page.

The decoder is intentionally pure and inactive.  It admits provider observations from
original response bytes; it does not collect pages or claim history completeness.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, NoReturn
from xml.parsers import expat

from . import trust
from .sink import CheckpointVersionRef, CheckpointVersionsPage

_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
_NAMESPACE_SEPARATOR = "\x1f"
_ROOT = "ListVersionsResult"
_BODY_MAX_BYTES = 16_777_216
_PAGE_MAX_ENTRIES = 1_000
_MAX_DEPTH = 4
_MAX_ELEMENTS = 32_768
_MAX_SCALAR_BYTES = 8_192
_MAX_ENCODED_LABEL_BYTES = 3_072
_MAX_LABEL_BYTES = 1_024
_HEX = frozenset("0123456789abcdefABCDEF")
_XML_WHITESPACE = frozenset(" \t\r\n")
_DECODE_CODES = frozenset(
    {"BODY_LIMIT", "RESPONSE_INVALID", "PAGE_LIMIT", "SCOPE_MISMATCH", "CURSOR_INVALID"}
)

_ROOT_SCALARS = frozenset(
    {
        "Name",
        "Prefix",
        "KeyMarker",
        "VersionIdMarker",
        "NextKeyMarker",
        "NextVersionIdMarker",
        "MaxKeys",
        "IsTruncated",
        "EncodingType",
        "Delimiter",
    }
)
_ROOT_REQUIRED = frozenset({"Name", "Prefix", "MaxKeys", "IsTruncated", "EncodingType"})
_VERSION_SCALARS = frozenset(
    {
        "Key",
        "VersionId",
        "IsLatest",
        "LastModified",
        "ETag",
        "Size",
        "StorageClass",
        "ChecksumAlgorithm",
        "ChecksumType",
    }
)
_DELETE_SCALARS = frozenset({"Key", "VersionId", "IsLatest", "LastModified"})
_ENTRY_REQUIRED = frozenset({"Key", "VersionId", "IsLatest"})
_OWNER_SCALARS = frozenset({"ID", "DisplayName"})
_RESTORE_SCALARS = frozenset({"IsRestoreInProgress", "RestoreExpiryDate"})


class CheckpointVersionPageInputError(ValueError):
    """The caller supplied an invalid decoding scope or argument."""


class CheckpointVersionPageDecodeError(Exception):
    """A supplied response page failed bounded admission."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or code not in _DECODE_CODES:
            raise ValueError("invalid checkpoint version page decode code")
        self.code = code
        super().__init__("checkpoint version page decode failed")


def _input_invalid() -> NoReturn:
    raise CheckpointVersionPageInputError("invalid checkpoint version page input") from None


def _decode_invalid(code: str = "RESPONSE_INVALID") -> NoReturn:
    raise CheckpointVersionPageDecodeError(code) from None


def _utf8_length(value: str) -> int | None:
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        return None
    return len(encoded)


def _input_marker(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value:
        _input_invalid()
    marker_size = _utf8_length(value)
    if marker_size is None or marker_size > _MAX_LABEL_BYTES:
        _input_invalid()
    return value


def _validate_inputs(
    body: object,
    bucket: object,
    org_id: object,
    key_marker: object,
    version_id_marker: object,
) -> tuple[bytes, str, str | None, str | None, str]:
    if type(body) is not bytes or type(bucket) is not str or type(org_id) is not uuid.UUID:
        _input_invalid()

    bucket_valid = False
    try:
        trust._bucket(bucket)
    except trust.TrustConfigurationError:
        bucket_valid = False
    else:
        bucket_valid = True
    if not bucket_valid:
        _input_invalid()

    prefix = f"checkpoints/{org_id}/"
    validated_key_marker = _input_marker(key_marker)
    validated_version_marker = _input_marker(version_id_marker)
    if validated_version_marker is not None and validated_key_marker is None:
        _input_invalid()
    if validated_key_marker is not None and not validated_key_marker.startswith(prefix):
        _input_invalid()
    return (
        body,
        bucket,
        validated_key_marker,
        validated_version_marker,
        prefix,
    )


def _percent_octets(encoded_text: str) -> bytes:
    if not encoded_text.isascii() or len(encoded_text) > _MAX_ENCODED_LABEL_BYTES:
        _decode_invalid()
    output = bytearray()
    offset = 0
    while offset < len(encoded_text):
        character = encoded_text[offset]
        if character == "%":
            pair = encoded_text[offset + 1 : offset + 3]
            if len(pair) != 2 or any(item not in _HEX for item in pair):
                _decode_invalid()
            output.append(int(pair, 16))
            offset += 3
        else:
            output.append(ord(character))
            offset += 1
    return bytes(output)


def _strict_utf8(raw: bytes) -> str:
    decoded: str | None = None
    try:
        decoded = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        decoded = None
    if decoded is None:
        _decode_invalid()
    return decoded


def _percent_label(text: str, *, empty: bool = False) -> str | None:
    decoded = _strict_utf8(_percent_octets(text))
    size = _utf8_length(decoded)
    if size is None or size > _MAX_LABEL_BYTES or (not empty and not decoded):
        _decode_invalid()
    if not decoded:
        return None
    return decoded


def _opaque_label(text: str, *, empty: bool = False) -> str | None:
    size = _utf8_length(text)
    if size is None or size > _MAX_LABEL_BYTES or (not empty and not text):
        _decode_invalid()
    if not text:
        return None
    return text


def _boolean(text: str) -> bool:
    if text not in ("true", "false"):
        _decode_invalid()
    return text == "true"


def _local_name(expanded_name: str) -> str:
    namespace, separator, local = expanded_name.partition(_NAMESPACE_SEPARATOR)
    if separator != _NAMESPACE_SEPARATOR or namespace != _NAMESPACE or not local:
        _decode_invalid()
    return local


@dataclass(slots=True)
class _Frame:
    name: str
    kind: str
    seen: set[str] = field(default_factory=set)
    values: dict[str, Any] = field(default_factory=dict)
    chunks: list[str] = field(default_factory=list)
    text_bytes: int = 0


class _PageDecoder:
    def __init__(self, *, bucket: str, prefix: str) -> None:
        self.bucket = bucket
        self.prefix = prefix
        self.stack: list[_Frame] = []
        self.depth = 0
        self.elements = 0
        self.entries = 0
        self.root_started = False
        self.root_closed = False
        self.root_values: dict[str, str | bool | None] = {}
        self.versions: list[CheckpointVersionRef] = []
        self.delete_markers: list[CheckpointVersionRef] = []

    def start(self, expanded_name: str, attributes: dict[str, str]) -> None:
        self.depth += 1
        self.elements += 1
        if self.depth > _MAX_DEPTH or self.elements > _MAX_ELEMENTS:
            _decode_invalid()
        if attributes:
            _decode_invalid()
        name = _local_name(expanded_name)

        if not self.stack:
            if self.root_started or self.root_closed or name != _ROOT or self.depth != 1:
                _decode_invalid()
            self.root_started = True
            self.stack.append(_Frame(name, "root"))
            return

        parent = self.stack[-1]
        if parent.kind == "scalar":
            _decode_invalid()
        if parent.kind == "root":
            self._start_root_child(parent, name)
            return
        if parent.kind in {"Version", "DeleteMarker"}:
            self._start_entry_child(parent, name)
            return
        if parent.kind == "Owner":
            self._start_unique_scalar(parent, name, _OWNER_SCALARS)
            return
        if parent.kind == "RestoreStatus":
            self._start_unique_scalar(parent, name, _RESTORE_SCALARS)
            return
        _decode_invalid()

    def _start_root_child(self, parent: _Frame, name: str) -> None:
        if name in _ROOT_SCALARS:
            self._start_unique_scalar(parent, name, _ROOT_SCALARS)
            return
        if name not in {"Version", "DeleteMarker"}:
            _decode_invalid()
        self.entries += 1
        if self.entries > _PAGE_MAX_ENTRIES:
            _decode_invalid("PAGE_LIMIT")
        self.stack.append(_Frame(name, name))

    def _start_entry_child(self, parent: _Frame, name: str) -> None:
        allowed_scalars = _VERSION_SCALARS if parent.kind == "Version" else _DELETE_SCALARS
        allowed_nested = {"Owner", "RestoreStatus"} if parent.kind == "Version" else {"Owner"}
        if name in allowed_scalars:
            if name != "ChecksumAlgorithm" and name in parent.seen:
                _decode_invalid()
            if name != "ChecksumAlgorithm":
                parent.seen.add(name)
            self.stack.append(_Frame(name, "scalar"))
            return
        if name in allowed_nested:
            if name in parent.seen:
                _decode_invalid()
            parent.seen.add(name)
            self.stack.append(_Frame(name, name))
            return
        _decode_invalid()

    def _start_unique_scalar(self, parent: _Frame, name: str, allowed: frozenset[str]) -> None:
        if name not in allowed or name in parent.seen:
            _decode_invalid()
        parent.seen.add(name)
        self.stack.append(_Frame(name, "scalar"))

    def character_data(self, data: str) -> None:
        if not data:
            return
        if not self.stack:
            if any(character not in _XML_WHITESPACE for character in data):
                _decode_invalid()
            return
        frame = self.stack[-1]
        if frame.kind != "scalar":
            if any(character not in _XML_WHITESPACE for character in data):
                _decode_invalid()
            return
        chunk_size = _utf8_length(data)
        if chunk_size is None or frame.text_bytes + chunk_size > _MAX_SCALAR_BYTES:
            _decode_invalid()
        frame.text_bytes += chunk_size
        frame.chunks.append(data)

    def end(self, expanded_name: str) -> None:
        if not self.stack:
            _decode_invalid()
        name = _local_name(expanded_name)
        frame = self.stack.pop()
        if frame.name != name:
            _decode_invalid()
        self.depth -= 1

        if frame.kind == "scalar":
            if not self.stack:
                _decode_invalid()
            self._finish_scalar(self.stack[-1], name, "".join(frame.chunks))
        elif frame.kind in {"Version", "DeleteMarker"}:
            self._finish_entry(frame)
        elif frame.kind == "root":
            if self.stack or self.depth != 0:
                _decode_invalid()
            self.root_closed = True

    def _finish_scalar(self, parent: _Frame, name: str, text: str) -> None:
        if parent.kind == "root":
            self._finish_root_scalar(name, text)
            return
        if parent.kind in {"Version", "DeleteMarker"}:
            if name == "Key":
                key = _percent_label(text)
                if key is None:
                    _decode_invalid()
                if not key.startswith(self.prefix):
                    _decode_invalid("SCOPE_MISMATCH")
                parent.values[name] = key
            elif name == "VersionId":
                parent.values[name] = _opaque_label(text)
            elif name == "IsLatest":
                parent.values[name] = _boolean(text)
            return
        if parent.kind == "RestoreStatus" and name == "IsRestoreInProgress":
            _boolean(text)

    def _finish_root_scalar(self, name: str, text: str) -> None:
        value: str | bool | None = text
        if name == "Name":
            if text != self.bucket:
                _decode_invalid("SCOPE_MISMATCH")
        elif name == "Prefix":
            value = _percent_label(text)
            if value != self.prefix:
                _decode_invalid("SCOPE_MISMATCH")
        elif name in {"KeyMarker", "NextKeyMarker"}:
            value = _percent_label(text, empty=True)
            if value is not None and not value.startswith(self.prefix):
                _decode_invalid("SCOPE_MISMATCH")
        elif name in {"VersionIdMarker", "NextVersionIdMarker"}:
            value = _opaque_label(text, empty=True)
        elif name == "MaxKeys":
            if text != "1000":
                _decode_invalid()
        elif name == "EncodingType":
            if text != "url":
                _decode_invalid()
        elif name == "IsTruncated":
            value = _boolean(text)
        elif name == "Delimiter" and text:
            _decode_invalid()
        self.root_values[name] = value

    def _finish_entry(self, frame: _Frame) -> None:
        if not _ENTRY_REQUIRED.issubset(frame.seen):
            _decode_invalid()
        key = frame.values.get("Key")
        version_id = frame.values.get("VersionId")
        if type(key) is not str or type(version_id) is not str:
            _decode_invalid()
        reference = CheckpointVersionRef(key, version_id)
        if frame.kind == "Version":
            self.versions.append(reference)
        else:
            self.delete_markers.append(reference)

    def reject_event(self, *_arguments: Any) -> NoReturn:
        _decode_invalid()

    def xml_declaration(self, version: str, encoding: str | None, _standalone: int) -> None:
        if version != "1.0" or (encoding is not None and encoding.lower() != "utf-8"):
            _decode_invalid()


def _parser_factory() -> Any:
    return expat.ParserCreate(encoding="UTF-8", namespace_separator=_NAMESPACE_SEPARATOR)


def _parse(body: bytes, decoder: _PageDecoder) -> None:
    utf8_valid = False
    try:
        body.decode("utf-8-sig", "strict")
    except UnicodeDecodeError:
        utf8_valid = False
    else:
        utf8_valid = True
    if not utf8_valid:
        _decode_invalid()
    if b"\x00" in body:
        _decode_invalid()

    parser = _parser_factory()
    parser.StartElementHandler = decoder.start
    parser.EndElementHandler = decoder.end
    parser.CharacterDataHandler = decoder.character_data
    parser.XmlDeclHandler = decoder.xml_declaration
    parser.CommentHandler = decoder.reject_event
    parser.ProcessingInstructionHandler = decoder.reject_event
    parser.StartDoctypeDeclHandler = decoder.reject_event
    parser.EntityDeclHandler = decoder.reject_event
    parser.UnparsedEntityDeclHandler = decoder.reject_event
    parser.NotationDeclHandler = decoder.reject_event
    parser.ExternalEntityRefHandler = decoder.reject_event
    parser.SkippedEntityHandler = decoder.reject_event
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    malformed = False
    try:
        parser.Parse(body, True)
    except expat.ExpatError:
        malformed = True
    if malformed:
        _decode_invalid()
    if not decoder.root_started or not decoder.root_closed or decoder.stack or decoder.depth != 0:
        _decode_invalid()


def _response_marker(
    values: dict[str, str | bool | None],
    name: str,
) -> tuple[bool, str | None]:
    if name not in values:
        return False, None
    value = values[name]
    if value is not None and type(value) is not str:
        _decode_invalid()
    return True, value


def _admit(
    decoder: _PageDecoder,
    *,
    key_marker: str | None,
    version_id_marker: str | None,
) -> CheckpointVersionsPage:
    values = decoder.root_values
    if not _ROOT_REQUIRED.issubset(values):
        _decode_invalid()

    key_present, echoed_key = _response_marker(values, "KeyMarker")
    version_present, echoed_version = _response_marker(values, "VersionIdMarker")
    if key_marker is None:
        if echoed_key is not None:
            _decode_invalid("CURSOR_INVALID")
    elif not key_present or echoed_key != key_marker:
        _decode_invalid("CURSOR_INVALID")
    if version_id_marker is None:
        if echoed_version is not None:
            _decode_invalid("CURSOR_INVALID")
    elif not version_present or echoed_version != version_id_marker:
        _decode_invalid("CURSOR_INVALID")

    truncated = values["IsTruncated"]
    if type(truncated) is not bool:
        _decode_invalid()
    _, next_key = _response_marker(values, "NextKeyMarker")
    _, next_version = _response_marker(values, "NextVersionIdMarker")

    if not truncated:
        if next_key is not None or next_version is not None:
            _decode_invalid("CURSOR_INVALID")
        next_key = None
        next_version = None
    else:
        if not decoder.entries or next_key is None:
            _decode_invalid("CURSOR_INVALID")
        if next_key == key_marker and next_version == version_id_marker:
            _decode_invalid("CURSOR_INVALID")

    return CheckpointVersionsPage(
        tuple(decoder.versions),
        tuple(decoder.delete_markers),
        truncated,
        next_key,
        next_version,
    )


def decode_checkpoint_version_page(
    body: bytes,
    *,
    bucket: str,
    org_id: uuid.UUID,
    key_marker: str | None = None,
    version_id_marker: str | None = None,
) -> CheckpointVersionsPage:
    """Decode one bounded supplied page into untrusted provider observations."""
    validated_body, validated_bucket, validated_key, validated_version, prefix = _validate_inputs(
        body, bucket, org_id, key_marker, version_id_marker
    )
    if len(validated_body) > _BODY_MAX_BYTES:
        _decode_invalid("BODY_LIMIT")
    if not validated_body:
        _decode_invalid()

    decoder = _PageDecoder(bucket=validated_bucket, prefix=prefix)
    _parse(validated_body, decoder)
    return _admit(
        decoder,
        key_marker=validated_key,
        version_id_marker=validated_version,
    )
