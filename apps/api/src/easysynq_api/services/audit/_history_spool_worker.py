"""Fresh storage-only executable; OS ceilings precede application imports/input."""

from __future__ import annotations

import dataclasses
import os
import resource
import sys
from typing import Any, BinaryIO


def _apply_limits() -> None:
    limits = (
        (resource.RLIMIT_AS, 536_870_912),
        (resource.RLIMIT_CPU, 120),
        (resource.RLIMIT_NOFILE, 32),
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_FSIZE, 1_073_741_824),
    )
    for kind, value in limits:
        resource.setrlimit(kind, (value, value))
    for kind, value in limits:
        if resource.getrlimit(kind) != (value, value):
            raise RuntimeError("history worker resource policy unavailable")


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        body = stream.read(min(size - len(output), 65_536))
        if not body:
            raise EOFError("history worker frame truncated")
        output.extend(body)
    return bytes(output)


def _read_frame(stream: BinaryIO) -> bytes:
    length = int.from_bytes(_read_exact(stream, 4), "big")
    if not 0 < length <= 131_072:
        raise ValueError("history worker frame invalid")
    return _read_exact(stream, length)


def _write(stream: BinaryIO, body: bytes) -> None:
    offset = 0
    while offset < len(body):
        written = stream.write(body[offset : offset + 65_536])
        if type(written) is not int or not 0 < written <= min(65_536, len(body) - offset):
            raise OSError("history worker pipe made no progress")
        offset += written
    stream.flush()


def _main() -> int:
    store = None
    try:
        _apply_limits()
        source_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
        )
        if len(sys.argv) != 2 or sys.argv[1] != source_root:
            return 1
        sys.path.insert(0, source_root)
        from easysynq_api.services.audit import _history_spool_protocol as wire
        from easysynq_api.services.audit._history_spool_store import _SpoolStore
        from easysynq_api.services.audit.history_collection import HistoryCollectionError

        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

        def send(value: dict[str, Any]) -> None:
            _write(stdout, wire.frame(wire.encode(value)))

        send({"op": "READY", "version": 1})
        sequence = 0
        while True:
            request = wire.metadata(_read_frame(stdin))
            sequence += 1
            if wire.integer(request.get("id"), 1, 2_147_483_647) != sequence:
                wire.invalid()
            op = request.get("op")
            if type(op) is not str:
                wire.invalid()
            response = {"op": "ACK", "id": sequence}
            try:
                if store is None:
                    if op != "INIT" or sequence != 1:
                        wire.invalid()
                    org, witnesses, limits = wire.decode_init(request)
                    ceiling = limits.maximum_spool_bytes
                    resource.setrlimit(resource.RLIMIT_FSIZE, (ceiling, ceiling))
                    if resource.getrlimit(resource.RLIMIT_FSIZE) != (ceiling, ceiling):
                        raise HistoryCollectionError("RUNTIME_UNSUPPORTED")
                    store = _SpoolStore(org, witnesses, limits)
                    response["version"] = 1
                elif op == "RESERVE_PAGE":
                    wire.fields(request, {"op", "id", "witness", "key", "version"})
                    ticket = store.reserve_page(
                        request["witness"], request["key"], request["version"]
                    )
                    response["ticket"] = dataclasses.asdict(ticket) if ticket is not None else None
                elif op == "PAGE_BEGIN":
                    wire.fields(request, {"op", "id", "ticket", "length"})
                    ticket = wire.ticket(request["ticket"])
                    length = wire.integer(request["length"], 1, wire.PAGE_MAX)
                    store.page_begin(ticket, length)
                    offset = 0
                    while offset < length:
                        body = wire.decode_chunk(
                            _read_frame(stdin), sequence, offset, length - offset
                        )
                        store.page_chunk(body)
                        offset += len(body)
                    end = wire.metadata(_read_frame(stdin))
                    wire.fields(end, {"op", "id"})
                    if end["op"] != "PAGE_END" or wire.integer(end["id"], 1) != sequence:
                        wire.invalid()
                    response["admission"] = dataclasses.asdict(store.page_end())
                elif op == "BODY":
                    wire.fields(request, {"op", "id", "ordinal", "length"})
                    length = wire.integer(request["length"], 0, wire.CHUNK_MAX)
                    body = (
                        wire.decode_chunk(_read_frame(stdin), sequence, 0, length)
                        if length
                        else b""
                    )
                    if len(body) != length:
                        wire.invalid()
                    store.record_body(request["ordinal"], body)
                elif op == "LIST_FAILURE":
                    wire.fields(request, {"op", "id", "ticket", "code"})
                    store.record_list_failure(wire.ticket(request["ticket"]), request["code"])
                elif op == "VERSION_FAILURE":
                    wire.fields(
                        request, {"op", "id", "ordinal", "code", "ineligible", "delete_marker"}
                    )
                    store.record_version_failure(
                        request["ordinal"],
                        request["code"],
                        ineligible=request["ineligible"],
                        delete_marker=request["delete_marker"],
                    )
                elif op == "CYCLE":
                    wire.fields(request, {"op", "id", "witness"})
                    store.record_cycle(request["witness"])
                elif op == "FINISH":
                    wire.fields(request, {"op", "id"})
                    if stdin.read(1) != b"":
                        wire.invalid()
                    summary = store.finish()
                    store.close()
                    store = None
                    response["summary"] = wire.summary_payload(summary)
                    send(response)
                    return 0
                else:
                    wire.invalid()
                send(response)
            except HistoryCollectionError as error:
                if store is not None:
                    store.close()
                    store = None
                send({"op": "ERROR", "id": sequence, "code": error.code})
                return 1
    except BaseException:  # noqa: BLE001 - child faults never expose exception text
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(_main())
