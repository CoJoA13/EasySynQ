"""Private one-request worker for the isolated original version-page reader."""

from __future__ import annotations

import os
import resource
import sys
from collections.abc import Callable
from typing import BinaryIO

_ADDRESS_SPACE_BYTES = 536_870_912
_CPU_SECONDS = 20
_FILE_DESCRIPTORS = 64
_FILE_BYTES = 0
_CORE_BYTES = 0
_REQUEST_MAX_BYTES = 131_072
_IO_CHUNK_BYTES = 8_192


def _apply_limits() -> None:
    required = (
        (resource.RLIMIT_AS, _ADDRESS_SPACE_BYTES),
        (resource.RLIMIT_CPU, _CPU_SECONDS),
        (resource.RLIMIT_NOFILE, _FILE_DESCRIPTORS),
        (resource.RLIMIT_FSIZE, _FILE_BYTES),
        (resource.RLIMIT_CORE, _CORE_BYTES),
    )
    for limit, value in required:
        resource.setrlimit(limit, (value, value))
    for limit, value in required:
        if resource.getrlimit(limit) != (value, value):
            raise RuntimeError("isolated worker resource limit verification failed")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = stream.read(min(_IO_CHUNK_BYTES, size - len(output)))
        if not chunk:
            raise EOFError("truncated isolated worker frame")
        output.extend(chunk)
    return bytes(output)


def _read_frame(stream: BinaryIO, maximum: int) -> bytes:
    declared = int.from_bytes(_read_exact(stream, 4), "big")
    if declared > maximum:
        raise ValueError("isolated worker input limit exceeded")
    payload = _read_exact(stream, declared)
    if stream.read(1) != b"":
        raise ValueError("isolated worker received trailing input")
    return payload


def _write_all(write: Callable[[bytes], int | None], payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        piece = payload[offset : offset + _IO_CHUNK_BYTES]
        written = write(piece)
        if type(written) is not int or not 0 < written <= len(piece):
            raise OSError("isolated worker output write made no progress")
        offset += written


def _main() -> int:
    try:
        _apply_limits()
        source_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
        )
        if len(sys.argv) != 2 or sys.argv[1] != source_root:
            return 1
        sys.path.insert(0, source_root)

        from easysynq_api.services.audit import isolated_version_page, version_page_transport

        ready = isolated_version_page._frame(isolated_version_page._ready_payload())
        _write_all(sys.stdout.buffer.write, ready)
        sys.stdout.buffer.flush()
        request = _read_frame(sys.stdin.buffer, _REQUEST_MAX_BYTES)
        reader, org_id, key_marker, version_id_marker = isolated_version_page._decode_request(
            request
        )
        outcome: (
            version_page_transport.RawCheckpointVersionPage
            | version_page_transport.VersionPageReadError
        )
        try:
            outcome = version_page_transport.read_raw_checkpoint_version_page(
                reader, org_id, key_marker=key_marker, version_id_marker=version_id_marker
            )
        except version_page_transport.VersionPageReadError as error:
            outcome = error
        result = isolated_version_page._frame(isolated_version_page._result_payload(outcome))
        _write_all(sys.stdout.buffer.write, result)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:  # noqa: BLE001 - unexpected child faults exit silently
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
