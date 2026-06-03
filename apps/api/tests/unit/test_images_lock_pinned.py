"""S11 air-gap guard — a RELEASE-GATED check that infra/images.lock pins images by @sha256 digest
(doc 03 §15: 'PRODUCTION RELEASES MUST PIN BY @sha256 DIGEST').

Resolving digests needs a connected host with Docker (`just images-update`), so this cannot run in
PR CI. The guard is therefore release-gated: it SKIPS during normal dev/PR CI (floating tags stay
legal while iterating) and only FAILS when ``EASYSYNQ_RELEASE=1`` is set (the release-tag CI run) —
so a release can never ship floating tags, while day-to-day work is unblocked. ``mailpit`` is a
dev-only image (excluded).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _images_lock() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infra" / "images.lock"
        if candidate.exists():
            return candidate.read_text()
    raise AssertionError("infra/images.lock not found above the test directory")


def _release_image_refs() -> list[str]:
    refs: list[str] = []
    for line in _images_lock().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        service, ref = parts[0], parts[1]
        if service == "mailpit":  # dev-profile-only image
            continue
        refs.append(ref)
    return refs


def test_images_lock_parses() -> None:
    """images.lock always has the non-dev service images (regardless of release-gating)."""
    refs = _release_image_refs()
    assert refs, "images.lock yielded no non-dev image refs"


@pytest.mark.skipif(
    os.getenv("EASYSYNQ_RELEASE") != "1",
    reason="digest-pin is a release-ceremony check (set EASYSYNQ_RELEASE=1 on the release run)",
)
def test_release_images_are_digest_pinned() -> None:
    """On a release run, every non-dev image MUST be @sha256-pinned (run `just images-update`)."""
    floating = [ref for ref in _release_image_refs() if "@sha256:" not in ref]
    assert not floating, f"release images.lock has floating tags (pin by @sha256): {floating}"
