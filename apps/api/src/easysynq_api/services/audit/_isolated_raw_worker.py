"""Private one-request worker for the isolated retained-version reader."""

from __future__ import annotations

import os
import resource
import sys
from collections.abc import Callable
from typing import BinaryIO

_ADDRESS_SPACE_BYTES = 536_870_912
_CPU_SECONDS = 10
_FILE_DESCRIPTORS = 64
_FILE_BYTES = 0
_CORE_BYTES = 0
_REQUEST_MAX_BYTES = 131_072


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
        chunk = stream.read(size - len(output))
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
        written = write(payload[offset:])
        if written is None:
            written = len(payload) - offset
        if type(written) is not int or written <= 0:
            raise OSError("isolated worker output write made no progress")
        offset += written


def _main() -> int:
    try:
        _apply_limits()
        if len(sys.argv) != 2:
            return 1
        source_root = sys.argv[1]
        if not os.path.isabs(source_root):
            return 1
        sys.path.insert(0, source_root)

        from easysynq_api.services.audit import isolated_raw, raw_transport

        ready = isolated_raw._frame(isolated_raw._ready_payload())
        _write_all(sys.stdout.buffer.write, ready)
        sys.stdout.buffer.flush()

        request = _read_frame(sys.stdin.buffer, _REQUEST_MAX_BYTES)
        reader, ref = isolated_raw._decode_request(request)
        outcome: raw_transport.RawCheckpointVersion | raw_transport.RawVersionReadError
        try:
            outcome = raw_transport.read_raw_checkpoint_version(reader, ref)
        except raw_transport.RawVersionReadError as error:
            outcome = error
        result = isolated_raw._frame(isolated_raw._result_payload(outcome))
        _write_all(sys.stdout.buffer.write, result)
        sys.stdout.buffer.flush()
        return 0
    except BaseException:  # noqa: BLE001 - child faults become only a fixed parent outcome
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
