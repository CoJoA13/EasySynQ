"""Per-install configuration, including the first-run ``setup_state`` one-way latch.

The QMS surface is locked (``/api/v1/*`` → 423/403 setup_incomplete) until the
latch reaches ``OPERATIONAL`` (doc 08 / slice S8). ``canonical_serialize_version``
pins the audit hash-chain serializer so verify-chain stays reproducible (R12, D-4).
"""

from __future__ import annotations

import datetime
import enum
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, false, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class SetupState(enum.Enum):
    UNINITIALIZED = "UNINITIALIZED"
    IN_SETUP = "IN_SETUP"
    OPERATIONAL = "OPERATIONAL"


# The PG ENUM type is created by the Alembic migration; the model references it
# without trying to create it (create_type=False).
setup_state_enum = SAEnum(
    SetupState,
    name="setup_state",
    values_callable=lambda e: [m.value for m in e],
    create_type=False,
)


class SystemConfig(Base):
    __tablename__ = "system_config"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    setup_state: Mapped[SetupState] = mapped_column(
        setup_state_enum,
        default=SetupState.UNINITIALIZED,
        nullable=False,
    )
    canonical_serialize_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    # S6 chain-linker bounded-lag alarm threshold (doc 12 §4.4): a written-but-not-yet-chained
    # tail older than this (seconds) raises a high-severity alarm. Target ≤5 s; default 60 s.
    audit_chain_lag_alarm_seconds: Mapped[int] = mapped_column(
        Integer,
        server_default="60",
        default=60,
        nullable=False,
    )
    # SoD-2 relaxation flag (doc 07 §7.1): when true, the sole approver may also release
    # (the author may *never* release their own edit, regardless). Org-level; defaults strict.
    allow_approver_release: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    # S-rec-3 (doc 06 §4.2): the org opt-in to capture a Mode-B record against a NON-Effective form
    # template (a Draft/InReview edition) for a controlled migration. Defaults OFF — the safe
    # drift-killing default is to require an Effective template; flipping it relaxes that integrity
    # rule org-wide, so it is gated on the SYSTEM-only ``config.update`` (admin) via /admin/config.
    capture_pre_release_templates: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    # SoD-6 relaxation flag (S-rec-4, doc 07 §7): when false (the default, STRICT), a record's
    # capturer may NOT execute its own disposition to DISPOSED/DESTROY (creator-not-disposer; 409
    # ``sod_self_disposition``). When true, the org relaxes that for a small/solo install. It is
    # gated on the SYSTEM-only ``config.update`` (admin) via /admin/config, like the toggles above.
    allow_self_disposition: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    # Severity-aware SoD-4 relaxation flag (S-capa-1 seam; consumed in S-capa-3, decisions-register
    # R39): when false (the default, STRICT), a CAPA's action implementer may NOT verify it. A
    # per-org flip lets a small/solo install relax this for **Minor** CAPAs only (Critical/Major
    # always hard-enforce verifier≠implementer). Exposed now via /admin/config so an operator can
    # pre-set it; the verify-time enforcement lands with S-capa-3. Distinct from
    # ``allow_self_disposition`` (the records SoD-6 flag) — a separate duty separation.
    allow_capa_self_verify: Mapped[bool] = mapped_column(
        Boolean,
        server_default=false(),
        default=False,
        nullable=False,
    )
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # First-run bootstrap secret (S8a, doc 08 §4): an operator-minted, single-use, TTL'd install
    # secret that gates the public /setup/bootstrap → first-admin grant (bootstrap-of-trust). The
    # hash is salted (``<salt_hex>:<sha256(salt+secret)_hex>``); the plaintext is never stored.
    bootstrap_secret_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    bootstrap_consumed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Auth-config gate G-D (S8c, doc 08 §9): the chosen primary login method (LOCAL/FEDERATED —
    # informational; the app always authenticates via Keycloak/OIDC) + the persisted non-bootstrap
    # login proof. ``auth_test_login_ok`` is True ONLY after ``/setup/configure-auth`` proves the
    # OIDC issuer is reachable AND the caller presented a valid non-bootstrap JWT — null/False reads
    # as G-D-unsatisfied (no false-PASS). Federation params live in Keycloak (not here).
    auth_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_test_login_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    auth_test_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
