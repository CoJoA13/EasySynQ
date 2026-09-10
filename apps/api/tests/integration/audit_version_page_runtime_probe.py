"""Finite actual-image probe for strict supplied version-page decoding."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
from collections.abc import Callable, Sequence
from types import FrameType
from uuid import UUID
from xml.parsers import expat

from easysynq_api.services.audit.sink import CheckpointVersionsPage
from easysynq_api.services.audit.version_page import (
    CheckpointVersionPageDecodeError,
    decode_checkpoint_version_page,
)

_BODY_LIMIT = 16_777_216
_ORG = UUID("11111111-1111-4111-8111-111111111111")
_PREFIX = "checkpoints/11111111-1111-4111-8111-111111111111/"
_ENCODED_PREFIX = "checkpoints%2F11111111-1111-4111-8111-111111111111%2F"
_ROOT_OPEN = b'<ListVersionsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
_ROOT_CLOSE = b"</ListVersionsResult>"
_FIELDS = (
    b"<Name>history-probe</Name>"
    + f"<Prefix>{_ENCODED_PREFIX}</Prefix>".encode()
    + b"<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
    + b"<IsTruncated>false</IsTruncated>"
)
_EXPECTED_CASES = (
    "mixed-records-and-opaque-labels",
    "thousand-observation-boundary",
    "combined-page-limit",
    "truncation-numeric-one",
    "truncation-numeric-zero",
    "truncation-uppercase-true",
    "truncation-garbage",
    "truncation-empty",
    "missing-truncation",
    "duplicate-truncation-false-true",
    "duplicate-truncation-true-false",
    "duplicate-root-prefix",
    "malformed-percent-short",
    "malformed-percent-nonhex",
    "invalid-utf8-percent",
    "single-percent-decode",
    "literal-plus-preserved",
    "malicious-doctype",
    "malicious-comment",
    "malformed-xml",
    "scope-binding",
    "key-only-cursor-replay",
    "opaque-two-part-cursor-replay",
    "request-marker-echo",
    "immediate-cursor-repeat",
    "late-entry-failure",
    "exact-body-boundary",
    "body-boundary-one-over",
    "bomless-utf16-little-endian",
    "bomless-utf16-big-endian",
    "raw-nul-xml",
    "percent-decoded-nul-key",
    "valid-utf8-replacement-character",
)


def _entry(
    kind: str = "Version",
    *,
    key: str = _ENCODED_PREFIX + "object",
    version_id: str = "opaque-version",
    latest: str = "false",
) -> bytes:
    return (
        f"<{kind}><Key>{key}</Key><VersionId>{version_id}</VersionId>".encode()
        + f"<IsLatest>{latest}</IsLatest></{kind}>".encode()
    )


def _page(*entries: bytes, fields: bytes = _FIELDS) -> bytes:
    return _ROOT_OPEN + fields + b"".join(entries) + _ROOT_CLOSE


class _Cases:
    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.outcomes: dict[str, str] = {}

    def _record(
        self,
        name: str,
        body: bytes,
        *,
        key_marker: str | None,
        version_id_marker: str | None,
    ) -> None:
        assert name in _EXPECTED_CASES and name not in self.outcomes
        metadata = json.dumps(
            {
                "name": name,
                "body_bytes": len(body),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "arguments": {
                    "bucket": "history-probe",
                    "org_id": str(_ORG),
                    "key_marker": key_marker,
                    "version_id_marker": version_id_marker,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.digest.update(len(metadata).to_bytes(8, "big"))
        self.digest.update(metadata)

    def accept(
        self,
        name: str,
        body: bytes,
        check: Callable[[CheckpointVersionsPage], None],
        *,
        key_marker: str | None = None,
        version_id_marker: str | None = None,
    ) -> None:
        self._record(
            name,
            body,
            key_marker=key_marker,
            version_id_marker=version_id_marker,
        )
        page = decode_checkpoint_version_page(
            body,
            bucket="history-probe",
            org_id=_ORG,
            key_marker=key_marker,
            version_id_marker=version_id_marker,
        )
        check(page)
        self.outcomes[name] = "accepted"

    def reject(
        self,
        name: str,
        body: bytes,
        code: str,
        *,
        key_marker: str | None = None,
        version_id_marker: str | None = None,
    ) -> None:
        self._record(
            name,
            body,
            key_marker=key_marker,
            version_id_marker=version_id_marker,
        )
        try:
            decode_checkpoint_version_page(
                body,
                bucket="history-probe",
                org_id=_ORG,
                key_marker=key_marker,
                version_id_marker=version_id_marker,
            )
        except CheckpointVersionPageDecodeError as error:
            assert error.code == code
            assert str(error) == "checkpoint version page decode failed"
            assert error.__cause__ is None and error.__context__ is None
        else:
            raise AssertionError(f"{name} returned instead of raising")
        self.outcomes[name] = code


def _mixed_check(page: CheckpointVersionsPage) -> None:
    assert [(item.key, item.version_id) for item in page.versions] == [
        (_PREFIX + "a%2B+ snowman-☃", "opaque%2F+/ null")
    ]
    assert [(item.key, item.version_id) for item in page.delete_markers] == [
        (_PREFIX + "deleted", "null")
    ]
    assert page.truncated is False
    assert page.next_key_marker is page.next_version_id_marker is None


def _thousand_check(page: CheckpointVersionsPage) -> None:
    assert len(page.versions) == len(page.delete_markers) == 500
    expected_versions = [
        (_PREFIX + f"version-{index:03d}", f"version-id-{index:03d}") for index in range(499)
    ]
    expected_versions.append(expected_versions[0])
    expected_deletes = [
        (_PREFIX + f"delete-{index:03d}", f"delete-id-{index:03d}") for index in range(499)
    ]
    expected_deletes.append(expected_deletes[0])
    assert [(item.key, item.version_id) for item in page.versions] == expected_versions
    assert [(item.key, item.version_id) for item in page.delete_markers] == expected_deletes


def _single_key(expected: str) -> Callable[[CheckpointVersionsPage], None]:
    def check(page: CheckpointVersionsPage) -> None:
        assert len(page.versions) == 1 and page.versions[0].key == expected

    return check


def _empty_page(page: CheckpointVersionsPage) -> None:
    assert page.versions == page.delete_markers == ()


def _cursor_check(page: CheckpointVersionsPage) -> None:
    assert page.truncated is True
    assert page.next_key_marker == _PREFIX + "next+key"
    assert page.next_version_id_marker == "next%2F+/ snow-雪"


def _key_only_cursor_check(page: CheckpointVersionsPage) -> None:
    assert page.truncated is True
    assert page.next_key_marker == _PREFIX + "next+key"
    assert page.next_version_id_marker is None


def _cursor_fields(*, key: str, version: str, next_key: str, next_version: str) -> bytes:
    return (
        b"<Name>history-probe</Name>"
        + f"<Prefix>{_ENCODED_PREFIX}</Prefix>".encode()
        + f"<KeyMarker>{key}</KeyMarker>".encode()
        + f"<VersionIdMarker>{version}</VersionIdMarker>".encode()
        + b"<MaxKeys>1000</MaxKeys><EncodingType>url</EncodingType>"
        + b"<IsTruncated>true</IsTruncated>"
        + f"<NextKeyMarker>{next_key}</NextKeyMarker>".encode()
        + f"<NextVersionIdMarker>{next_version}</NextVersionIdMarker>".encode()
    )


def _exercise(cases: _Cases) -> None:
    mixed = _page(
        _entry(
            key=_ENCODED_PREFIX + "a%252B%2B%20snowman-%E2%98%83",
            version_id="opaque%2F+/ null",
            latest="true",
        ),
        _entry("DeleteMarker", key=_ENCODED_PREFIX + "deleted", version_id="null"),
    )
    cases.accept("mixed-records-and-opaque-labels", mixed, _mixed_check)

    versions = [
        _entry(key=_ENCODED_PREFIX + f"version-{index:03d}", version_id=f"version-id-{index:03d}")
        for index in range(499)
    ]
    versions.append(versions[0])
    deletes = [
        _entry(
            "DeleteMarker",
            key=_ENCODED_PREFIX + f"delete-{index:03d}",
            version_id=f"delete-id-{index:03d}",
        )
        for index in range(499)
    ]
    deletes.append(deletes[0])
    thousand = _page(*versions, *deletes)
    cases.accept("thousand-observation-boundary", thousand, _thousand_check)
    cases.reject(
        "combined-page-limit",
        _page(*versions, *deletes, _entry(key=_ENCODED_PREFIX + "overflow")),
        "PAGE_LIMIT",
    )

    cases.reject(
        "truncation-numeric-one",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>1</IsTruncated>"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "truncation-numeric-zero",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>0</IsTruncated>"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "truncation-uppercase-true",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>TRUE</IsTruncated>"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "truncation-garbage",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b"<IsTruncated>garbage</IsTruncated>"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "truncation-empty",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b"<IsTruncated/>"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "missing-truncation",
        mixed.replace(b"<IsTruncated>false</IsTruncated>", b""),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "duplicate-truncation-false-true",
        mixed.replace(
            b"<IsTruncated>false</IsTruncated>",
            b"<IsTruncated>false</IsTruncated><IsTruncated>true</IsTruncated>",
        ),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "duplicate-truncation-true-false",
        mixed.replace(
            b"<IsTruncated>false</IsTruncated>",
            b"<IsTruncated>true</IsTruncated><IsTruncated>false</IsTruncated>",
        ),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "duplicate-root-prefix",
        mixed.replace(
            f"<Prefix>{_ENCODED_PREFIX}</Prefix>".encode(),
            (f"<Prefix>{_ENCODED_PREFIX}</Prefix>" * 2).encode(),
        ),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "malformed-percent-short",
        mixed.replace(b"a%252B%2B%20snowman-%E2%98%83", b"bad%2"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "malformed-percent-nonhex",
        mixed.replace(b"a%252B%2B%20snowman-%E2%98%83", b"bad%GG"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "invalid-utf8-percent",
        mixed.replace(b"a%252B%2B%20snowman-%E2%98%83", b"bad%FF"),
        "RESPONSE_INVALID",
    )
    cases.accept(
        "single-percent-decode",
        _page(_entry(key=_ENCODED_PREFIX + "literal%252Fescape")),
        _single_key(_PREFIX + "literal%2Fescape"),
    )
    cases.accept(
        "literal-plus-preserved",
        _page(_entry(key=_ENCODED_PREFIX + "literal+plus")),
        _single_key(_PREFIX + "literal+plus"),
    )
    cases.reject(
        "malicious-doctype",
        b'<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]>' + mixed,
        "RESPONSE_INVALID",
    )
    cases.reject(
        "malicious-comment",
        mixed.replace(b"<Name>", b"<!-- hidden --><Name>", 1),
        "RESPONSE_INVALID",
    )
    cases.reject("malformed-xml", mixed[:-1], "RESPONSE_INVALID")
    cases.reject(
        "scope-binding",
        mixed.replace(_ENCODED_PREFIX.encode(), b"checkpoints%2Fother%2F", 1),
        "SCOPE_MISMATCH",
    )

    current_key = _PREFIX + "current+key"
    current_version = "opaque%2F+/"
    cursor_entry = _entry(key=_ENCODED_PREFIX + "returned", version_id="returned-id")
    cursor_fields = _cursor_fields(
        key=_ENCODED_PREFIX + "current%2Bkey",
        version=current_version,
        next_key=_ENCODED_PREFIX + "next",
        next_version="next-id",
    )
    key_only_fields = _cursor_fields(
        key=_ENCODED_PREFIX + "current%2Bkey",
        version="",
        next_key=_ENCODED_PREFIX + "next%2Bkey",
        next_version="",
    )
    cases.accept(
        "key-only-cursor-replay",
        _page(cursor_entry, fields=key_only_fields),
        _key_only_cursor_check,
        key_marker=current_key,
    )
    opaque_cursor_fields = _cursor_fields(
        key=_ENCODED_PREFIX + "current%2Bkey",
        version=current_version,
        next_key=_ENCODED_PREFIX + "next%2Bkey",
        next_version="next%2F+/ snow-雪",
    )
    cases.accept(
        "opaque-two-part-cursor-replay",
        _page(cursor_entry, fields=opaque_cursor_fields),
        _cursor_check,
        key_marker=current_key,
        version_id_marker=current_version,
    )
    cases.reject(
        "request-marker-echo",
        _page(cursor_entry, fields=cursor_fields),
        "CURSOR_INVALID",
        key_marker=_PREFIX + "different",
        version_id_marker=current_version,
    )
    repeated_fields = _cursor_fields(
        key=_ENCODED_PREFIX + "current%2Bkey",
        version=current_version,
        next_key=_ENCODED_PREFIX + "current%2Bkey",
        next_version=current_version,
    )
    cases.reject(
        "immediate-cursor-repeat",
        _page(cursor_entry, fields=repeated_fields),
        "CURSOR_INVALID",
        key_marker=current_key,
        version_id_marker=current_version,
    )
    cases.reject(
        "late-entry-failure",
        _page(_entry(key=_ENCODED_PREFIX + "valid-first"), _entry(key="")),
        "RESPONSE_INVALID",
    )

    empty = _page()
    exact = (b" " * (_BODY_LIMIT - len(empty))) + empty
    cases.accept(
        "exact-body-boundary",
        exact,
        _empty_page,
    )
    cases.reject("body-boundary-one-over", exact + b" ", "BODY_LIMIT")
    cases.reject(
        "bomless-utf16-little-endian",
        mixed.decode("utf-8").encode("utf-16-le"),
        "RESPONSE_INVALID",
    )
    cases.reject(
        "bomless-utf16-big-endian",
        mixed.decode("utf-8").encode("utf-16-be"),
        "RESPONSE_INVALID",
    )
    cases.reject("raw-nul-xml", b"\x00" + mixed, "RESPONSE_INVALID")
    cases.accept(
        "percent-decoded-nul-key",
        _page(_entry(key=_ENCODED_PREFIX + "control%00key")),
        _single_key(_PREFIX + "control\x00key"),
    )
    cases.accept(
        "valid-utf8-replacement-character",
        _page(_entry(key=_ENCODED_PREFIX + "%EF%BF%BD")),
        _single_key(_PREFIX + "\ufffd"),
    )


def _run(application_image_id: str) -> dict[str, object]:
    cases = _Cases()
    sdk_calls: list[str] = []

    def observe_calls(frame: FrameType, event: str, _argument: object) -> None:
        if event != "call":
            return
        module = frame.f_globals.get("__name__")
        if isinstance(module, str) and (
            module == "boto3"
            or module.startswith("boto3.")
            or module == "botocore"
            or module.startswith("botocore.")
        ):
            sdk_calls.append(module)

    sys.setprofile(observe_calls)
    try:
        _exercise(cases)
    finally:
        sys.setprofile(None)

    assert tuple(cases.outcomes) == _EXPECTED_CASES
    absent = tuple(
        name for name in ("mypy", "pytest", "ruff") if importlib.util.find_spec(name) is None
    )
    assert absent == ("mypy", "pytest", "ruff")
    sdk_not_used = not sdk_calls
    assert sdk_not_used
    return {
        "case_count": len(_EXPECTED_CASES),
        "case_names": list(_EXPECTED_CASES),
        "application_image_id": application_image_id,
        "uid": os.getuid(),
        "sdk_not_used": sdk_not_used,
        "input_vectors_sha256": cases.digest.hexdigest(),
        "outcomes": cases.outcomes,
        "runtime_versions": {
            "python": platform.python_version(),
            "expat": expat.EXPAT_VERSION,
        },
        "dev_packages_absent": list(absent),
        "cleanup_complete": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-image-id", required=True)
    arguments = parser.parse_args(argv)
    image_id = arguments.application_image_id
    assert image_id.startswith("sha256:") and len(image_id) == 71
    assert all(character in "0123456789abcdef" for character in image_id[7:])
    print(json.dumps(_run(image_id), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
