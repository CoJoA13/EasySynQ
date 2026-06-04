"""S-pack-2 unit proofs — the share-link derived state + the pack content-hash determinism (NIT)."""

from __future__ import annotations

import datetime
import uuid

from easysynq_api.db.models.pack_share_link import PackShareLink
from easysynq_api.domain.packs.content_hash import pack_content_hash

_NOW = datetime.datetime(2026, 6, 4, 12, 0, tzinfo=datetime.UTC)


def _link(**kw: object) -> PackShareLink:
    base = {
        "org_id": uuid.uuid4(),
        "pack_id": uuid.uuid4(),
        "token_digest": "d" * 64,
        "expires_at": _NOW + datetime.timedelta(days=7),
        "created_by": uuid.uuid4(),
    }
    base.update(kw)
    return PackShareLink(**base)


def test_state_is_active_before_expiry() -> None:
    link = _link()
    assert link.state(now=_NOW) == "ACTIVE"
    assert link.is_live(now=_NOW)


def test_state_is_expired_past_expiry() -> None:
    link = _link(expires_at=_NOW - datetime.timedelta(seconds=1))
    assert link.state(now=_NOW) == "EXPIRED"
    assert not link.is_live(now=_NOW)


def test_revoked_beats_expired() -> None:
    # A revoked link reads REVOKED even after it would otherwise have expired (revoke wins).
    link = _link(
        expires_at=_NOW - datetime.timedelta(days=1),
        revoked_at=_NOW - datetime.timedelta(days=2),
    )
    assert link.state(now=_NOW) == "REVOKED"
    assert not link.is_live(now=_NOW)


def test_pack_content_hash_is_deterministic() -> None:
    # The cover sheet carries this seal; an auditor re-hashes to verify → it MUST be reproducible.
    args = dict(
        scope_kind="CLAUSE",
        scope_selector={"clause_ids": ["B", "a", "a"]},
        period_start="2026-01-01",
        period_end="2026-05-31",
        included_record_ids=["r2", "r1"],
        pinned_version_ids=["v1"],
        evidence_sha256s=["sha2", "sha1"],
        excluded_permission_record_ids=["p1"],
        excluded_absence_record_ids=["x1"],
    )
    assert pack_content_hash(**args) == pack_content_hash(**args)  # type: ignore[arg-type]
    assert pack_content_hash(**args).startswith("sha256:")  # type: ignore[arg-type]
