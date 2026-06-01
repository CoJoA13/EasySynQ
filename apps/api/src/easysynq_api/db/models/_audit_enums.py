"""Native-PG enum bindings for the audit cluster (slice S6, doc 12 §4.2, doc 14 §12).

``actor_type`` and ``audit_object_type`` are closed sets fixed by doc 12 §4.2. ``event_type`` is
the one *extensible* enum: doc 12 §4.2 / doc 14 §12 model it as an enum (not free text), but new
categories are additive via ``ALTER TYPE … ADD VALUE`` in a future migration — so the deferred
Keycloak auth events (``LOGIN_*`` …) and later domains slot in without a schema rewrite. The v1
value set below is the canonical emitted set (vault/lifecycle from S3/S4, authz from S2/S5, and the
S6 integrity events). Created by the Alembic migration; referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class ActorType(enum.Enum):
    user = "user"
    system = "system"  # actor_id is NULL for system/beat jobs (doc 12 §4.2)
    external_auditor = "external_auditor"
    admin = "admin"


class AuditObjectType(enum.Enum):
    document = "document"
    version = "version"
    record = "record"
    permission = "permission"
    user = "user"
    session = "session"
    config = "config"
    audit = "audit"


class EventType(enum.Enum):
    # vault + lifecycle (S3/S4) — verbatim the strings the existing sinks already emit
    DOCUMENT_CREATED = "DOCUMENT_CREATED"
    CHECKOUT = "CHECKOUT"
    CHECKIN = "CHECKIN"
    NO_CHANGE = "NO_CHANGE"
    LOCK_BROKEN = "LOCK_BROKEN"
    SUBMITTED_FOR_REVIEW = "SUBMITTED_FOR_REVIEW"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    RELEASED = "RELEASED"
    SUPERSEDED = "SUPERSEDED"
    REVISION_STARTED = "REVISION_STARTED"
    MADE_OBSOLETE = "MADE_OBSOLETE"
    # authorization (S2/S5) — denied-access attempts (always) + allows (configurable verbosity,
    # doc 12 §4.1 — off by default) + the permission/role/override changes
    ACCESS_DENIED = "ACCESS_DENIED"
    ACCESS_ALLOWED = "ACCESS_ALLOWED"
    TWO_TIER_VIOLATION = "TWO_TIER_VIOLATION"
    PERM_GRANT = "PERM_GRANT"
    PERM_REVOKE = "PERM_REVOKE"
    ROLE_ASSIGN = "ROLE_ASSIGN"
    ROLE_REVOKE = "ROLE_REVOKE"
    OVERRIDE_ADD = "OVERRIDE_ADD"
    OVERRIDE_REMOVE = "OVERRIDE_REMOVE"
    # integrity (S6)
    CHAIN_VERIFY_PASS = "CHAIN_VERIFY_PASS"  # noqa: S105 — enum label, not a credential
    CHAIN_VERIFY_FAIL = "CHAIN_VERIFY_FAIL"
    CHECKPOINT_ANCHORED = "CHECKPOINT_ANCHORED"


class CheckpointSinkKind(enum.Enum):
    worm_bucket = "worm_bucket"
    external_object_store = "external_object_store"
    append_only_syslog = "append_only_syslog"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


actor_type_enum = SAEnum(ActorType, name="actor_type", values_callable=_vals, create_type=False)
audit_object_type_enum = SAEnum(
    AuditObjectType, name="audit_object_type", values_callable=_vals, create_type=False
)
event_type_enum = SAEnum(EventType, name="event_type", values_callable=_vals, create_type=False)
checkpoint_sink_kind_enum = SAEnum(
    CheckpointSinkKind, name="checkpoint_sink_kind", values_callable=_vals, create_type=False
)

# The canonical v1 value tuples, re-used by the migration's enum-create step so the ORM and the
# hand-authored DDL never drift.
ACTOR_TYPE_VALUES = tuple(_vals(ActorType))
AUDIT_OBJECT_TYPE_VALUES = tuple(_vals(AuditObjectType))
EVENT_TYPE_VALUES = tuple(_vals(EventType))
CHECKPOINT_SINK_KIND_VALUES = tuple(_vals(CheckpointSinkKind))
