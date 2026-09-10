"""Independent public-vector tests for strict supplied version-page decoding."""

from __future__ import annotations

import builtins
import socket
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

ORG = UUID("11111111-1111-4111-8111-111111111111")
PREFIX = "checkpoints/11111111-1111-4111-8111-111111111111/"
ENCODED_PREFIX = "checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
NS = "http://s3.amazonaws.com/doc/2006-03-01/"
ROOT_OPEN = f'<ListVersionsResult xmlns="{NS}">'.encode()
ROOT_CLOSE = b"</ListVersionsResult>"
REQUIRED_FIELDS = (
    b"<Name>history-probe</Name>",
    f"<Prefix>{ENCODED_PREFIX}</Prefix>".encode(),
    b"<MaxKeys>1000</MaxKeys>",
    b"<EncodingType>url</EncodingType>",
    b"<IsTruncated>false</IsTruncated>",
)
VERSION = (
    f"<Version><Key>{ENCODED_PREFIX}a%252B%2B%E2%98%83</Key>".encode()
    + b"<VersionId>opaque%2F+/</VersionId><IsLatest>true</IsLatest></Version>"
)
DELETE_MARKER = (
    f"<DeleteMarker><Key>{ENCODED_PREFIX}deleted</Key>".encode()
    + b"<VersionId>null</VersionId><IsLatest>false</IsLatest></DeleteMarker>"
)
VALID = (
    ROOT_OPEN
    + b"\n<Name>history-probe</Name>\n"
    + f"<Prefix>{ENCODED_PREFIX}</Prefix>\n".encode()
    + b"<KeyMarker/><VersionIdMarker/><MaxKeys>1000</MaxKeys>\n"
    + b"<EncodingType>url</EncodingType><IsTruncated>false</IsTruncated>\n"
    + VERSION
    + b"\n"
    + DELETE_MARKER
    + b"\n"
    + ROOT_CLOSE
)

pytestmark = pytest.mark.unit


def _document(*children: bytes) -> bytes:
    return ROOT_OPEN + b"".join(children) + ROOT_CLOSE


def _page(*entries: bytes, fields: tuple[bytes, ...] = REQUIRED_FIELDS) -> bytes:
    return _document(*fields, *entries)


def _required_fields(*extra: bytes) -> tuple[bytes, ...]:
    return (*REQUIRED_FIELDS, *extra)


def _truncated_fields(*extra: bytes) -> tuple[bytes, ...]:
    return (
        *(
            item.replace(b"false", b"true") if item.startswith(b"<IsTruncated>") else item
            for item in REQUIRED_FIELDS
        ),
        *extra,
    )


def _entry(
    kind: str = "Version",
    *,
    key: str = ENCODED_PREFIX + "object",
    version_id: str = "opaque+version/1",
    is_latest: str = "false",
    extra: bytes = b"",
) -> bytes:
    return (
        f"<{kind}><Key>{key}</Key><VersionId>{version_id}</VersionId>".encode()
        + f"<IsLatest>{is_latest}</IsLatest>".encode()
        + extra
        + f"</{kind}>".encode()
    )


def _module() -> Any:
    from easysynq_api.services.audit import version_page

    return version_page


def _capture(call: Callable[[], Any]) -> BaseException:
    try:
        call()
    except BaseException as error:  # noqa: BLE001 - identity is the asserted contract
        return error
    raise AssertionError("call returned instead of raising")


def _assert_decode_error(body: bytes, code: str = "RESPONSE_INVALID", **kwargs: Any) -> None:
    module = _module()
    error = _capture(
        lambda: module.decode_checkpoint_version_page(
            body,
            bucket=kwargs.pop("bucket", "history-probe"),
            org_id=kwargs.pop("org_id", ORG),
            **kwargs,
        )
    )
    assert type(error) is module.CheckpointVersionPageDecodeError
    assert error.code == code
    assert str(error) == "checkpoint version page decode failed"
    assert error.args == ("checkpoint version page decode failed",)
    assert error.__cause__ is None
    assert error.__context__ is None


def _assert_input_error(body: Any = VALID, **kwargs: Any) -> None:
    module = _module()
    error = _capture(
        lambda: module.decode_checkpoint_version_page(
            body,
            bucket=kwargs.pop("bucket", "history-probe"),
            org_id=kwargs.pop("org_id", ORG),
            **kwargs,
        )
    )
    assert type(error) is module.CheckpointVersionPageInputError
    assert str(error) == "invalid checkpoint version page input"
    assert error.args == ("invalid checkpoint version page input",)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_public_decoder_preserves_version_and_delete_marker() -> None:
    from easysynq_api.services.audit.version_page import decode_checkpoint_version_page

    result = decode_checkpoint_version_page(VALID, bucket="history-probe", org_id=ORG)

    assert [(version.key, version.version_id) for version in result.versions] == [
        (PREFIX + "a%2B+\u2603", "opaque%2F+/")
    ]
    assert [(marker.key, marker.version_id) for marker in result.delete_markers] == [
        (PREFIX + "deleted", "null")
    ]
    assert result.truncated is False
    assert result.next_key_marker is result.next_version_id_marker is None


def test_public_decoder_rejects_lossy_truncation() -> None:
    from easysynq_api.services.audit.version_page import (
        CheckpointVersionPageDecodeError,
        decode_checkpoint_version_page,
    )

    invalid = VALID.replace(
        b"<IsTruncated>false</IsTruncated>",
        b"<IsTruncated>garbage</IsTruncated>",
    )

    with pytest.raises(CheckpointVersionPageDecodeError) as caught:
        decode_checkpoint_version_page(invalid, bucket="history-probe", org_id=ORG)
    assert caught.value.code == "RESPONSE_INVALID"


def test_public_decoder_rejects_invalid_utf8_percent_octet() -> None:
    from easysynq_api.services.audit.version_page import (
        CheckpointVersionPageDecodeError,
        decode_checkpoint_version_page,
    )

    invalid = VALID.replace(b"a%252B%2B%E2%98%83", b"a%FF")

    with pytest.raises(CheckpointVersionPageDecodeError) as caught:
        decode_checkpoint_version_page(invalid, bucket="history-probe", org_id=ORG)
    assert caught.value.code == "RESPONSE_INVALID"


def test_round_trip_preserves_all_lexical_label_cases() -> None:
    module = _module()
    key = ENCODED_PREFIX + "pct%252f+literal/slash%2fspace%20bmp%E9%9B%AAastral%F0%9F%9A%80"
    version_id = "opaque%2f+/ space 雪🚀 null"
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=key, version_id=version_id, is_latest="true")),
        bucket="history-probe",
        org_id=ORG,
    )
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + "pct%2f+literal/slash/space bmp雪astral🚀", version_id)
    ]


def test_valid_utf8_replacement_character_is_preserved() -> None:
    module = _module()
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=ENCODED_PREFIX + "%EF%BF%BD")),
        bucket="history-probe",
        org_id=ORG,
    )
    assert result.versions[0].key == PREFIX + "\ufffd"


def test_xml_entities_numeric_references_and_cdata_retain_xml_semantics() -> None:
    module = _module()
    body = _page(
        f"<Version><Key><![CDATA[{ENCODED_PREFIX}entity%26]]></Key>".encode()
        + "<VersionId>opaque&amp;&#x2B;<![CDATA[/雪]]></VersionId>".encode()
        + b"<IsLatest>true</IsLatest></Version>"
    )
    result = module.decode_checkpoint_version_page(body, bucket="history-probe", org_id=ORG)
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + "entity&", "opaque&+/雪")
    ]


def test_percent_decoded_control_key_is_retained_as_untrusted_observation() -> None:
    module = _module()
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=ENCODED_PREFIX + "control%00%09%0A")),
        bucket="history-probe",
        org_id=ORG,
    )
    assert result.versions[0].key == PREFIX + "control\x00\t\n"


def test_duplicate_observations_are_retained_in_input_order_within_each_kind() -> None:
    module = _module()
    first = _entry(key=ENCODED_PREFIX + "first", version_id="v1")
    duplicate = _entry(key=ENCODED_PREFIX + "first", version_id="v1")
    deleted = _entry("DeleteMarker", key=ENCODED_PREFIX + "gone", version_id="null")
    result = module.decode_checkpoint_version_page(
        _page(first, deleted, duplicate, deleted), bucket="history-probe", org_id=ORG
    )
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + "first", "v1"),
        (PREFIX + "first", "v1"),
    ]
    assert [(item.key, item.version_id) for item in result.delete_markers] == [
        (PREFIX + "gone", "null"),
        (PREFIX + "gone", "null"),
    ]


def test_optional_metadata_grammar_is_admitted_and_discarded() -> None:
    module = _module()
    metadata = (
        b"<LastModified>ignored</LastModified><ETag>&quot;opaque&quot;</ETag>"
        b"<Size>-1</Size><StorageClass>unknown</StorageClass>"
        b"<ChecksumAlgorithm>ONE</ChecksumAlgorithm>"
        b"<ChecksumAlgorithm>TWO</ChecksumAlgorithm><ChecksumType>ignored</ChecksumType>"
        b"<Owner><ID>id</ID><DisplayName>display</DisplayName></Owner>"
        b"<RestoreStatus><IsRestoreInProgress>false</IsRestoreInProgress>"
        b"<RestoreExpiryDate>ignored</RestoreExpiryDate></RestoreStatus>"
    )
    delete_metadata = b"<LastModified>ignored</LastModified><Owner><ID>id</ID></Owner>"
    result = module.decode_checkpoint_version_page(
        _page(_entry(extra=metadata), _entry("DeleteMarker", extra=delete_metadata)),
        bucket="history-probe",
        org_id=ORG,
    )
    assert len(result.versions) == len(result.delete_markers) == 1


def test_namespace_prefix_alias_is_accepted() -> None:
    module = _module()
    body = (
        f'<s3:ListVersionsResult xmlns:s3="{NS}">'.encode()
        + b"<s3:Name>history-probe</s3:Name>"
        + f"<s3:Prefix>{ENCODED_PREFIX}</s3:Prefix>".encode()
        + b"<s3:MaxKeys>1000</s3:MaxKeys><s3:EncodingType>url</s3:EncodingType>"
        + b"<s3:IsTruncated>false</s3:IsTruncated>"
        + f"<s3:Version><s3:Key>{ENCODED_PREFIX}prefixed</s3:Key>".encode()
        + b"<s3:VersionId>opaque</s3:VersionId><s3:IsLatest>true</s3:IsLatest>"
        + b"</s3:Version></s3:ListVersionsResult>"
    )
    result = module.decode_checkpoint_version_page(body, bucket="history-probe", org_id=ORG)
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + "prefixed", "opaque")
    ]


def test_control_and_null_like_opaque_version_labels_are_preserved() -> None:
    module = _module()
    control = (
        f"<Version><Key>{ENCODED_PREFIX}control-version</Key>".encode()
        + b"<VersionId>v&#x9;&#xA;&#xD;</VersionId><IsLatest>true</IsLatest></Version>"
    )
    result = module.decode_checkpoint_version_page(
        _page(
            control,
            _entry(version_id="null", key=ENCODED_PREFIX + "literal-null"),
            _entry("DeleteMarker", version_id="None", key=ENCODED_PREFIX + "literal-none"),
        ),
        bucket="history-probe",
        org_id=ORG,
    )
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + "control-version", "v\t\n\r"),
        (PREFIX + "literal-null", "null"),
    ]
    assert [(item.key, item.version_id) for item in result.delete_markers] == [
        (PREFIX + "literal-none", "None")
    ]


@pytest.mark.parametrize("with_version", [False, True], ids=["key-only", "two-part"])
def test_request_echo_and_truncated_continuation_round_trip(with_version: bool) -> None:
    module = _module()
    current_key = PREFIX + "current+key"
    current_version = "opaque%2F+/ 雪" if with_version else None
    fields = (
        b"<Name>history-probe</Name>",
        f"<Prefix>{ENCODED_PREFIX}</Prefix>".encode(),
        f"<KeyMarker>{ENCODED_PREFIX}current%2Bkey</KeyMarker>".encode(),
        (
            f"<VersionIdMarker>{current_version}</VersionIdMarker>".encode()
            if current_version is not None
            else b"<VersionIdMarker/>"
        ),
        b"<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>",
        b"<IsTruncated>true</IsTruncated>",
        f"<NextKeyMarker>{ENCODED_PREFIX}next%2Bkey</NextKeyMarker>".encode(),
        (
            "<NextVersionIdMarker>next%2F+/ 雪</NextVersionIdMarker>".encode()
            if with_version
            else b"<NextVersionIdMarker/>"
        ),
    )
    result = module.decode_checkpoint_version_page(
        _page(_entry(), fields=fields),
        bucket="history-probe",
        org_id=ORG,
        key_marker=current_key,
        version_id_marker=current_version,
    )
    assert result.truncated is True
    assert result.next_key_marker == PREFIX + "next+key"
    assert result.next_version_id_marker == ("next%2F+/ 雪" if with_version else None)


@pytest.mark.parametrize(
    "marker_fields",
    [b"", b"<KeyMarker/><VersionIdMarker/><NextKeyMarker/><NextVersionIdMarker/>"],
    ids=["absent", "empty"],
)
def test_terminal_page_accepts_absent_or_empty_optional_markers(marker_fields: bytes) -> None:
    module = _module()
    result = module.decode_checkpoint_version_page(
        _page(fields=_required_fields(marker_fields)), bucket="history-probe", org_id=ORG
    )
    assert result.versions == result.delete_markers == ()
    assert result.truncated is False
    assert result.next_key_marker is result.next_version_id_marker is None


@pytest.mark.parametrize("required", ["Name", "Prefix", "MaxKeys", "EncodingType", "IsTruncated"])
def test_missing_required_root_singleton_is_rejected(required: str) -> None:
    field = next(item for item in REQUIRED_FIELDS if item.startswith(f"<{required}>".encode()))
    _assert_decode_error(_page(fields=tuple(item for item in REQUIRED_FIELDS if item != field)))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        (b"<MaxKeys>1000</MaxKeys>", b"<MaxKeys>999</MaxKeys>"),
        (b"<MaxKeys>1000</MaxKeys>", b"<MaxKeys>1000 </MaxKeys>"),
        (b"<EncodingType>url</EncodingType>", b"<EncodingType>URL</EncodingType>"),
        (b"<EncodingType>url</EncodingType>", b"<EncodingType> url</EncodingType>"),
    ],
)
def test_fixed_root_scalar_literals_are_exact(field: bytes, replacement: bytes) -> None:
    _assert_decode_error(VALID.replace(field, replacement))


@pytest.mark.parametrize(
    "field",
    [
        b"<Name>history-probe</Name>",
        f"<Prefix>{ENCODED_PREFIX}</Prefix>".encode(),
        b"<KeyMarker/>",
        b"<VersionIdMarker/>",
        b"<NextKeyMarker/>",
        b"<NextVersionIdMarker/>",
        b"<MaxKeys>1000</MaxKeys>",
        b"<IsTruncated>false</IsTruncated>",
        b"<EncodingType>url</EncodingType>",
        b"<Delimiter/>",
    ],
)
def test_every_root_singleton_rejects_duplication(field: bytes) -> None:
    tag = field.split(b">", 1)[0].rstrip(b"/") + b">"
    existing = any(item.startswith(tag) for item in REQUIRED_FIELDS)
    if existing:
        fields = (*REQUIRED_FIELDS, field)
    else:
        fields = (*REQUIRED_FIELDS, field, field)
    _assert_decode_error(_page(fields=fields))


@pytest.mark.parametrize("order", [("true", "false"), ("false", "true")])
def test_conflicting_truncation_fields_are_rejected_in_both_orders(
    order: tuple[str, str],
) -> None:
    fields = tuple(item for item in REQUIRED_FIELDS if not item.startswith(b"<IsTruncated>"))
    fields += tuple(f"<IsTruncated>{value}</IsTruncated>".encode() for value in order)
    _assert_decode_error(_page(fields=fields))


@pytest.mark.parametrize("kind", ["Version", "DeleteMarker"])
@pytest.mark.parametrize("missing", ["Key", "VersionId", "IsLatest"])
def test_entries_require_each_core_singleton(kind: str, missing: str) -> None:
    children = {
        "Key": f"<Key>{ENCODED_PREFIX}object</Key>".encode(),
        "VersionId": b"<VersionId>v1</VersionId>",
        "IsLatest": b"<IsLatest>false</IsLatest>",
    }
    entry = (
        f"<{kind}>".encode()
        + b"".join(value for name, value in children.items() if name != missing)
        + f"</{kind}>".encode()
    )
    _assert_decode_error(_page(entry))


@pytest.mark.parametrize(
    ("kind", "duplicate"),
    [
        ("Version", b"<Key>unused</Key>"),
        ("Version", b"<VersionId>unused</VersionId>"),
        ("Version", b"<IsLatest>false</IsLatest>"),
        ("Version", b"<LastModified/>"),
        ("Version", b"<ETag/>"),
        ("Version", b"<Size/>"),
        ("Version", b"<StorageClass/>"),
        ("Version", b"<ChecksumType/>"),
        ("Version", b"<Owner/>"),
        ("Version", b"<RestoreStatus/>"),
        ("DeleteMarker", b"<Key>unused</Key>"),
        ("DeleteMarker", b"<VersionId>unused</VersionId>"),
        ("DeleteMarker", b"<IsLatest>false</IsLatest>"),
        ("DeleteMarker", b"<LastModified/>"),
        ("DeleteMarker", b"<Owner/>"),
    ],
)
def test_entry_singletons_reject_duplicates(kind: str, duplicate: bytes) -> None:
    _assert_decode_error(_page(_entry(kind, extra=duplicate + duplicate)))


@pytest.mark.parametrize(
    "nested",
    [
        b"<Owner><ID/><ID/></Owner>",
        b"<Owner><DisplayName/><DisplayName/></Owner>",
        b"<RestoreStatus><IsRestoreInProgress>false</IsRestoreInProgress>"
        b"<IsRestoreInProgress>true</IsRestoreInProgress></RestoreStatus>",
        b"<RestoreStatus><RestoreExpiryDate/><RestoreExpiryDate/></RestoreStatus>",
    ],
)
def test_nested_metadata_singletons_reject_duplicates(nested: bytes) -> None:
    _assert_decode_error(_page(_entry(extra=nested)))


@pytest.mark.parametrize(
    "mutation",
    [
        (b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>False</IsTruncated>"),
        (b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>0</IsTruncated>"),
        (b"<IsLatest>true</IsLatest>", b"<IsLatest>TRUE</IsLatest>"),
        (b"<IsLatest>true</IsLatest>", b"<IsLatest>1</IsLatest>"),
    ],
)
def test_noncanonical_page_and_entry_booleans_are_rejected(
    mutation: tuple[bytes, bytes],
) -> None:
    _assert_decode_error(VALID.replace(*mutation))


@pytest.mark.parametrize("value", ["True", "0", " false ", ""])
def test_noncanonical_restore_boolean_is_rejected(value: str) -> None:
    _assert_decode_error(
        _page(
            _entry(
                extra=(
                    b"<RestoreStatus><IsRestoreInProgress>"
                    + value.encode()
                    + b"</IsRestoreInProgress></RestoreStatus>"
                )
            )
        )
    )


@pytest.mark.parametrize(
    "body",
    [
        VALID.replace(b"history-probe", b"different", 1),
        VALID.replace(ENCODED_PREFIX.encode(), b"checkpoints%2F22222222%2F", 1),
        VALID.replace((ENCODED_PREFIX + "a%252B%2B%E2%98%83").encode(), b"outside"),
        _page(_entry(), fields=_required_fields(b"<KeyMarker>outside</KeyMarker>")),
        _page(
            _entry(),
            fields=_truncated_fields(b"<NextKeyMarker>outside</NextKeyMarker>"),
        ),
    ],
    ids=["bucket", "prefix", "entry-key", "echo-key", "next-key"],
)
def test_all_textual_scope_mismatches_are_rejected(body: bytes) -> None:
    _assert_decode_error(body, "SCOPE_MISMATCH")


@pytest.mark.parametrize(
    ("fields", "kwargs"),
    [
        (
            _required_fields(f"<KeyMarker>{ENCODED_PREFIX}other</KeyMarker>".encode()),
            {"key_marker": PREFIX + "current"},
        ),
        (
            _required_fields(b"<VersionIdMarker>other</VersionIdMarker>"),
            {"key_marker": PREFIX + "current", "version_id_marker": "current"},
        ),
        (REQUIRED_FIELDS, {"key_marker": PREFIX + "current"}),
        (
            _required_fields(f"<KeyMarker>{ENCODED_PREFIX}current</KeyMarker>".encode()),
            {"key_marker": PREFIX + "current", "version_id_marker": "current"},
        ),
    ],
    ids=["key-disagrees", "version-disagrees", "key-missing", "version-missing"],
)
def test_request_marker_echo_disagreement_is_rejected(
    fields: tuple[bytes, ...], kwargs: dict[str, Any]
) -> None:
    _assert_decode_error(_page(fields=fields), "CURSOR_INVALID", **kwargs)


@pytest.mark.parametrize(
    "body",
    [
        _page(fields=_required_fields(b"<CommonPrefixes/>")),
        _page(fields=_required_fields(b"<Unexpected/>")),
        _page(_entry(extra=b"<Unexpected/>")),
        _page(_entry(extra=b"<Owner><Unexpected/></Owner>")),
        _page(_entry().replace(b"object</Key>", b"object<Nested/></Key>")),
        _page(_entry("DeleteMarker", extra=b"<RestoreStatus/>")),
        _page(fields=_required_fields(b"mixed")),
        _page(_entry(extra=b"mixed")),
        ROOT_OPEN.replace(b">", b' unexpected="value">', 1)
        + b"".join(REQUIRED_FIELDS)
        + ROOT_CLOSE,
        _page(_entry().replace(b"<Version>", b'<Version unexpected="value">')),
        _page(fields=_required_fields(b"<Delimiter>/</Delimiter>")),
        _page(fields=_required_fields(b"<CommonPrefixes><Prefix/></CommonPrefixes>")),
        _page().replace(NS.encode(), b"urn:wrong", 1),
        _page().replace(f' xmlns="{NS}"'.encode(), b"", 1),
        b'<Wrong xmlns="' + NS.encode() + b'">' + b"".join(REQUIRED_FIELDS) + b"</Wrong>",
        _page(_entry()).replace(b"<Key>", b'<Key xmlns="">', 1),
        VALID + VALID,
        VALID + b"trailing",
    ],
    ids=[
        "common-prefix-empty",
        "unknown-root",
        "unknown-entry",
        "unknown-nested",
        "scalar-child",
        "restore-on-delete-marker",
        "mixed-root",
        "mixed-entry",
        "root-attribute",
        "entry-attribute",
        "delimiter",
        "common-prefix-nested",
        "wrong-namespace",
        "absent-namespace",
        "wrong-root",
        "namespace-reset",
        "second-root",
        "trailing-text",
    ],
)
def test_unknown_structure_attributes_namespaces_and_content_are_rejected(body: bytes) -> None:
    _assert_decode_error(body)


def test_additional_namespace_declarations_are_the_only_admitted_attributes() -> None:
    module = _module()
    body = VALID.replace(ROOT_OPEN, ROOT_OPEN[:-1] + b' xmlns:unused="urn:unused">', 1)
    result = module.decode_checkpoint_version_page(body, bucket="history-probe", org_id=ORG)
    assert len(result.versions) == len(result.delete_markers) == 1


@pytest.mark.parametrize("bad", ["%", "%0", "%GG", "%FF", "雪", "%C0%AF"])
def test_malformed_percent_and_utf8_sequences_are_rejected(bad: str) -> None:
    _assert_decode_error(_page(_entry(key=ENCODED_PREFIX + bad)))


@pytest.mark.parametrize("field", ["Prefix", "KeyMarker", "NextKeyMarker", "Key"])
def test_every_percent_decoded_response_field_uses_strict_decoding(field: str) -> None:
    if field == "Prefix":
        fields = tuple(
            b"<Prefix>%GG</Prefix>" if item.startswith(b"<Prefix>") else item
            for item in REQUIRED_FIELDS
        )
        body = _page(fields=fields)
    elif field == "KeyMarker":
        body = _page(fields=_required_fields(b"<KeyMarker>%GG</KeyMarker>"))
    elif field == "NextKeyMarker":
        body = _page(fields=_required_fields(b"<NextKeyMarker>%GG</NextKeyMarker>"))
    else:
        body = _page(_entry(key=ENCODED_PREFIX + "%GG"))
    _assert_decode_error(body)


def test_key_is_percent_decoded_exactly_once_and_plus_remains_plus() -> None:
    module = _module()
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=ENCODED_PREFIX + "double%252F+literal")),
        bucket="history-probe",
        org_id=ORG,
    )
    assert result.versions[0].key == PREFIX + "double%2F+literal"


def test_opaque_version_fields_are_never_percent_decoded_or_normalized() -> None:
    module = _module()
    fields = _truncated_fields(
        f"<NextKeyMarker>{ENCODED_PREFIX}next</NextKeyMarker>".encode(),
        "<NextVersionIdMarker>null%2F+/ None 雪</NextVersionIdMarker>".encode(),
    )
    result = module.decode_checkpoint_version_page(
        _page(_entry(version_id="null%2F+/ None 雪"), fields=fields),
        bucket="history-probe",
        org_id=ORG,
    )
    assert result.versions[0].version_id == "null%2F+/ None 雪"
    assert result.next_version_id_marker == "null%2F+/ None 雪"


@pytest.mark.parametrize(
    "body",
    [
        _page(_entry(key="")),
        _page(_entry(version_id="")),
        _page(_entry(key=ENCODED_PREFIX + ("a" * (1025 - len(PREFIX))))),
        _page(_entry(version_id="v" * 1025)),
        _page(_entry(key=ENCODED_PREFIX + ("雪" * 400))),
    ],
    ids=[
        "empty-key",
        "empty-version",
        "oversized-key",
        "oversized-version",
        "non-ascii-encoded-key",
    ],
)
def test_empty_oversized_or_nonascii_labels_are_rejected(body: bytes) -> None:
    _assert_decode_error(body)


@pytest.mark.parametrize(
    "body",
    [
        b'<?xml version="1.1"?>' + VALID,
        b'<?xml version="1.0" encoding="ISO-8859-1"?>' + VALID,
        b'<?xml version="1.0" encoding="UTF8"?>' + VALID,
        b"\xff" + VALID,
        VALID[:-1],
        b"not xml",
    ],
    ids=["xml-1.1", "latin-1", "utf8-alias", "raw-invalid-utf8", "truncated", "not-xml"],
)
def test_only_strict_utf8_xml_1_0_is_accepted(body: bytes) -> None:
    _assert_decode_error(body)


def test_explicit_utf8_bytes_remain_accepted() -> None:
    module = _module()
    body = VALID.decode("utf-8").encode("utf-8")
    result = module.decode_checkpoint_version_page(body, bucket="history-probe", org_id=ORG)
    assert len(result.versions) == len(result.delete_markers) == 1


@pytest.mark.parametrize(
    "encoding",
    ["utf-16-le", "utf-16-be"],
    ids=["little-endian", "big-endian"],
)
def test_bomless_utf16_autodetection_is_rejected(encoding: str) -> None:
    body = VALID.decode("utf-8").encode(encoding)
    _assert_decode_error(body)


@pytest.mark.parametrize(
    "body",
    [
        b"\x00" + VALID,
        VALID.replace(b"history-probe", b"history\x00-probe", 1),
        VALID.replace(
            b"<VersionId>opaque%2F+/</VersionId>",
            b"<VersionId><![CDATA[opaque\x00]]></VersionId>",
        ),
    ],
    ids=["document", "scalar-text", "cdata"],
)
def test_literal_nul_xml_octets_are_rejected(body: bytes) -> None:
    _assert_decode_error(body)


@pytest.mark.parametrize(
    "body",
    [
        VALID.decode("utf-8").encode("utf-16"),
        VALID.decode("utf-8").encode("utf-32-le"),
        VALID.decode("utf-8").encode("utf-32-be"),
        b'<?xml version="1.0" encoding="UTF-16"?>' + VALID,
    ],
    ids=["utf16-bom", "utf32-little-endian", "utf32-big-endian", "declared-utf16"],
)
def test_other_non_utf8_autodetection_paths_remain_rejected(body: bytes) -> None:
    _assert_decode_error(body)


def test_utf8_bom_and_case_insensitive_utf8_declaration_are_accepted() -> None:
    module = _module()
    declared = b'\xef\xbb\xbf<?xml version="1.0" encoding="uTf-8"?>' + VALID
    result = module.decode_checkpoint_version_page(declared, bucket="history-probe", org_id=ORG)
    assert len(result.versions) == len(result.delete_markers) == 1


@pytest.mark.parametrize(
    "injection",
    [
        b"<!-- rejected -->" + VALID,
        b"<?rejected value?>" + VALID,
        b'<!DOCTYPE ListVersionsResult [<!ENTITY x "value">]>' + VALID,
        b'<!DOCTYPE ListVersionsResult SYSTEM "file:///forbidden">' + VALID,
        b'<!DOCTYPE ListVersionsResult [<!ENTITY ext SYSTEM "file:///forbidden">]>'
        + VALID.replace(b"history-probe", b"&ext;", 1),
    ],
    ids=["comment", "processing-instruction", "internal-entity", "external-dtd", "external-entity"],
)
def test_comments_processing_instructions_dtds_and_entities_are_rejected(
    injection: bytes,
) -> None:
    _assert_decode_error(injection)


@pytest.mark.parametrize(
    ("fields", "entries"),
    [
        (
            _truncated_fields(),
            (),
        ),
        (
            _truncated_fields(b"<NextKeyMarker/>"),
            (_entry(),),
        ),
        (
            _truncated_fields(b"<NextVersionIdMarker>v</NextVersionIdMarker>"),
            (_entry(),),
        ),
        (
            _required_fields(f"<NextKeyMarker>{ENCODED_PREFIX}next</NextKeyMarker>".encode()),
            (_entry(),),
        ),
        (_required_fields(b"<NextVersionIdMarker>v</NextVersionIdMarker>"), (_entry(),)),
    ],
    ids=[
        "truncated-empty-page",
        "truncated-empty-next",
        "truncated-version-only",
        "terminal-next-key",
        "terminal-next-version",
    ],
)
def test_invalid_terminal_and_truncated_cursor_shapes_are_rejected(
    fields: tuple[bytes, ...], entries: tuple[bytes, ...]
) -> None:
    _assert_decode_error(_page(*entries, fields=fields), "CURSOR_INVALID")


@pytest.mark.parametrize("with_version", [False, True], ids=["key-only", "pair"])
def test_immediate_repeated_cursor_is_rejected(with_version: bool) -> None:
    current_version = "v1" if with_version else None
    fields = (
        b"<Name>history-probe</Name>",
        f"<Prefix>{ENCODED_PREFIX}</Prefix>".encode(),
        f"<KeyMarker>{ENCODED_PREFIX}same</KeyMarker>".encode(),
        b"<VersionIdMarker>v1</VersionIdMarker>" if with_version else b"<VersionIdMarker/>",
        b"<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType><IsTruncated>true</IsTruncated>",
        f"<NextKeyMarker>{ENCODED_PREFIX}same</NextKeyMarker>".encode(),
        b"<NextVersionIdMarker>v1</NextVersionIdMarker>"
        if with_version
        else b"<NextVersionIdMarker/>",
    )
    _assert_decode_error(
        _page(_entry(), fields=fields),
        "CURSOR_INVALID",
        key_marker=PREFIX + "same",
        version_id_marker=current_version,
    )


def test_body_size_exact_boundary_is_a_valid_padded_document() -> None:
    module = _module()
    body = VALID + (b" " * (16_777_216 - len(VALID)))
    result = module.decode_checkpoint_version_page(body, bucket="history-probe", org_id=ORG)
    assert len(body) == 16_777_216
    assert len(result.versions) == len(result.delete_markers) == 1


def test_body_size_one_over_is_body_limit() -> None:
    body = VALID + (b" " * (16_777_217 - len(VALID)))
    assert len(body) == 16_777_217
    _assert_decode_error(body, "BODY_LIMIT")


def test_encoded_and_decoded_label_exact_boundaries_are_valid() -> None:
    module = _module()
    decoded_key = PREFIX + ("a" * (1024 - len(PREFIX)))
    encoded_key = "".join(f"%{ord(character):02X}" for character in decoded_key)
    assert len(encoded_key) == 3072
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=encoded_key, version_id="v" * 1024)),
        bucket="history-probe",
        org_id=ORG,
    )
    assert result.versions[0].key == decoded_key
    assert result.versions[0].version_id == "v" * 1024


def test_encoded_label_one_over_is_rejected() -> None:
    decoded_key = PREFIX + ("a" * (1024 - len(PREFIX)))
    encoded_key = "".join(f"%{ord(character):02X}" for character in decoded_key) + "a"
    assert len(encoded_key) == 3073
    _assert_decode_error(_page(_entry(key=encoded_key)))


def test_multibyte_label_byte_boundaries_are_exact() -> None:
    module = _module()
    encoded_suffix = "%E9%9B%AA" * 325
    version_id = ("雪" * 341) + "a"
    result = module.decode_checkpoint_version_page(
        _page(_entry(key=ENCODED_PREFIX + encoded_suffix, version_id=version_id)),
        bucket="history-probe",
        org_id=ORG,
    )
    assert len(result.versions[0].key.encode("utf-8")) == 1024
    assert result.versions[0].key == PREFIX + ("雪" * 325)
    assert len(result.versions[0].version_id.encode("utf-8")) == 1024
    assert result.versions[0].version_id == version_id

    _assert_decode_error(
        _page(_entry(key=ENCODED_PREFIX + encoded_suffix + "a", version_id=version_id))
    )
    _assert_decode_error(
        _page(_entry(key=ENCODED_PREFIX + encoded_suffix, version_id=version_id + "b"))
    )


def test_multibyte_input_and_response_marker_byte_boundaries_are_exact() -> None:
    module = _module()
    current_key = PREFIX + ("雪" * 325)
    encoded_key = ENCODED_PREFIX + ("%E9%9B%AA" * 325)
    current_version = ("雪" * 341) + "a"
    fields = _required_fields(
        f"<KeyMarker>{encoded_key}</KeyMarker>".encode(),
        f"<VersionIdMarker>{current_version}</VersionIdMarker>".encode(),
    )
    result = module.decode_checkpoint_version_page(
        _page(fields=fields),
        bucket="history-probe",
        org_id=ORG,
        key_marker=current_key,
        version_id_marker=current_version,
    )
    assert len(current_key.encode("utf-8")) == 1024
    assert len(current_version.encode("utf-8")) == 1024
    assert result.truncated is False

    _assert_input_error(VALID, key_marker=current_key + "a")
    _assert_input_error(
        VALID,
        key_marker=current_key,
        version_id_marker=current_version + "b",
    )


def test_input_and_response_marker_exact_byte_boundaries_are_valid() -> None:
    module = _module()
    current_key = PREFIX + ("k" * (1024 - len(PREFIX)))
    current_version = "v" * 1024
    fields = _required_fields(
        f"<KeyMarker>{current_key}</KeyMarker>".encode(),
        f"<VersionIdMarker>{current_version}</VersionIdMarker>".encode(),
    )
    result = module.decode_checkpoint_version_page(
        _page(fields=fields),
        bucket="history-probe",
        org_id=ORG,
        key_marker=current_key,
        version_id_marker=current_version,
    )
    assert len(current_key.encode()) == len(current_version.encode()) == 1024
    assert result.truncated is False


def test_response_version_marker_one_over_is_rejected() -> None:
    fields = _truncated_fields(
        f"<NextKeyMarker>{ENCODED_PREFIX}next</NextKeyMarker>".encode(),
        f"<NextVersionIdMarker>{'v' * 1025}</NextVersionIdMarker>".encode(),
    )
    _assert_decode_error(_page(_entry(), fields=fields))


def test_one_thousand_mixed_entries_are_all_retained() -> None:
    module = _module()
    entries = tuple(
        _entry(
            "Version" if index % 2 == 0 else "DeleteMarker",
            key=ENCODED_PREFIX + f"object-{index}",
            version_id=f"version-{index}",
        )
        for index in range(1000)
    )
    result = module.decode_checkpoint_version_page(
        _page(*entries), bucket="history-probe", org_id=ORG
    )
    assert len(result.versions) == 500
    assert len(result.delete_markers) == 500
    assert [(item.key, item.version_id) for item in result.versions] == [
        (PREFIX + f"object-{index}", f"version-{index}") for index in range(0, 1000, 2)
    ]
    assert [(item.key, item.version_id) for item in result.delete_markers] == [
        (PREFIX + f"object-{index}", f"version-{index}") for index in range(1, 1000, 2)
    ]


def test_one_thousand_and_one_entries_is_page_limit() -> None:
    entries = tuple(_entry(key=ENCODED_PREFIX + f"object-{index}") for index in range(1001))
    _assert_decode_error(_page(*entries), "PAGE_LIMIT")


def test_depth_four_is_valid_and_depth_five_is_rejected() -> None:
    module = _module()
    at_limit = _entry(extra=b"<Owner><ID>depth-four</ID></Owner>")
    result = module.decode_checkpoint_version_page(
        _page(at_limit), bucket="history-probe", org_id=ORG
    )
    assert len(result.versions) == 1
    over = _entry(extra=b"<Owner><ID><Unexpected/></ID></Owner>")
    _assert_decode_error(_page(over))


def test_element_count_exact_boundary_is_valid_and_one_over_is_rejected() -> None:
    module = _module()
    # root + five required fields + Version + three required entry fields = ten elements.
    at_limit = _entry(extra=b"<ChecksumAlgorithm/>" * 32_758)
    result = module.decode_checkpoint_version_page(
        _page(at_limit), bucket="history-probe", org_id=ORG
    )
    assert len(result.versions) == 1
    over = _entry(extra=b"<ChecksumAlgorithm/>" * 32_759)
    _assert_decode_error(_page(over))


def test_scalar_utf8_byte_boundary_is_valid_and_one_over_is_rejected() -> None:
    module = _module()
    valid = _entry(extra=b"<ETag>" + (b"a" * 8192) + b"</ETag>")
    result = module.decode_checkpoint_version_page(_page(valid), bucket="history-probe", org_id=ORG)
    assert len(result.versions) == 1
    invalid = _entry(extra=b"<ETag>" + (b"a" * 8193) + b"</ETag>")
    _assert_decode_error(_page(invalid))


def test_multibyte_scalar_utf8_byte_boundary_is_valid_and_one_over_is_rejected() -> None:
    module = _module()
    exact_text = "雪" * 2730 + "aa"
    assert len(exact_text.encode("utf-8")) == 8192
    valid = _entry(extra=f"<ETag>{exact_text}</ETag>".encode())
    result = module.decode_checkpoint_version_page(_page(valid), bucket="history-probe", org_id=ORG)
    assert len(result.versions) == 1

    over_text = exact_text + "a"
    assert len(over_text.encode("utf-8")) == 8193
    invalid = _entry(extra=f"<ETag>{over_text}</ETag>".encode())
    _assert_decode_error(_page(invalid))


def test_nested_optional_metadata_cannot_evade_scalar_limit() -> None:
    oversized = b"<Owner><DisplayName>" + (b"a" * 8193) + b"</DisplayName></Owner>"
    _assert_decode_error(_page(_entry(extra=oversized)))


def test_late_failure_after_valid_entry_returns_no_partial_page() -> None:
    _assert_decode_error(_page(_entry(key=ENCODED_PREFIX + "valid"), b"<Unexpected/>"))


@pytest.mark.parametrize("code", [None, "UNKNOWN", "response_invalid", 1])
def test_decode_error_constructor_rejects_unknown_codes(code: Any) -> None:
    module = _module()
    error = _capture(lambda: module.CheckpointVersionPageDecodeError(code))
    assert type(error) is ValueError
    assert str(error) == "invalid checkpoint version page decode code"


class _BucketSubclass(str):
    pass


class _BytesSubclass(bytes):
    pass


class _UUIDSubclass(UUID):
    pass


@pytest.mark.parametrize(
    ("body", "kwargs"),
    [
        (bytearray(VALID), {}),
        (_BytesSubclass(VALID), {}),
        (VALID, {"bucket": 1}),
        (VALID, {"bucket": _BucketSubclass("history-probe")}),
        (VALID, {"bucket": "Invalid_Bucket"}),
        (VALID, {"org_id": str(ORG)}),
        (VALID, {"org_id": _UUIDSubclass(str(ORG))}),
        (VALID, {"key_marker": 1}),
        (VALID, {"key_marker": _BucketSubclass(PREFIX + "key")}),
        (VALID, {"key_marker": ""}),
        (VALID, {"key_marker": "\ud800"}),
        (VALID, {"key_marker": PREFIX + ("a" * (1025 - len(PREFIX)))}),
        (VALID, {"key_marker": "outside"}),
        (VALID, {"version_id_marker": "v1"}),
        (VALID, {"key_marker": PREFIX + "key", "version_id_marker": ""}),
        (
            VALID,
            {
                "key_marker": PREFIX + "key",
                "version_id_marker": _BucketSubclass("version"),
            },
        ),
        (VALID, {"key_marker": PREFIX + "key", "version_id_marker": "\ud800"}),
        (VALID, {"key_marker": PREFIX + "key", "version_id_marker": "v" * 1025}),
    ],
    ids=[
        "body-type",
        "body-subclass",
        "bucket-type",
        "bucket-subclass",
        "bucket-r73",
        "org-type",
        "org-subclass",
        "key-marker-type",
        "key-marker-subclass",
        "empty-key-marker",
        "surrogate-key-marker",
        "long-key-marker",
        "key-marker-scope",
        "version-without-key",
        "empty-version-marker",
        "version-marker-subclass",
        "surrogate-version-marker",
        "long-version-marker",
    ],
)
def test_malformed_call_arguments_are_fixed_input_errors(body: Any, kwargs: dict[str, Any]) -> None:
    _assert_input_error(body, **kwargs)


def test_argument_validation_precedes_body_limit() -> None:
    _assert_input_error(b"x" * 16_777_217, bucket="Invalid_Bucket")


def test_empty_body_is_response_invalid() -> None:
    _assert_decode_error(b"")


@pytest.mark.parametrize(
    "fault",
    [
        RuntimeError("unexpected parser fault"),
        MemoryError("fatal memory fault"),
        KeyboardInterrupt("fatal interrupt"),
        SystemExit(17),
        ExceptionGroup("group", [RuntimeError("nested")]),
        BaseExceptionGroup("base-group", [KeyboardInterrupt("nested fatal")]),
    ],
)
def test_injected_parser_factory_faults_preserve_exact_identity(
    monkeypatch: pytest.MonkeyPatch, fault: BaseException
) -> None:
    module = _module()

    def raise_fault() -> None:
        raise fault

    monkeypatch.setattr(module, "_parser_factory", raise_fault)
    assert (
        _capture(
            lambda: module.decode_checkpoint_version_page(VALID, bucket="history-probe", org_id=ORG)
        )
        is fault
    )


def test_decoder_performs_no_file_network_or_provider_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("decoder attempted external access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = module.decode_checkpoint_version_page(VALID, bucket="history-probe", org_id=ORG)
    assert len(result.versions) == len(result.delete_markers) == 1
