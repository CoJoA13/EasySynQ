"""Build the synthesized FINDING/CAPA pack dossier (S-aud-capa-pack, doc 06 §7.1/§7.3).

``build_dossier`` reads the scope subjects (findings / CAPAs) + their stage trails + e-signatures +
linked-evidence record identifiers, projects every user through the pure ``domain/packs/dossier``
serializers (the PII boundary — only ``{user_id, display_name}`` ever reaches the ZIP), and returns:

* the per-subject JSON files (``findings/<id>.json`` / ``capas/<id>.json``) for the pack ZIP,
* a ``file_manifest`` (``[{path, sha256}]``) + a ``subjects`` index for ``manifest.json``,
* the ``dossier_digest`` (over the sorted per-file sha256s) that folds into the v2 content hash.

It runs inside the build's single transaction, READ-only; the dossier reflects the CAPA state **at
build time** (the cover's ``generated_at`` anchors it — a concurrent FSM advance is captured as-is,
not a defect: the pack is a frozen build-time snapshot, doc 06 §7.4). Subjects are NOT pack_item
records (a finding/CAPA carries no evidence_blob → no ZIP bytes); their content_hash + narrative
live here, sealed via ``dossier_digest``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models._evidence_enums import EvidenceForTargetType
from ...db.models.app_user import AppUser
from ...db.models.documented_information import DocumentedInformation
from ...db.models.record import Record
from ...domain.packs import dossier as pure
from . import repository as repo


@dataclasses.dataclass(frozen=True, slots=True)
class DossierBuild:
    files: list[tuple[str, bytes]]  # (zip_path, bytes) — written into the pack ZIP
    file_manifest: list[dict[str, Any]]  # [{path, sha256}] — for manifest["dossier"]["files"]
    subjects: list[dict[str, Any]]  # [{kind, id, identifier, content_hash, path}] — manifest index
    digest: str  # the dossier seal (folds into the v2 pack_content_hash)


def _user_ref(user: AppUser | None) -> pure.UserRef | None:
    if user is None:
        return None
    return pure.UserRef(user_id=str(user.id), display_name=user.display_name)


async def _identifier(session: AsyncSession, record_id: uuid.UUID | None) -> str | None:
    if record_id is None:
        return None
    base = await session.get(DocumentedInformation, record_id)
    return base.identifier if base is not None else None


async def _finding_subject(
    session: AsyncSession,
    org_id: uuid.UUID,
    finding_id: uuid.UUID,
    record: Record,
    base: DocumentedInformation,
) -> dict[str, Any]:
    finding = await repo.get_finding(session, finding_id)
    if finding is None:  # pragma: no cover - validated at scope time (shared-PK subtype exists)
        raise RuntimeError(f"finding {finding_id} missing for dossier")
    audit_ref: dict[str, Any] | None = None
    audit = await repo.get_audit(session, finding.audit_id)
    if audit is not None:
        audit_ref = {"id": str(audit.id), "identifier": await _identifier(session, audit.id)}
    linked_capa: dict[str, Any] | None = None
    if finding.auto_capa_id is not None:
        capa = await repo.get_capa(session, finding.auto_capa_id)
        if capa is not None:
            linked_capa = {
                "id": str(capa.id),
                "identifier": await _identifier(session, capa.id),
                "close_state": capa.close_state.value,
            }
    ev = (
        await repo.evidence_records_for_targets(
            session, org_id, EvidenceForTargetType.FINDING, [finding_id]
        )
    ).get(finding_id, [])
    captured_by = await session.get(AppUser, record.captured_by)
    return pure.serialize_finding_dossier(
        finding_id=str(finding.id),
        identifier=base.identifier,
        summary=base.title,
        finding_type=finding.finding_type.value,
        severity=finding.severity.value if finding.severity is not None else None,
        clause_ref=finding.clause_ref,
        process_ref=finding.process_ref,
        captured_at=record.captured_at.isoformat() if record.captured_at else None,
        captured_by=_user_ref(captured_by),
        content_hash=record.content_hash,
        audit=audit_ref,
        correction_of=await _identifier(session, record.correction_of),
        superseded_by_correction=await _identifier(session, record.superseded_by_correction),
        linked_capa=linked_capa,
        evidence_records=ev,
    )


async def _capa_subject(
    session: AsyncSession,
    org_id: uuid.UUID,
    capa_id: uuid.UUID,
    record: Record,
    base: DocumentedInformation,
) -> dict[str, Any]:
    capa = await repo.get_capa(session, capa_id)
    if capa is None:  # pragma: no cover - validated at scope time (shared-PK subtype exists)
        raise RuntimeError(f"capa {capa_id} missing for dossier")
    origin_finding: dict[str, Any] | None = None
    if capa.origin_finding_id is not None:
        finding = await repo.get_finding(session, capa.origin_finding_id)
        if finding is not None:
            fbase = await session.get(DocumentedInformation, finding.id)
            origin_finding = {
                "id": str(finding.id),
                "identifier": fbase.identifier if fbase is not None else None,
                "finding_type": finding.finding_type.value,
                "severity": finding.severity.value if finding.severity is not None else None,
                "summary": fbase.title if fbase is not None else None,
            }

    stages = await repo.list_capa_stages(session, capa_id)
    stage_ids = [s.id for s in stages]
    evidence_by_stage = await repo.evidence_records_for_targets(
        session, org_id, EvidenceForTargetType.CAPA_STAGE, stage_ids
    )
    sig_ids = [s.signed_event_id for s in stages if s.signed_event_id is not None]
    sigs = await repo.signature_events_by_id(session, sig_ids)

    # All user ids referenced by this CAPA (stage creators + signers + the capa capturer).
    user_ids: set[uuid.UUID] = {record.captured_by}
    user_ids.update(s.created_by for s in stages)
    user_ids.update(sig.signer_user_id for sig in sigs.values() if sig.signer_user_id is not None)
    users = await repo.users_by_ids(session, list(user_ids))

    stage_dicts: list[dict[str, Any]] = []
    for s in stages:
        signature: pure.SignatureRef | None = None
        if s.signed_event_id is not None and s.signed_event_id in sigs:
            sig = sigs[s.signed_event_id]
            signer = users.get(sig.signer_user_id) if sig.signer_user_id is not None else None
            signature = pure.SignatureRef(
                meaning=sig.meaning.value,
                signer=_user_ref(signer),
                content_digest=sig.content_digest,
                signed_at=sig.created_at.isoformat() if sig.created_at else None,
            )
        stage_dicts.append(
            pure.serialize_capa_stage(
                stage_id=str(s.id),
                stage=s.stage.value,
                cycle_marker=s.cycle_marker,
                created_at=s.created_at.isoformat() if s.created_at else None,
                created_by=_user_ref(users.get(s.created_by)),
                content_block=s.content_block,
                signature=signature,
                evidence_records=evidence_by_stage.get(s.id, []),
            )
        )

    return pure.serialize_capa_dossier(
        capa_id=str(capa.id),
        identifier=base.identifier,
        title=base.title,
        source=capa.source.value,
        severity=capa.severity.value,
        close_state=capa.close_state.value,
        cycle_marker=capa.cycle_marker,
        process_id=str(capa.process_id) if capa.process_id else None,
        captured_at=record.captured_at.isoformat() if record.captured_at else None,
        captured_by=_user_ref(users.get(record.captured_by)),
        content_hash=record.content_hash,
        origin_finding=origin_finding,
        stages=stage_dicts,
    )


async def build_dossier(
    session: AsyncSession, org_id: uuid.UUID, *, scope_kind: str, scope_ids: list[uuid.UUID]
) -> DossierBuild:
    """Assemble the per-subject dossier files + the sealed digest for a FINDING/CAPA pack."""
    kind = "finding" if scope_kind == "FINDING" else "capa"
    subjects_with_base = {
        rec.id: (rec, base) for rec, base in await repo.get_records_with_base(session, scope_ids)
    }

    files: list[tuple[str, bytes]] = []
    file_manifest: list[dict[str, Any]] = []
    subjects: list[dict[str, Any]] = []
    # Iterate scope_ids (a stable, caller-defined order) so the build is deterministic.
    for sid in scope_ids:
        pair = subjects_with_base.get(sid)
        if pair is None:  # pragma: no cover - validated at scope time
            continue
        record, base = pair
        if kind == "finding":
            obj = await _finding_subject(session, org_id, sid, record, base)
        else:
            obj = await _capa_subject(session, org_id, sid, record, base)
        path = pure.dossier_filename(kind, base.identifier, str(sid))
        data = pure.canonical_dossier_bytes(obj)
        sha = hashlib.sha256(data).hexdigest()
        files.append((path, data))
        file_manifest.append({"path": path, "sha256": sha})
        subjects.append(
            {
                "kind": kind,
                "id": str(sid),
                "identifier": base.identifier,
                "content_hash": record.content_hash,
                "path": path,
            }
        )

    digest = pure.dossier_digest([fm["sha256"] for fm in file_manifest])
    return DossierBuild(files=files, file_manifest=file_manifest, subjects=subjects, digest=digest)
