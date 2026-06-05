"""Stage 6 — Human-in-the-loop review (slice S-ing-4, doc 09 §9/§11.3/§12-13).

Turns a ``Proposed`` import run into a confirmed, commit-ready set. Writes NOTHING to the vault
(commit is S-ing-5) and **never** consumes a numbering sequence. The reviewer's intent is two ways:

* **Dimensional** intent (accept/correct/exclude/defer + the R10 kind-confirm) is recorded ONLY as
  append-only ``import_decision`` rows and **folded at read time** (``fold_file_decisions``) over
  the engine's immutable ``import_classification`` / ``import_proposal_node``. Confirmed ``kind``
  lives in ``decision.after.kind`` — NEVER written back to ``import_classification`` (R10).
* **Structural** intent (merge/split) live-mutates the ``import_dupe_cluster`` /
  ``import_version_family`` rows (targeted ORM edits that preserve every OTHER group's
  ``reconstruct_revision_chain``) then **re-derives** the proposal nodes via
  ``propose.rebuild_proposals``, so the read surfaces + the checklist reflect the reshaping at once.

Every write takes the run ``FOR UPDATE`` (org-404 + ``_REVIEWABLE`` guard + serialization), flips
``Proposed → Reviewing`` on the first decision (race-free under the lock), honours an optional
``Idempotency-Key`` (replay returns the existing decision — the partial-UNIQUE + a pre-insert
SELECT), and emits a USER ``IMPORT_DECISION_RECORDED`` BEFORE commit (AC#6). REVIEWING is lock-free
— see ``service._REVIEWABLE`` for the reaper-trap note.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db.models._audit_enums import EventType
from ...db.models._ingestion_enums import (
    ImportConfidenceBand,
    ImportDecisionAction,
    ImportKind,
    ImportRunStatus,
)
from ...db.models.app_user import AppUser
from ...db.models.import_classification import ImportClassification
from ...db.models.import_decision import ImportDecision
from ...db.models.import_file import ImportFile
from ...db.models.import_proposal_node import ImportProposalNode
from ...db.models.import_run import ImportRun
from ...db.models.import_version_family import ImportVersionFamily
from ...domain.ingestion.normalize import normalize_base_name
from ...domain.ingestion.version_family import FileForPick, order_members
from ...problems import ProblemException
from ..reports.checklist import compute_checklist
from . import repository as repo
from .dedup import _file_for_pick
from .propose import rebuild_proposals
from .service import _REVIEWABLE, emit_import_event

# Disposition each per-file action folds to. accept/correct → in the import; exclude → dropped;
# defer → left undecided-but-acknowledged (commit proceeds without it).
_DISPOSITION = {
    ImportDecisionAction.ACCEPT: "included",
    ImportDecisionAction.CORRECT: "included",
    ImportDecisionAction.EXCLUDE: "excluded",
    ImportDecisionAction.DEFER: "deferred",
}
_FILE_ACTIONS = frozenset(
    {
        ImportDecisionAction.ACCEPT,
        ImportDecisionAction.CORRECT,
        ImportDecisionAction.EXCLUDE,
        ImportDecisionAction.DEFER,
    }
)
_DIMENSION_KEYS = ("kind", "type_code", "clause_numbers", "process_names", "identifier", "owner")
# Dispositions that keep a keep-item "in the import" for conflict/projection purposes (excluded +
# deferred files never collide / never project coverage; doc 09 §9.2/§11.3).
_IN_IMPORT = frozenset({"included", "undecided"})


# ------------------------------------------------------------------------ the effective-state fold


@dataclasses.dataclass(frozen=True, slots=True)
class EffectiveFileState:
    """A keep-item's current state = the engine proposal folded with the file's decisions (newest
    wins per dimension). ``kind`` is ``UNCONFIRMED`` until a decision sets ``after.kind`` (R10)."""

    disposition: str  # included | excluded | deferred | undecided
    kind: str  # DOCUMENT | RECORD | UNCONFIRMED
    identifier: str | None
    # Where the effective identifier came from: 'human' (a reviewer correct/accept set it explicitly
    # —
    # always conflict-checked), the engine's node.identifier_source ('preserved_doc_code' = a real
    # doc-code that CAN collide; 'suggested_default' = the "{type}-<new>" sentinel that CANNOT), or
    # None.
    identifier_source: str | None
    type_code: str | None
    clause_numbers: list[str]
    process_names: list[str] | None
    owner: str | None
    decided: bool
    last_action: str | None

    @property
    def commit_ready(self) -> bool:
        """R10: an item commits only when it is in the import AND its kind is human-confirmed."""
        return self.disposition == "included" and self.kind in ("DOCUMENT", "RECORD")

    @property
    def identifier_collidable(self) -> bool:
        """True iff the effective identifier is a concrete code that can truly collide (a preserved
        doc-code OR a human-set identifier) — the "{type}-<new>" sentinel / a null identifier never
        collides and is allocated fresh at commit (S-ing-5 finding 5)."""
        return self.identifier is not None and self.identifier_source in (
            "human",
            "preserved_doc_code",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "kind": self.kind,
            "identifier": self.identifier,
            "identifier_source": self.identifier_source,
            "type_code": self.type_code,
            "clause_numbers": self.clause_numbers,
            "process_names": self.process_names,
            "owner": self.owner,
            "decided": self.decided,
            "last_action": self.last_action,
            "commit_ready": self.commit_ready,
        }


def fold_file_decisions(
    decisions_newest_first: list[ImportDecision],
    node: ImportProposalNode | None,
    classification: ImportClassification | None,
) -> EffectiveFileState:
    """Pure fold — the SINGLE source of a file's effective state (the checklist, the file-detail
    read, and S-ing-5's commit gate all use it). ``decisions_newest_first`` must be ONLY this file's
    file-targeted decisions (merge/split are cluster-targeted, never folded per file)."""

    def latest(key: str) -> Any:
        for d in decisions_newest_first:
            if d.after and d.after.get(key) is not None:
                return d.after[key]
        return None

    last = decisions_newest_first[0].action if decisions_newest_first else None
    disposition = _DISPOSITION.get(last, "undecided") if last is not None else "undecided"
    kind = latest("kind") or "UNCONFIRMED"
    human_identifier = latest("identifier")
    identifier_source: str | None
    if human_identifier is not None:
        identifier = human_identifier
        identifier_source = "human"
    elif node is not None:
        identifier = node.proposed_identifier
        identifier_source = node.identifier_source
    else:
        identifier = None
        identifier_source = None
    type_code = latest("type_code")
    if type_code is None and classification is not None:
        type_code = classification.type_code
    clauses = latest("clause_numbers")
    if clauses is None:
        clauses = list(classification.clause_numbers) if classification is not None else []
    process = latest("process_names")
    if process is None and classification is not None and classification.process_names:
        process = list(classification.process_names)
    owner = latest("owner")
    if owner is None and node is not None:
        owner = node.proposed_owner
    return EffectiveFileState(
        disposition=disposition,
        kind=str(kind),
        identifier=identifier,
        identifier_source=identifier_source,
        type_code=type_code,
        clause_numbers=list(clauses),
        process_names=process,
        owner=owner,
        decided=bool(decisions_newest_first),
        last_action=last.value if last is not None else None,
    )


# --------------------------------------------------------------------------- guards & helpers


async def _load_reviewable(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID, *, for_update: bool
) -> ImportRun:
    run = await repo.get_run(session, run_id, for_update=for_update)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    if run.status not in _REVIEWABLE:
        raise ProblemException(
            status=409,
            code="conflict",
            title="Import run is not in a reviewable state",
            detail=f"status={run.status.value}; review requires Proposed or Reviewing",
        )
    return run


def _enter_reviewing(session: AsyncSession, run: ImportRun, caller: AppUser) -> None:
    """Flip Proposed→Reviewing on the first decision (race-free under the run's FOR UPDATE)."""
    if run.status is ImportRunStatus.PROPOSED:
        run.status = ImportRunStatus.REVIEWING
        emit_import_event(
            session,
            caller,
            EventType.IMPORT_RUN_STAGE_CHANGED,
            run.id,
            before={"status": "Proposed"},
            after={"status": "Reviewing"},
        )


def _coerce_action(raw: str) -> ImportDecisionAction:
    try:
        return ImportDecisionAction(raw)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"unknown decision action: {raw}"
        ) from exc


def _validate_after(action: ImportDecisionAction, after: dict[str, Any] | None) -> dict[str, Any]:
    """Light validation of the dimensional payload. ``kind`` (if present) must be a real confirmed
    kind (DOCUMENT|RECORD — never UNKNOWN: confirming UNKNOWN is not a confirmation, R10). List
    dimensions must be lists of strings."""
    clean: dict[str, Any] = {}
    after = after or {}
    unknown = set(after) - set(_DIMENSION_KEYS)
    if unknown:
        raise ProblemException(
            status=422,
            code="validation_error",
            title=f"unknown decision dimension(s): {sorted(unknown)}",
        )
    kind = after.get("kind")
    if kind is not None:
        if kind not in (ImportKind.DOCUMENT.value, ImportKind.RECORD.value):
            raise ProblemException(
                status=422,
                code="validation_error",
                title="kind must be confirmed as DOCUMENT or RECORD (R10)",
            )
        clean["kind"] = kind
    for key in ("type_code", "identifier", "owner"):
        val = after.get(key)
        if val is not None:
            if not isinstance(val, str):
                raise ProblemException(
                    status=422, code="validation_error", title=f"{key} must be a string"
                )
            clean[key] = val
    for key in ("clause_numbers", "process_names"):
        val = after.get(key)
        if val is not None:
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                raise ProblemException(
                    status=422, code="validation_error", title=f"{key} must be a list of strings"
                )
            clean[key] = val
    if action is ImportDecisionAction.CORRECT and not clean:
        raise ProblemException(
            status=422, code="validation_error", title="a correct decision must change ≥1 dimension"
        )
    return clean


async def _replay(
    session: AsyncSession, run_id: uuid.UUID, idem_key: str | None
) -> ImportDecision | None:
    """Idempotency-Key replay: the existing decision for this key, or None to proceed."""
    if not idem_key:
        return None
    return await repo.find_decision_by_idem(session, run_id, idem_key)


# ----------------------------------------------------------------------- per-file + bulk decisions


async def record_file_decision(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    file_id: uuid.UUID,
    *,
    action: str,
    after: dict[str, Any] | None,
    reason: str | None,
    idem_key: str | None = None,
) -> dict[str, Any]:
    """Record one dimensional decision (accept/correct/exclude/defer). Merge/split are rejected here
    (they have dedicated endpoints — structural ≠ dimensional)."""
    run = await _load_reviewable(session, caller, run_id, for_update=True)
    act = _coerce_action(action)
    if act not in _FILE_ACTIONS:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="merge/split are structural — use the /merge or /split endpoint",
        )
    replay = await _replay(session, run_id, idem_key)
    if replay is not None:
        replay_id = replay.id  # capture BEFORE rollback (loaded; rollback would expire → lazy I/O)
        await session.rollback()
        return {
            "run_id": str(run_id),
            "file_id": str(file_id),
            "replayed": True,
            "decision_id": str(replay_id),
        }

    f = await repo.get_file(session, run_id, file_id)
    if f is None:
        raise ProblemException(status=404, code="not_found", title="Import file not found")
    if not f.included_candidate:
        # An excluded/quarantined scan file is not a candidate — it has no proposal node and can
        # never commit, so a decision on it is meaningless (pull-from-quarantine is deferred).
        raise ProblemException(
            status=422, code="validation_error", title="file is not an included candidate"
        )
    clean_after = _validate_after(act, after)
    _, _, node = await repo.get_file_membership(session, run_id, file_id)
    cls = await _classification_for(session, run, file_id)
    existing = list(await repo.decisions_for_file(session, run_id, file_id))
    before = fold_file_decisions(existing, node, cls).as_dict()
    payload = dict(clean_after)
    if reason:
        payload["reason"] = reason
    await repo.insert_decision(
        session,
        org_id=run.org_id,
        run_id=run_id,
        action=act,
        decided_by=caller.id,
        file_id=file_id,
        target_kind="file",
        before=before,
        after=payload,
        idempotency_key=idem_key,
    )
    _enter_reviewing(session, run, caller)
    emit_import_event(
        session,
        caller,
        EventType.IMPORT_DECISION_RECORDED,
        run_id,
        before=before,
        after={"action": act.value, "file_id": str(file_id), **payload},
    )
    await session.commit()
    return await _file_decision_result(session, run, file_id)


async def record_bulk_decisions(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    *,
    action: str,
    file_ids: list[uuid.UUID] | None,
    selector: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str | None,
    idem_key: str | None = None,
) -> dict[str, Any]:
    """Apply ONE dimensional action across an explicit ``file_ids`` list OR a ``selector`` filter
    (kind/band/disposition over the existing classification/scan columns). Bulk kind-confirm
    (``after.kind``) is the explicit human act (R10) — never threshold-auto. One decision row per
    file; one summary ``IMPORT_DECISION_RECORDED`` event."""
    settings = get_settings()
    run = await _load_reviewable(session, caller, run_id, for_update=True)
    act = _coerce_action(action)
    if act not in _FILE_ACTIONS:
        raise ProblemException(
            status=422,
            code="validation_error",
            title="merge/split are structural — use the /merge or /split endpoint",
        )
    clean_after = _validate_after(act, after)

    targets = await _resolve_selection(session, run, file_ids, selector, settings)
    if not targets:
        raise ProblemException(
            status=422, code="validation_error", title="bulk decision selected no files"
        )
    if len(targets) > settings.import_bulk_decision_max:
        raise ProblemException(
            status=422,
            code="validation_error",
            title=f"bulk selection {len(targets)} exceeds max {settings.import_bulk_decision_max}",
        )

    # Idempotency for the whole bulk: the key (if any) is stamped on the FIRST inserted row; a
    # replay finds it and no-ops (the bulk commits atomically, so a crash-before-commit leaves no
    # re-applies correctly).
    if (replay := await _replay(session, run_id, idem_key)) is not None:
        replay_id = replay.id  # capture BEFORE rollback (else the expired attr triggers lazy I/O)
        await session.rollback()
        return {
            "run_id": str(run_id),
            "action": act.value,
            "applied": 0,
            "replayed": True,
            "decision_id": str(replay_id),
        }

    payload = dict(clean_after)
    if reason:
        payload["reason"] = reason
    targets_sorted = sorted(targets, key=str)
    for n, fid in enumerate(targets_sorted):
        await repo.insert_decision(
            session,
            org_id=run.org_id,
            run_id=run_id,
            action=act,
            decided_by=caller.id,
            file_id=fid,
            target_kind="file",
            before=None,  # bulk omits the per-file before-fold (the summary event carries the set)
            after=payload,
            idempotency_key=idem_key if (idem_key and n == 0) else None,
        )
    _enter_reviewing(session, run, caller)
    emit_import_event(
        session,
        caller,
        EventType.IMPORT_DECISION_RECORDED,
        run_id,
        after={
            "action": act.value,
            "bulk": True,
            "count": len(targets_sorted),
            "file_ids": [str(i) for i in targets_sorted],
            **payload,
        },
    )
    await session.commit()
    return {"run_id": str(run_id), "action": act.value, "applied": len(targets_sorted)}


async def _resolve_selection(
    session: AsyncSession,
    run: ImportRun,
    file_ids: list[uuid.UUID] | None,
    selector: dict[str, Any] | None,
    settings: Any,
) -> list[uuid.UUID]:
    """Bulk selection: explicit ``file_ids`` (validated ∈ run) OR a ``selector`` over the existing
    ``list_files_with_classification`` dimensions (disposition/kind/band) — NOT a review_status
    push-down (that is a read-only display filter)."""
    if file_ids:
        rows = []
        for fid in file_ids:
            f = await repo.get_file(session, run.id, fid)
            if f is None:
                raise ProblemException(
                    status=404, code="not_found", title=f"Import file not in run: {fid}"
                )
            if not f.included_candidate:
                raise ProblemException(
                    status=422,
                    code="validation_error",
                    title=f"file is not an included candidate: {fid}",
                )
            rows.append(f.id)
        return rows
    if selector is not None:
        kind = selector.get("kind")
        band = selector.get("band")
        disposition = selector.get("disposition")
        files = await repo.list_files_with_classification(
            session,
            run.id,
            classifier_version=run.classifier_version,
            disposition=disposition,
            kind=ImportKind(kind) if kind else None,
            band=_coerce_band(band),
            limit=settings.import_bulk_decision_max,
            offset=0,
        )
        return [f.id for f, _c in files]
    raise ProblemException(
        status=422, code="validation_error", title="bulk decision needs file_ids or a selector"
    )


def _coerce_band(band: str | None) -> Any:
    if not band:
        return None
    try:
        return ImportConfidenceBand(band)
    except ValueError as exc:
        raise ProblemException(
            status=422, code="validation_error", title=f"unknown band: {band}"
        ) from exc


# ----------------------------------------------------------------------- structural: merge / split


async def merge_files(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    *,
    file_ids: list[uuid.UUID],
    effective_file_id: uuid.UUID | None = None,
    reconstruct_revision_chain: bool | None = None,
    reason: str | None = None,
    idem_key: str | None = None,
) -> dict[str, Any]:
    """Combine ≥2 files into ONE version family (force a revision chain). Consolidates any families
    the files already touch (deleting the drained sources), removes them from any dupe-cluster, sets
    the family's ordered members + effective + ``reconstruct_revision_chain``, **preserves every
    OTHER group's reconstruct flag** (targeted ORM edits, never a full replace), then re-derives the
    proposal nodes. effective_file_id (if given) is the §7.2 human override, else the total-order
    pick.
    """
    run = await _load_reviewable(session, caller, run_id, for_update=True)
    if len({*file_ids}) < 2:
        raise ProblemException(
            status=422, code="validation_error", title="merge needs ≥2 distinct files"
        )
    if (replay := await _replay(session, run_id, idem_key)) is not None:
        replay_id = replay.id  # capture BEFORE rollback (else the expired attr triggers lazy I/O)
        await session.rollback()
        return {"run_id": str(run_id), "replayed": True, "decision_id": str(replay_id)}

    ctx = await _file_ctx(session, run, file_ids)  # validates each ∈ run + included; for the pick
    # effective_file_id is validated AFTER consolidation (below) — it may legitimately be a member
    # of a touched family, not one of the explicit file_ids.

    clusters = list(await repo.list_dupe_clusters(session, run_id))
    families = list(await repo.list_version_families(session, run_id))
    selected = set(file_ids)

    # Consolidate any families the selected files already belong to.
    touched = [f for f in families if selected & set(f.ordered_member_file_ids)]
    members: set[uuid.UUID] = set(file_ids)
    reconstruct_seed = reconstruct_revision_chain
    before_groups: list[dict[str, Any]] = []
    for fam in touched:
        members.update(fam.ordered_member_file_ids)
        if reconstruct_revision_chain is None and fam.reconstruct_revision_chain:
            reconstruct_seed = True  # OR-preserve across a consolidation
        before_groups.append(
            {
                "kind": "version_family",
                "family_id": str(fam.id),
                "members": [str(i) for i in fam.ordered_member_file_ids],
                "reconstruct_revision_chain": fam.reconstruct_revision_chain,
            }
        )
    # Members may include files outside the explicit selection (existing family members) — load
    # their context for the canonical pick too.
    await _ensure_ctx(session, run, members, ctx)

    # Pull the merged files out of any dupe-cluster (they're versions now, not duplicates).
    for cl in clusters:
        kept = [m for m in cl.member_file_ids if m not in members]
        if len(kept) == len(cl.member_file_ids):
            continue
        before_groups.append(
            {
                "kind": "dupe_cluster",
                "cluster_id": str(cl.id),
                "members": [str(i) for i in cl.member_file_ids],
            }
        )
        if len(kept) < 2:
            await session.delete(cl)  # <2 → the survivor becomes a standalone keep-item
        else:
            # The kept members may be files OUTSIDE the merge set (a cluster can hold unrelated
            # files) — load their context before recomputing the canonical, else ctx[m] KeyErrors.
            await _ensure_ctx(session, run, set(kept), ctx, require_included=False)
            cl.member_file_ids = kept
            cl.canonical_file_id = order_members([ctx[m] for m in kept])[0].file_id
    for fam in touched:
        await session.delete(fam)  # replaced by the combined family below
    await session.flush()

    ordered = [m.file_id for m in order_members([ctx[m] for m in members])]
    if effective_file_id is not None and effective_file_id not in set(ordered):
        raise ProblemException(
            status=422,
            code="validation_error",
            title="effective_file_id must be one of the merged files (incl. consolidated members)",
        )
    effective = effective_file_id if effective_file_id is not None else ordered[0]
    reconstruct = bool(reconstruct_seed) if reconstruct_seed is not None else False
    fam_row = ImportVersionFamily(
        org_id=run.org_id,
        run_id=run_id,
        family_key=f"manual:{uuid.uuid4()}",
        base_name=normalize_base_name(ctx[ordered[0]].filename),
        doc_code=None,
        ordered_member_file_ids=ordered,
        effective_file_id=effective,
        reconstruct_revision_chain=reconstruct,
        evidence={"merged_by_review": True},
    )
    session.add(fam_row)
    await session.flush()

    await rebuild_proposals(session, run_id, org_id=run.org_id, version=run.classifier_version)
    await _refresh_counts(session, run)
    _enter_reviewing(session, run, caller)
    after = {
        "action": "merge",
        "family_id": str(fam_row.id),
        "members": [str(i) for i in ordered],
        "effective_file_id": str(effective),
        "reconstruct_revision_chain": reconstruct,
    }
    if reason:
        after["reason"] = reason
    await repo.insert_decision(
        session,
        org_id=run.org_id,
        run_id=run_id,
        action=ImportDecisionAction.MERGE,
        decided_by=caller.id,
        cluster_id=fam_row.id,
        target_kind="version_family",
        before={"groups": before_groups},
        after=after,
        idempotency_key=idem_key,
    )
    emit_import_event(
        session,
        caller,
        EventType.IMPORT_DECISION_RECORDED,
        run_id,
        before={"groups": before_groups},
        after=after,
    )
    await session.commit()
    return {"run_id": str(run_id), "family_id": str(fam_row.id), "members": after["members"]}


async def split_cluster(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    separate_file_ids: list[uuid.UUID],
    reason: str | None = None,
    idem_key: str | None = None,
) -> dict[str, Any]:
    """Break members out of a dupe-cluster / version-family. The separated files (and a survivor
    when the group drops <2 members → the group is DELETED) become standalone keep-items."""
    run = await _load_reviewable(session, caller, run_id, for_update=True)
    if target_kind not in ("dupe_cluster", "version_family"):
        raise ProblemException(
            status=422,
            code="validation_error",
            title="target_kind must be dupe_cluster or version_family",
        )
    if not separate_file_ids:
        raise ProblemException(
            status=422, code="validation_error", title="split needs ≥1 file to separate"
        )
    if (replay := await _replay(session, run_id, idem_key)) is not None:
        replay_id = replay.id  # capture BEFORE rollback (else the expired attr triggers lazy I/O)
        await session.rollback()
        return {"run_id": str(run_id), "replayed": True, "decision_id": str(replay_id)}

    sep = set(separate_file_ids)
    if target_kind == "dupe_cluster":
        cluster = await repo.get_dupe_cluster(session, run_id, target_id)
        if cluster is None:
            raise ProblemException(status=404, code="not_found", title="Dupe cluster not found")
        members = list(cluster.member_file_ids)
        before = {
            "kind": "dupe_cluster",
            "cluster_id": str(target_id),
            "members": [str(i) for i in members],
        }
        if not sep <= set(members):
            raise ProblemException(
                status=422,
                code="validation_error",
                title="separate_file_ids not all in the cluster",
            )
        remaining = [m for m in members if m not in sep]
        if len(remaining) < 2:
            await session.delete(cluster)
        else:
            ctx = await _file_ctx(session, run, remaining)
            cluster.member_file_ids = remaining
            cluster.canonical_file_id = order_members([ctx[m] for m in remaining])[0].file_id
    else:
        family = await repo.get_version_family(session, run_id, target_id)
        if family is None:
            raise ProblemException(status=404, code="not_found", title="Version family not found")
        members = list(family.ordered_member_file_ids)
        before = {
            "kind": "version_family",
            "family_id": str(target_id),
            "members": [str(i) for i in members],
        }
        if not sep <= set(members):
            raise ProblemException(
                status=422, code="validation_error", title="separate_file_ids not all in the family"
            )
        remaining = [m for m in members if m not in sep]
        if len(remaining) < 2:
            await session.delete(family)
        else:
            ctx = await _file_ctx(session, run, remaining)
            ordered = [m.file_id for m in order_members([ctx[m] for m in remaining])]
            family.ordered_member_file_ids = ordered
            family.effective_file_id = ordered[0]
    await session.flush()

    await rebuild_proposals(session, run_id, org_id=run.org_id, version=run.classifier_version)
    await _refresh_counts(session, run)
    _enter_reviewing(session, run, caller)
    after = {
        "action": "split",
        "target_kind": target_kind,
        "target_id": str(target_id),
        "separated": [str(i) for i in sorted(sep, key=str)],
    }
    if reason:
        after["reason"] = reason
    await repo.insert_decision(
        session,
        org_id=run.org_id,
        run_id=run_id,
        action=ImportDecisionAction.SPLIT,
        decided_by=caller.id,
        cluster_id=target_id,
        target_kind=target_kind,
        before=before,
        after=after,
        idempotency_key=idem_key,
    )
    emit_import_event(
        session, caller, EventType.IMPORT_DECISION_RECORDED, run_id, before=before, after=after
    )
    await session.commit()
    return {"run_id": str(run_id), "target_id": str(target_id), "separated": after["separated"]}


# ----------------------------------------------------------------------- reads: checklist + detail


async def list_decisions(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID
) -> tuple[ImportRun, list[dict[str, Any]]]:
    run = await repo.get_run(session, run_id)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    rows = await repo.list_decisions(session, run_id)
    return run, [_decision_view(d) for d in rows]


async def list_files_review(
    session: AsyncSession,
    caller: AppUser,
    run_id: uuid.UUID,
    *,
    disposition: str | None,
    kind: ImportKind | None,
    band: Any | None,
    review_status: str | None,
    limit: int,
    offset: int,
) -> tuple[ImportRun, list[tuple[ImportFile, ImportClassification | None, dict[str, Any]]]]:
    """The file inventory annotated with each file's folded ``review`` effective state. The
    ``disposition``/``kind``/``band`` filters push down to the DB (the S-ing-2 path).
    ``review_status`` is a DERIVED fold value (included/excluded/deferred/undecided): when set, the
    matching rows are loaded in full (bounded by ``import_bulk_decision_max``), folded, filtered,
    then paginated in Python; else the DB pagination is used and only the page is folded."""
    settings = get_settings()
    run = await repo.get_run(session, run_id)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")

    fetch_all = review_status is not None
    rows = await repo.list_files_with_classification(
        session,
        run_id,
        classifier_version=run.classifier_version,
        disposition=disposition,
        kind=kind,
        band=band,
        limit=settings.import_bulk_decision_max if fetch_all else min(limit, 200),
        offset=0 if fetch_all else max(offset, 0),
    )
    nodes = {n.file_id: n for n in await repo.list_proposal_nodes(session, run_id)}
    decs: dict[uuid.UUID, list[ImportDecision]] = {}
    for d in await repo.list_decisions(session, run_id):
        if d.file_id is not None:
            decs.setdefault(d.file_id, []).append(d)

    out: list[tuple[ImportFile, ImportClassification | None, dict[str, Any]]] = []
    for f, c in rows:
        state = fold_file_decisions(decs.get(f.id, []), nodes.get(f.id), c)
        if review_status is not None and state.disposition != review_status:
            continue
        out.append((f, c, state.as_dict()))
    if fetch_all:
        out = out[max(offset, 0) : max(offset, 0) + min(limit, 200)]
    return run, out


async def get_file_review(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID, file_id: uuid.UUID
) -> dict[str, Any]:
    run = await repo.get_run(session, run_id)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")
    f = await repo.get_file(session, run_id, file_id)
    if f is None:
        raise ProblemException(status=404, code="not_found", title="Import file not found")
    _, _, node = await repo.get_file_membership(session, run_id, file_id)
    cls = await _classification_for(session, run, file_id)
    decisions = list(await repo.decisions_for_file(session, run_id, file_id))
    state = fold_file_decisions(decisions, node, cls)
    return {
        "effective": state.as_dict(),
        "decision_history": [_decision_view(d) for d in decisions],
    }


async def compute_review_checklist(
    session: AsyncSession, caller: AppUser, run_id: uuid.UUID
) -> dict[str, Any]:
    """The §9.3 pre-commit checklist: blocking conflicts (over the EFFECTIVE folded identifiers) +
    the non-blocking ★-coverage projection + advisory counts + folded review stats."""
    settings = get_settings()
    run = await repo.get_run(session, run_id)
    if run is None or run.org_id != caller.org_id:
        raise ProblemException(status=404, code="not_found", title="Import run not found")

    nodes = await repo.list_proposal_nodes(session, run_id)
    rows = await repo.included_files_with_context(session, run_id, run.classifier_version)
    cls_by_file = {f.id: c for f, _e, c in rows}
    all_decisions = await repo.list_decisions(session, run_id)
    decisions_by_file: dict[uuid.UUID, list[ImportDecision]] = {}
    for d in all_decisions:  # newest-first already (list_decisions ordering)
        if d.file_id is not None:
            decisions_by_file.setdefault(d.file_id, []).append(d)

    # Fold each keep-item (= each proposal node).
    states: dict[uuid.UUID, EffectiveFileState] = {}
    for node in nodes:
        states[node.file_id] = fold_file_decisions(
            decisions_by_file.get(node.file_id, []), node, cls_by_file.get(node.file_id)
        )

    # In-import keep-items (excluded/deferred are out for conflicts + projection).
    in_import = {fid: st for fid, st in states.items() if st.disposition in _IN_IMPORT}

    # Blocking: duplicate identifier within the import — only over COLLIDABLE identifiers (a
    # preserved
    # doc-code or a human-set one). The "{type}-<new>" sentinel / a null identifier is allocated
    # fresh
    # at commit and never collides, so N sentinels of the same type must NOT false-block (finding
    # 5).
    by_ident: dict[str, list[str]] = {}
    for fid, st in in_import.items():
        if st.identifier_collidable and st.identifier is not None:
            by_ident.setdefault(st.identifier, []).append(str(fid))
    blocking: list[dict[str, Any]] = [
        {
            "type": "duplicate_identifier_within_import",
            "identifier": ident,
            "file_ids": fids,
            "resolved": False,
        }
        for ident, fids in sorted(by_ident.items())
        if len(fids) > 1
    ]

    # Blocking: collides with an existing vault document (over COLLIDABLE EFFECTIVE identifiers).
    idents = [
        st.identifier
        for st in in_import.values()
        if st.identifier_collidable and st.identifier is not None
    ]
    vault_hits = await repo.vault_identifier_collisions(session, run.org_id, idents)
    for fid, st in sorted(in_import.items(), key=lambda kv: str(kv[0])):
        if st.identifier_collidable and st.identifier and st.identifier in vault_hits:
            blocking.append(
                {
                    "type": "collides_with_vault_doc",
                    "identifier": st.identifier,
                    "file_id": str(fid),
                    "documented_information_id": vault_hits[st.identifier],
                    "resolved": False,
                }
            )

    # Blocking: a singleton document-type (Quality Policy / Scope Statement) that already has an
    # Effective instance in the vault, or appears more than once among the in-import DOCUMENT items
    # —
    # R25 single-Effective-per-type guard would otherwise surface as a per-item 23505 at commit
    # (finding 23). Resolve only the distinct type_codes of confirmed-DOCUMENT keep-items.
    doc_states = [
        (fid, st) for fid, st in in_import.items() if st.kind == "DOCUMENT" and st.type_code
    ]
    codes = {st.type_code for _fid, st in doc_states if st.type_code}
    dt_by_code = await repo.get_document_types_by_codes(session, run.org_id, codes)
    singleton_type_ids = {dt.id for dt in dt_by_code.values() if dt.is_singleton}
    existing_singletons = await repo.vault_effective_singleton_type_ids(
        session, run.org_id, singleton_type_ids
    )
    by_singleton_type: dict[uuid.UUID, list[str]] = {}
    for fid, st in doc_states:
        dt = dt_by_code.get(st.type_code) if st.type_code else None
        if dt is not None and dt.is_singleton:
            by_singleton_type.setdefault(dt.id, []).append(str(fid))
    for type_id, fids in sorted(by_singleton_type.items(), key=lambda kv: str(kv[0])):
        if type_id in existing_singletons or len(fids) > 1:
            blocking.append(
                {
                    "type": "singleton_type_already_effective",
                    "document_type_id": str(type_id),
                    "file_ids": sorted(fids),
                    "existing_in_vault": type_id in existing_singletons,
                    "resolved": False,
                }
            )

    # Blocking: ambiguous keep-items with NO human decision, above the configurable threshold.
    ambiguous_unresolved = [
        str(fid)
        for fid, st in in_import.items()
        if st.disposition == "undecided" and (c := cls_by_file.get(fid)) is not None and c.ambiguous
    ]
    threshold = settings.import_review_ambiguous_threshold
    if len(ambiguous_unresolved) > threshold:
        blocking.append(
            {
                "type": "ambiguous_unresolved",
                "count": len(ambiguous_unresolved),
                "threshold": threshold,
                "file_ids": sorted(ambiguous_unresolved),
                "resolved": False,
            }
        )

    # Advisory: ★-coverage projection over the confirmed-DOCUMENT keep-items' effective clauses.
    projected_clauses: set[str] = set()
    for st in in_import.values():
        if st.kind == "DOCUMENT":
            projected_clauses.update(st.clause_numbers)
    star = await compute_checklist(session, run.org_id, projected_clause_numbers=projected_clauses)

    # Advisory counts + folded review stats.
    unknown_low = sum(
        1
        for fid, st in in_import.items()
        if (c := cls_by_file.get(fid)) is not None
        and (c.kind == ImportKind.UNKNOWN or c.band == ImportConfidenceBand.LOW)
    )
    kind_unconfirmed = sum(
        1 for st in states.values() if st.disposition == "included" and st.kind == "UNCONFIRMED"
    )
    review = {
        "keep_items": len(states),
        "decided": sum(1 for st in states.values() if st.decided),
        "accepted": sum(1 for st in states.values() if st.last_action == "accept"),
        "corrected": sum(1 for st in states.values() if st.last_action == "correct"),
        "excluded": sum(1 for st in states.values() if st.disposition == "excluded"),
        "deferred": sum(1 for st in states.values() if st.disposition == "deferred"),
        "undecided": sum(1 for st in states.values() if st.disposition == "undecided"),
        "kind_confirmed": sum(1 for st in states.values() if st.kind in ("DOCUMENT", "RECORD")),
        "commit_ready": sum(1 for st in states.values() if st.commit_ready),
    }

    return {
        "run_id": str(run_id),
        "status": run.status.value,
        "ready": not blocking,
        "blocking": blocking,
        "advisory": {
            "star_coverage": star,
            "unknown_low": unknown_low,
            "kind_unconfirmed": kind_unconfirmed,
        },
        "review": review,
    }


# --------------------------------------------------------------------------- internal helpers


async def _classification_for(
    session: AsyncSession, run: ImportRun, file_id: uuid.UUID
) -> ImportClassification | None:
    detail = await repo.get_file_detail(
        session, run.id, file_id, classifier_version=run.classifier_version
    )
    return detail[2] if detail is not None else None


async def _file_ctx(
    session: AsyncSession, run: ImportRun, file_ids: list[uuid.UUID]
) -> dict[uuid.UUID, FileForPick]:
    """``{file_id: FileForPick}`` for the canonical-pick, validating each file ∈ run + included."""
    ctx: dict[uuid.UUID, FileForPick] = {}
    await _ensure_ctx(session, run, set(file_ids), ctx, require_included=True)
    return ctx


async def _ensure_ctx(
    session: AsyncSession,
    run: ImportRun,
    file_ids: set[uuid.UUID],
    ctx: dict[uuid.UUID, FileForPick],
    *,
    require_included: bool = False,
) -> None:
    for fid in file_ids:
        if fid in ctx:
            continue
        detail = await repo.get_file_detail(
            session, run.id, fid, classifier_version=run.classifier_version
        )
        if detail is None:
            raise ProblemException(
                status=404, code="not_found", title=f"Import file not in run: {fid}"
            )
        f, ext, _c = detail
        if require_included and not f.included_candidate:
            raise ProblemException(
                status=422,
                code="validation_error",
                title=f"file is not an included candidate: {fid}",
            )
        ctx[fid] = _file_for_pick(f, ext)


async def _refresh_counts(session: AsyncSession, run: ImportRun) -> None:
    dedup_counts = await repo.compute_dedup_counts(session, run.id)
    proposal_counts = await repo.compute_proposal_counts(session, run.id)
    run.counts = {**(run.counts or {}), **dedup_counts, **proposal_counts}


async def _file_decision_result(
    session: AsyncSession,
    run: ImportRun,
    file_id: uuid.UUID,
    *,
    replayed: ImportDecision | None = None,
) -> dict[str, Any]:
    review = await get_file_review_state(session, run, file_id)
    out: dict[str, Any] = {"run_id": str(run.id), "file_id": str(file_id), "review": review}
    if replayed is not None:
        out["replayed"] = True
        out["decision_id"] = str(replayed.id)
    return out


async def get_file_review_state(
    session: AsyncSession, run: ImportRun, file_id: uuid.UUID
) -> dict[str, Any]:
    _, _, node = await repo.get_file_membership(session, run.id, file_id)
    cls = await _classification_for(session, run, file_id)
    decisions = list(await repo.decisions_for_file(session, run.id, file_id))
    return fold_file_decisions(decisions, node, cls).as_dict()


def _decision_view(d: ImportDecision) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "action": d.action.value,
        "file_id": str(d.file_id) if d.file_id is not None else None,
        "cluster_id": str(d.cluster_id) if d.cluster_id is not None else None,
        "target_kind": d.target_kind,
        "before": d.before,
        "after": d.after,
        "decided_by": str(d.decided_by),
        "decided_at": d.decided_at.isoformat() if d.decided_at is not None else None,
    }
