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
    # retention-policy management (S-rec-4, doc 06 §5.1, doc 15 §8.16) — the RETENTION_POLICY_*
    # CRUD/archive lifecycle keys here on the ``retention_policy`` row id (its own table, not a
    # ``record``). The SoD-6 self-disposition refusal (DISPOSITION_REFUSED_SOD) is a *record* event
    # → it reuses ``record``, NOT this. Added via ``ALTER TYPE audit_object_type ADD VALUE`` in 0028
    # (the 0019/0025 precedent).
    retention_policy = "retention_policy"
    # ingestion runs (S-ing-1, doc 09 §3.2, doc 14 §13) — the IMPORT_RUN_* lifecycle events key here
    # on the ``import_run`` row id (its own transient staging table, not a ``record``/``document``).
    # The run's per-file inventory (import_file) does NOT get its own object type — those rows
    # belong
    # to the run, so item-level events (later slices) key on the run (the
    # process_link-reuses-document
    # precedent). Added via ``ALTER TYPE audit_object_type ADD VALUE`` in 0029 (the 0019/0025/0028
    # precedent).
    import_run = "import_run"
    # the declarative workflow engine (S-wf-engine, doc 10 §2.6) — the per-transition TASK_DECIDED /
    # STAGE_ADVANCED / STAGE_FAILED events key here on the ``workflow_instance`` id (the engine's
    # subject is polymorphic with no FK, so the instance is the per-flow anchor). The DOCUMENT
    # single-stage approval (S5) keeps writing via VaultAuditSink on ``document_version``. Added via
    # ``ALTER TYPE audit_object_type ADD VALUE`` in 0035 (the 0019/0025/0028/0029 precedent).
    workflow_instance = "workflow_instance"
    # the NCR own-table (S-capa-1, doc 14 §9) — NCR_CREATED / NCR_DISPOSITIONED key here on the
    # ``ncr.id`` (an own table, NOT a record subtype, so it cannot reuse ``record``). The CAPA +
    # complaint record subtypes (``capa.id`` / ``complaint.id`` ARE record ids) keep reusing
    # ``record``. Added via ``ALTER TYPE audit_object_type ADD VALUE`` in 0036 (the precedent
    # above).
    ncr = "ncr"
    # the DCR own-table (S-dcr-1, doc 05 §5 / doc 14 §7) — DCR_RAISED / DCR_UPDATED /
    # DCR_TRANSITIONED key here on the ``dcr.id`` (a mutable workflow object, NOT a record subtype
    # per R22, so it cannot reuse ``record``). Added via ``ALTER TYPE audit_object_type ADD VALUE``
    # in 0040 (the ``ncr`` precedent above).
    dcr = "dcr"


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
    # evidence-pack external delivery (S-pack-2, doc 06 §7.4, UJ-7) — the time-boxed Ed25519
    # share-link lifecycle (object_type ``evidence_pack``, the pack header id). PACK_SHARED records
    # a link minted for an auditor (actor = generator; detail = recipient + expiry + digest);
    # PACK_SHARE_REVOKED trails a manual early revoke; PACK_DOWNLOADED is the **guest** access (a
    # system-actor event — actor_id NULL — a bearer-token guest has no app_user; detail = format +
    # recipient + client_ip + digest). Added via ALTER TYPE event_type ADD VALUE in 0026 (the
    # 0011-0025 additive pattern; a from-scratch upgrade rebuilds the type from the ORM values).
    PACK_SHARED = "PACK_SHARED"
    PACK_DOWNLOADED = "PACK_DOWNLOADED"
    PACK_SHARE_REVOKED = "PACK_SHARE_REVOKED"
    # Mode-B structured forms (S-rec-3, doc 06 §4.2). FORM_SCHEMA_SET trails an author setting a
    # form template's working ``field_schema`` (object_type ``document`` — a form template IS a
    # controlled document; the schema is frozen into the version's metadata_snapshot at check-in).
    # CONFIG_UPDATED trails a post-OPERATIONAL org-config change via PATCH /admin/config (object
    # type ``config`` — e.g. the ``capture_pre_release_templates`` toggle). Added via ALTER TYPE
    # event_type ADD VALUE in 0027 (the 0011-0026 additive pattern; a from-scratch ``upgrade head``
    # rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    FORM_SCHEMA_SET = "FORM_SCHEMA_SET"
    CONFIG_UPDATED = "CONFIG_UPDATED"
    # records-family close-out (S-rec-4, doc 06 §5, doc 07 §7, doc 15 §8.16). The RETENTION_POLICY_*
    # trio trails the /retention-policies CRUD + soft-archive (object_type ``retention_policy``).
    # DISPOSITION_REFUSED_SOD trails a self-disposition refused by the SoD-6 creator-not-disposer
    # gate (object_type ``record``) — distinct from RECORD_ERASURE_REFUSED (a preservation refusal:
    # WORM/legal-hold/COMPLIANCE): SoD-6 fires for ALL disposition actions (incl. the non-DESTROY
    # ARCHIVE/TRANSFER, where "erasure refused" would be a misnomer) and is a duty-segregation, not
    # a preservation, refusal. Added via ALTER TYPE ... ADD VALUE in 0028 (the 0011-0027 additive
    # pattern; a from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES, so the
    # members live here too).
    RETENTION_POLICY_CREATED = "RETENTION_POLICY_CREATED"
    RETENTION_POLICY_UPDATED = "RETENTION_POLICY_UPDATED"
    RETENTION_POLICY_ARCHIVED = "RETENTION_POLICY_ARCHIVED"
    DISPOSITION_REFUSED_SOD = "DISPOSITION_REFUSED_SOD"
    # ingestion run + scan/inventory foundation (S-ing-1, doc 09 §3.2/§12.2, doc 14 §13). The run
    # is a
    # first-class audited object: IMPORT_RUN_CREATED (operator starts a run, user actor),
    # IMPORT_RUN_STAGE_CHANGED (each Created→Scanning→Scanned transition, a *system* actor — the
    # scan
    # runs detached in the worker with no HTTP caller), IMPORT_RUN_FAILED (scan error / reaper,
    # system
    # actor), IMPORT_RUN_CANCELLED (operator aborts, user actor). The
    # IMPORT_ITEM_*/_COMPLETED/_PARTIAL
    # events + the import_baseline signature defer to the later review/commit slices. Added via
    # ALTER
    # TYPE event_type ADD VALUE in 0029 (the 0011-0028 additive pattern; a from-scratch ``upgrade
    # head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    IMPORT_RUN_CREATED = "IMPORT_RUN_CREATED"
    IMPORT_RUN_STAGE_CHANGED = "IMPORT_RUN_STAGE_CHANGED"
    IMPORT_RUN_FAILED = "IMPORT_RUN_FAILED"
    IMPORT_RUN_CANCELLED = "IMPORT_RUN_CANCELLED"
    # ingestion human-in-the-loop review (S-ing-4, doc 09 §9/§12.2). One row per Mara accept /
    # correct / merge / split / exclude / defer (a USER actor — review is HTTP-driven, not worker),
    # capturing before→after. Reuses object_type=import_run (object_id=run_id) — decisions are
    # run-scoped events, NOT a new audit_object_type. Added via ALTER TYPE event_type ADD VALUE in
    # 0032 (the 0011-0031 additive pattern; a from-scratch ``upgrade head`` rebuilds the type from
    # EVENT_TYPE_VALUES, so the member lives here too).
    IMPORT_DECISION_RECORDED = "IMPORT_DECISION_RECORDED"
    # ingestion commit (S-ing-5, doc 09 §10.1/§12.2). Per committed item: IMPORT_ITEM_COMMITTED
    # keyed
    # object_type=document|record + object_id=the new vault row + scope_ref=identifier (so the
    # per-doc
    # audit read GET /documents/{id}/audit-events surfaces the import as the doc's creation event,
    # AC#6) — a SYSTEM actor (the detached commit worker has no HTTP caller; the human committer is
    # carried by import_run.committed_by + the import_baseline signature_event). IMPORT_ITEM_FAILED
    # on
    # an isolated per-item failure (object_type=import_run). IMPORT_RUN_COMPLETED /
    # IMPORT_RUN_PARTIAL
    # at the run terminal (object_type=import_run, after carries report_record_id + counts). Added
    # via
    # ALTER TYPE event_type ADD VALUE in 0033 (the 0011-0032 additive pattern; a from-scratch
    # ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    IMPORT_ITEM_COMMITTED = "IMPORT_ITEM_COMMITTED"
    IMPORT_ITEM_FAILED = "IMPORT_ITEM_FAILED"
    IMPORT_RUN_COMPLETED = "IMPORT_RUN_COMPLETED"
    IMPORT_RUN_PARTIAL = "IMPORT_RUN_PARTIAL"
    # ISO internal-audit family (S-aud-1, doc 02 Cl 9.2 / doc 10 §5 / doc 14 §14). Programme + plan
    # are own-table containers → object_type=audit (the reserved AuditObjectType.audit value, no ADD
    # VALUE); the audit RECORD's create + FSM events reuse object_type=record (audit.id is a record
    # id) so GET /documents/{id}/audit-events surfaces them. AUDIT_TRANSITIONED carries before/after
    # state; AUDIT_CLOSED is the gated Closing→Closed terminal. Added via ALTER TYPE event_type ADD
    # VALUE in 0034 (the 0011-0033 additive pattern; a from-scratch ``upgrade head`` rebuilds from
    # EVENT_TYPE_VALUES, so the members live here too).
    AUDIT_PROGRAM_CREATED = "AUDIT_PROGRAM_CREATED"
    AUDIT_PROGRAM_UPDATED = "AUDIT_PROGRAM_UPDATED"
    AUDIT_PLAN_CREATED = "AUDIT_PLAN_CREATED"
    AUDIT_CREATED = "AUDIT_CREATED"
    AUDIT_TRANSITIONED = "AUDIT_TRANSITIONED"
    AUDIT_CLOSED = "AUDIT_CLOSED"
    # the declarative workflow engine (S-wf-engine, doc 10 §2.6). One in-txn audit row per stage
    # transition (object_type=workflow_instance): TASK_DECIDED per task decision; STAGE_ADVANCED
    # when
    # a stage's quorum is MET + the flow advances/completes; STAGE_FAILED on early-fail/reject. The
    # DOCUMENT approval (S5) keeps its VaultAuditSink lifecycle events untouched. Added via ALTER
    # TYPE
    # event_type ADD VALUE in 0035 (the 0011-0034 additive pattern; a from-scratch ``upgrade head``
    # rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    TASK_DECIDED = "TASK_DECIDED"
    STAGE_ADVANCED = "STAGE_ADVANCED"
    STAGE_FAILED = "STAGE_FAILED"
    # the CAPA core + intake family (S-capa-1, doc 02 Cl 10.2, doc 10 §6, doc 14 §9). CAPA +
    # complaint
    # are kind=RECORD subtypes (events key on object_type=record, their ``.id`` IS a record id); NCR
    # is an own table (object_type=ncr). Added via ``ALTER TYPE event_type ADD VALUE`` in 0036 (the
    # additive pattern; a from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES, so
    # the members live here too).
    CAPA_RAISED = "CAPA_RAISED"
    CAPA_TRANSITIONED = "CAPA_TRANSITIONED"
    COMPLAINT_CAPTURED = "COMPLAINT_CAPTURED"
    COMPLAINT_SPAWNED_CAPA = "COMPLAINT_SPAWNED_CAPA"
    NCR_CREATED = "NCR_CREATED"
    NCR_DISPOSITIONED = "NCR_DISPOSITIONED"
    # audit findings + the NC→CAPA auto-link (S-aud-2, doc 10 §5.3, doc 14 §9). audit_finding is a
    # kind=RECORD subtype → events key on object_type=record (finding.id IS a record id); the
    # auto-created CAPA emits its own CAPA_RAISED. AUDIT_FINDING_CORRECTED is the audited supersede
    # pointer-write on the original when a finding is retyped (general retype, any direction). Added
    # via ALTER TYPE event_type ADD VALUE in 0037 (the additive pattern; a from-scratch ``upgrade
    # head`` rebuilds the type from EVENT_TYPE_VALUES, so the members live here too).
    AUDIT_FINDING_CREATED = "AUDIT_FINDING_CREATED"
    AUDIT_FINDING_CORRECTED = "AUDIT_FINDING_CORRECTED"
    # Document Change Requests (S-dcr-1, doc 05 §5 / doc 14 §7). The DCR is a mutable workflow
    # object (object_type=dcr, NOT a record). DCR_RAISED = intake (Open); DCR_UPDATED =
    # edit-while-Open; DCR_TRANSITIONED = any state move (cancel in S-dcr-1; assess/route/approve/
    # implement/close later). Added via ALTER TYPE event_type ADD VALUE in 0040 (the additive
    # pattern; a from-scratch ``upgrade head`` rebuilds the type from EVENT_TYPE_VALUES too).
    DCR_RAISED = "DCR_RAISED"
    DCR_UPDATED = "DCR_UPDATED"
    DCR_TRANSITIONED = "DCR_TRANSITIONED"
    # document↔document links (S-dcr-2, doc 05 §7.1 / doc 14 §5.6) — the where-used reference graph;
    # keyed on object_type=document (the from_document_id, the CLAUSE_MAPPED precedent). Added via
    # ALTER TYPE event_type ADD VALUE in 0041.
    DOCUMENT_LINKED = "DOCUMENT_LINKED"
    DOCUMENT_UNLINKED = "DOCUMENT_UNLINKED"


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
