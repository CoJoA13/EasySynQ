"""S-drift-3: hash_object streams sha256 in bounded chunks via the INTERNAL client (no presign)."""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest

from easysynq_api.services.vault import storage


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._io = io.BytesIO(data)
        self.reads: list[int] = []
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        self.reads.append(n)
        return self._io.read(n)

    def close(self) -> None:
        self.closed = True


async def test_hash_object_streams_and_matches_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"\x01\x02" * (3 * 1024 * 1024 // 2) + b"tail"  # >3 MiB → multiple 1 MiB chunks
    body = _FakeBody(data)

    class _FakeClient:
        def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # boto3 API naming
            assert (Bucket, Key) == ("docs", "ab/cd/key")
            return {"Body": body}

    monkeypatch.setattr(storage, "_client", lambda: _FakeClient())
    digest = await storage.hash_object("ab/cd/key", bucket="docs")
    assert digest == hashlib.sha256(data).hexdigest()
    # Bounded memory: every read was chunk-sized, and the body was closed.
    assert all(n == 1 << 20 for n in body.reads)
    assert body.closed
