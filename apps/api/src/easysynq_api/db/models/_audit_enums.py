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
    # process IA (S9c, doc 02 §3.3) — process/edge events key here; process_link events reuse
    # ``document`` (the link is about the document, the clause_mapping precedent). Added via
    # ``ALTER TYPE audit_object_type ADD VALUE`` in 0019 (the event_type 0011-0017 pattern).
    process = "process"
    # evidence packs (S-pack-1, doc 06 §7) — the PACK_GENERATED/PACK_BUILD_FAILED lifecycle events
    # key here on the ``evidence_pack`` header id (NOT ``record`` — a pack header is its own table;
    # the pre-seal PACK_BUILD_FAILED has no record id yet). The pack's own EVIDENCE capture is a
    # separate ``record``-typed RECORD_CAPTURED. Added via ``ALTER TYPE audit_object_type ADD
    # VALUE`` in 0025 (the 0019 precedent).
    evidence_pack = "evidence_pack"


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
    # controlled-copy distribution (S7d, doc 04 §11.2) — the per-request export/print intent trail
    # (who/when/which version). Distinct from the cached, deterministic mirror rendition. Added via
    # ``ALTER TYPE … ADD VALUE`` in 0011 (the first additive-enum migration); a fresh ``upgrade
    # head`` rebuilds the type from ``EVENT_TYPE_VALUES`` (below) — so a migrated and a from-scratch
    # DB converge only because these members live here too.
    EXPORTED = "EXPORTED"
    PRINTED = "PRINTED"
    # first-run setup wizard (S8a, doc 08) — the bootstrap-of-trust + finalize trail. Added via
    # ``ALTER TYPE … ADD VALUE`` in 0012 (same additive pattern as 0011 for EXPORTED/PRINTED).
    BOOTSTRAP_CONSUMED = "BOOTSTRAP_CONSUMED"
    ADMIN_BOOTSTRAPPED = "ADMIN_BOOTSTRAPPED"
    ORG_PROFILE_SET = "ORG_PROFILE_SET"
    SETUP_FINALIZED = "SETUP_FINALIZED"
    # storage / WORM-verify gate (S8b, doc 08 §7) — added via ALTER TYPE … ADD VALUE in 0013.
    WORM_VERIFIED = "WORM_VERIFIED"
    # backup config + restore-test gate G-C / AC#5 (S8b2, doc 08 §8) — added via ALTER TYPE …
    # ADD VALUE in 0014. BACKUP_CONFIGURED records the policy; RESTORE_TEST_PASSED/_FAILED are the
    # drill outcome the G-C gate reads (only a PASS satisfies the gate).
    BACKUP_CONFIGURED = "BACKUP_CONFIGURED"
    RESTORE_TEST_PASSED = "RESTORE_TEST_PASSED"
    RESTORE_TEST_FAILED = "RESTORE_TEST_FAILED"
    # auth-config + non-bootstrap-login-proof gate G-D (S8c, doc 08 §9) — added via ALTER TYPE …
    # ADD VALUE in 0015. AUTH_CONFIGURED records the chosen method; AUTH_TEST_LOGIN_OK is the proof
    # the G-D gate reads (only an OK satisfies it); AUTH_TEST_LOGIN_FAILED trails a failed probe.
    AUTH_CONFIGURED = "AUTH_CONFIGURED"
    AUTH_TEST_LOGIN_OK = "AUTH_TEST_LOGIN_OK"
    AUTH_TEST_LOGIN_FAILED = "AUTH_TEST_LOGIN_FAILED"
    # user lifecycle admin (S8d, doc 08 §10/§11) — the invite (pre-create) + enable/disable trail
    # (object_type ``user``). Added via ALTER TYPE … ADD VALUE in 0016 (the 0011-0015 pattern).
    USER_CREATED = "USER_CREATED"
    USER_STATUS_CHANGED = "USER_STATUS_CHANGED"
    # clause IA / clause_mapping (S9, doc 02 §2.1, doc 14 §4) — the audited document↔clause link
    # (object_type ``document``, keyed to the mapped artifact). Added via ALTER TYPE … ADD VALUE in
    # 0017 (the 0011-0016 additive pattern).
    CLAUSE_MAPPED = "CLAUSE_MAPPED"
    CLAUSE_UNMAPPED = "CLAUSE_UNMAPPED"
    # process IA (S9c, doc 02 §3.3, doc 14 §4) — process create/update/state-confirm + edge
    # add/remove (object_type ``process``) and the document↔process link (object_type ``document``,
    # the clause_mapping precedent). Added via ALTER TYPE … ADD VALUE in 0019 (the 0011-17 pattern).
    PROCESS_CREATED = "PROCESS_CREATED"
    PROCESS_UPDATED = "PROCESS_UPDATED"
    PROCESS_STATE_CHANGED = "PROCESS_STATE_CHANGED"
    PROCESS_EDGE_ADDED = "PROCESS_EDGE_ADDED"
    PROCESS_EDGE_REMOVED = "PROCESS_EDGE_REMOVED"
    PROCESS_LINKED = "PROCESS_LINKED"
    PROCESS_UNLINKED = "PROCESS_UNLINKED"
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
    # operator-grade restore + upgrade CLIs (S11, doc 18 §9, doc 12 §8.2 / R37) — the live
    # WORM-aware restore-to-verified-target trail + the pre-backup/health-gated upgrade trail
    # (object_type ``config``). RESTORE_CHECKPOINT_AHEAD is the tamper-suspected flag (the off-host
    # checkpoint is ahead of the restored head); RESTORE_CHECKPOINT_ACK records the audited operator
    # acknowledgement that proceeds past it. Added via ALTER TYPE … ADD VALUE in 0022 (the 0011-0021
    # additive pattern; a from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES).
    RESTORE_STARTED = "RESTORE_STARTED"
    RESTORE_VERIFIED = "RESTORE_VERIFIED"
    RESTORE_FAILED = "RESTORE_FAILED"
    RESTORE_CHECKPOINT_AHEAD = "RESTORE_CHECKPOINT_AHEAD"
    RESTORE_CHECKPOINT_ACK = "RESTORE_CHECKPOINT_ACK"
    UPGRADE_STARTED = "UPGRADE_STARTED"
    UPGRADE_COMPLETED = "UPGRADE_COMPLETED"
    UPGRADE_FAILED = "UPGRADE_FAILED"
    # records capture + evidence-linking + correction (S-rec-1, doc 06 §4/§6) — the immutable
    # capture trail, the correct-don't-change pointer flip, and the evidence-for link annotations
    # (object_type ``record``; AuditObjectType.record already exists → no audit_object_type ALTER).
    # Added via ALTER TYPE event_type ADD VALUE in 0023 (the 0011-0022 additive pattern; a
    # from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live
    # here too).
    RECORD_CAPTURED = "RECORD_CAPTURED"
    RECORD_CORRECTED = "RECORD_CORRECTED"
    RECORD_EVIDENCE_LINKED = "RECORD_EVIDENCE_LINKED"
    RECORD_EVIDENCE_UNLINKED = "RECORD_EVIDENCE_UNLINKED"
    # records retention & disposition (S-rec-2, doc 06 §5, doc 14 §10) — the disposition state
    # machine + the Beat retention sweep + legal-hold + the R27 dual-control WORM-destroy hatch
    # (object_type ``record``). DISPOSITION_DUE is the sweep's ACTIVE→DUE_FOR_REVIEW flip (and the
    # v1 "notify owning org_role" surrogate until doc-10 notifications); DISPOSED is the executed
    # disposition (tombstone); RETENTION_EXTENDED is the DUE_FOR_REVIEW→ACTIVE re-anchor;
    # LEGAL_HOLD_PLACED/RELEASED toggle the preservation freeze; the WORM_DESTROY_* trio is the
    # two-person legal-order hatch; ERASURE_REFUSED logs a destroy blocked by WORM/legal-hold/
    # COMPLIANCE mode (the GDPR refused-with-reason, R27). Added via ALTER TYPE event_type ADD VALUE
    # in 0024 (the 0011-0023 additive pattern; a from-scratch ``upgrade head`` rebuilds the type
    # from EVENT_TYPE_VALUES, so the members live here too).
    RECORD_DISPOSITION_DUE = "RECORD_DISPOSITION_DUE"
    RECORD_DISPOSED = "RECORD_DISPOSED"
    RECORD_RETENTION_EXTENDED = "RECORD_RETENTION_EXTENDED"
    RECORD_LEGAL_HOLD_PLACED = "RECORD_LEGAL_HOLD_PLACED"
    RECORD_LEGAL_HOLD_RELEASED = "RECORD_LEGAL_HOLD_RELEASED"
    RECORD_WORM_DESTROY_REQUESTED = "RECORD_WORM_DESTROY_REQUESTED"
    RECORD_WORM_DESTROY_CANCELLED = "RECORD_WORM_DESTROY_CANCELLED"
    RECORD_WORM_DESTROYED = "RECORD_WORM_DESTROYED"
    RECORD_ERASURE_REFUSED = "RECORD_ERASURE_REFUSED"
    # evidence packs (S-pack-1, doc 06 §7, doc 13 §7.3) — the audited pack lifecycle (object_type
    # ``evidence_pack``). PACK_GENERATED records the sealed pack (scope + content hashes + counts +
    # the preview-vs-seal diff); PACK_BUILD_FAILED trails a failed/abandoned build. Added via ALTER
    # TYPE event_type ADD VALUE in 0025 (the 0011-0024 additive pattern; a from-scratch ``upgrade
    # head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    PACK_GENERATED = "PACK_GENERATED"
    PACK_BUILD_FAILED = "PACK_BUILD_FAILED"


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
