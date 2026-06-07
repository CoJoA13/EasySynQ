"""Presigned URLs sign against the browser-facing host (``s3_public_endpoint`` when set) so SigV4
validates against the host the browser hits — no post-hoc host rewrite to break it; else the
internal ``s3_endpoint`` (the CI/test default)."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from easysynq_api.services.vault import storage

pytestmark = pytest.mark.unit


def _settings(*, public: str) -> SimpleNamespace:
    return SimpleNamespace(
        s3_endpoint="http://minio:9000",
        s3_public_endpoint=public,
        s3_access_key="key",
        s3_secret_key="secret",
        s3_region="us-east-1",
        s3_presign_expiry_seconds=900,
    )


def test_presign_signs_against_public_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "get_settings", lambda: _settings(public="http://localhost:9000"))
    url = storage._presign("get_object", "sha-abc", "documents", {})
    parts = urlsplit(url)
    assert parts.netloc == "localhost:9000"  # the browser-facing host, NOT the internal minio:9000
    assert parts.path == "/documents/sha-abc"  # path-style bucket/key preserved
    assert (
        "Signature" in parts.query
    )  # a real presigned URL (SigV2/V4 both carry a Signature param)


def test_presign_falls_back_to_internal_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "get_settings", lambda: _settings(public=""))
    url = storage._presign("put_object", "sha-xyz", "staging", {"ContentType": "application/pdf"})
    assert (
        urlsplit(url).netloc == "minio:9000"
    )  # empty public → internal endpoint (CI/test default)
