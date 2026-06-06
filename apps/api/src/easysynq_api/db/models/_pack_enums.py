"""Native-PG enum bindings for evidence packs (slice S-pack-1, doc 06 §7, doc 14).

An Evidence Pack (UJ-7) is an on-demand, scope-limited, immutable, self-verifying bundle of records
+ their evidence + a traceability manifest for a clause or process (doc 06 §7). The four enums below
model the pack header + membership:

* ``PackScopeKind`` — the scope a pack is built for. CLAUSE + PROCESS (S-pack-1) + FINDING + CAPA
  (S-aud-capa-pack; a DATE period is an overlay column, not a scope kind). A FINDING/CAPA pack
  resolves the records linked as evidence to the finding / the CAPA's stages AND bundles a
  synthesized, content-hash-sealed *dossier* (the finding's fields + the CAPA's full stage trail +
  the e-signatures) so an auditor can "prove this NC was closed effectively" (doc 06 §7.1). FINDING/
  CAPA were added by ``ALTER TYPE pack_scope_kind ADD VALUE`` (mig 0039); the
  ``EvidenceForTargetType.FINDING``/``CAPA_STAGE`` targets were already live (S-aud-2/S-capa-3).
* ``PackStatus`` — the build lifecycle: DRAFT (preview persisted) → BUILDING (generate enqueued) →
  SEALED (immutable hashed ZIP written + registered as an EVIDENCE Record) | FAILED (build error;
  re-triggerable). SEALED is terminal.
* ``PackItemType`` — a membership row is either a RECORD or one of its PINNED governing
  DOCUMENT_VERSIONs (``record.source_version_id``, the edition in force at capture — doc 06 §7.3).
* ``PackInclusionStatus`` — the R28 honesty classification: INCLUDED, or excluded with the reason
  surfaced (EXCLUDED_PERMISSION = the generator was not entitled to read it; EXCLUDED_ABSENCE = its
  evidence is physically gone). A silently-dropped item is a spec-defined defect (R28), so every
  in-scope candidate gets a row with its classifying status — the exclusion report IS the table.

Created by the Alembic migration; referenced here with ``create_type=False``.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class PackScopeKind(enum.Enum):
    CLAUSE = "CLAUSE"
    PROCESS = "PROCESS"
    FINDING = "FINDING"  # S-aud-capa-pack: scope to one or more audit findings (+ a dossier)
    CAPA = "CAPA"  # S-aud-capa-pack: scope to one or more CAPAs (+ the stage-trail dossier)


class PackStatus(enum.Enum):
    DRAFT = "DRAFT"
    BUILDING = "BUILDING"
    SEALED = "SEALED"
    FAILED = "FAILED"


class PackItemType(enum.Enum):
    RECORD = "RECORD"
    DOCUMENT_VERSION = "DOCUMENT_VERSION"


class PackInclusionStatus(enum.Enum):
    INCLUDED = "INCLUDED"
    EXCLUDED_PERMISSION = "EXCLUDED_PERMISSION"  # not entitled to read (R28 permission class)
    EXCLUDED_ABSENCE = "EXCLUDED_ABSENCE"  # evidence physically gone (R28 genuine-absence)


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


pack_scope_kind_enum = SAEnum(
    PackScopeKind, name="pack_scope_kind", values_callable=_vals, create_type=False
)
pack_status_enum = SAEnum(PackStatus, name="pack_status", values_callable=_vals, create_type=False)
pack_item_type_enum = SAEnum(
    PackItemType, name="pack_item_type", values_callable=_vals, create_type=False
)
pack_inclusion_status_enum = SAEnum(
    PackInclusionStatus, name="pack_inclusion_status", values_callable=_vals, create_type=False
)

# Canonical value tuples re-used by the migration's CREATE TYPE step (the 0023 single-source
# pattern — the hand-authored DDL and the ORM SAEnum bindings can never drift).
PACK_SCOPE_KIND_VALUES = tuple(_vals(PackScopeKind))
PACK_STATUS_VALUES = tuple(_vals(PackStatus))
PACK_ITEM_TYPE_VALUES = tuple(_vals(PackItemType))
PACK_INCLUSION_STATUS_VALUES = tuple(_vals(PackInclusionStatus))
