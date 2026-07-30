"""Issue #406 — version-history authorization follows each immutable version's state.

Every surface first requires ``document.read`` for the live Document metadata row.  Version rows
then add the state-specific content capability:

* Effective -> no additional key;
* Draft / InReview / Approved -> ``document.read_draft``;
* Superseded / Obsolete -> ``document.read_obsolete``.

The list returns only authorized rows.  Detail/download enforce one row, and text/visual diffs
enforce both sides.  The Document-detail ``read_draft`` affordance probes the same immutable-state
contexts rather than the mutable headline.  These proofs use all six version states, mixed-state
pairs, an ARTIFACT-scoped ALLOW with request/lifecycle predicates, and a lifecycle-scoped explicit
DENY.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from easysynq_api.db.models._vault_enums import DocumentCurrentState, VersionState
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.document_version import DocumentVersion
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel
from easysynq_api.services.diff import set_text_extractor
from easysynq_api.services.diff.extractor import TikaTextExtractor
from easysynq_api.tasks.visual_diff import visual_diff as visual_diff_task

from .test_vault import (
    _auth,
    _checkin,
    _create,
    _ensure_user,
    _grant_doc_perms,
    _sop_type_id,
    _upload,
)

pytestmark = pytest.mark.integration

_DRAFT_STATES = frozenset({VersionState.Draft, VersionState.InReview, VersionState.Approved})
_OBSOLETE_STATES = frozenset({VersionState.Superseded, VersionState.Obsolete})


@dataclass(frozen=True)
class _VersionMatrix:
    document_id: str
    version_ids: dict[VersionState, str]


class _BytesDecodeExtractor:
    async def extract_text(
        self, *, data: bytes, mime_type: str | None, filename: str
    ) -> str | None:
        return data.decode("utf-8", "replace")


def _subject(label: str) -> str:
    return f"kc-version-authz-{label}-{uuid.uuid4().hex[:10]}"


async def _grant(
    subject: str,
    keys: Iterable[str],
    *,
    effect: Effect = Effect.ALLOW,
    level: ScopeLevel = ScopeLevel.SYSTEM,
    selector: dict[str, object] | None = None,
    predicates: dict[str, object] | None = None,
) -> None:
    """Add direct overrides with one shared scope shape, keeping each test principal run-scoped."""
    keys_tuple = tuple(keys)
    async with get_sessionmaker()() as session:
        user = await _ensure_user(session, subject)
        permissions = {
            permission.key: permission
            for permission in (
                await session.execute(select(Permission).where(Permission.key.in_(keys_tuple)))
            )
            .scalars()
            .all()
        }
        assert set(permissions) == set(keys_tuple)
        for key in keys_tuple:
            scope = Scope(
                org_id=user.org_id,
                level=level,
                selector=selector or {},
            )
            session.add(scope)
            await session.flush()
            session.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=permissions[key].id,
                    effect=effect,
                    scope_id=scope.id,
                    predicates=predicates,
                )
            )
        await session.commit()


async def _seed_version_matrix(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> _VersionMatrix:
    """Create one real blob/version, then insert the other five immutable state snapshots.

    The lifecycle engine is covered elsewhere.  Direct INSERTs vary only ``version_state`` and keep
    the blob/document/scope tuple identical, isolating the authorization matrix under test.
    """
    creator = _subject("creator")
    await _grant_doc_perms(creator)
    headers = _auth(token_factory, creator)
    document_id = (await _create(app_client, headers, await _sop_type_id()))["id"]
    checked_out = await app_client.post(
        f"/api/v1/documents/{document_id}/checkout", headers=headers
    )
    assert checked_out.status_code == 200, checked_out.text
    sha = await _upload(
        app_client,
        headers,
        document_id,
        f"version-authz-{document_id}\n".encode(),
        ct="text/plain",
    )
    checked_in = await _checkin(
        app_client,
        headers,
        document_id,
        sha,
        change_reason="Draft authorization fixture",
        change_significance="MINOR",
    )
    assert checked_in.status_code == 201, checked_in.text

    async with get_sessionmaker()() as session:
        base = await session.get(DocumentVersion, uuid.UUID(checked_in.json()["id"]))
        document = await session.get(DocumentedInformation, uuid.UUID(document_id))
        assert base is not None and document is not None
        assert base.version_state is VersionState.Draft

        versions: dict[VersionState, DocumentVersion] = {VersionState.Draft: base}
        for version_seq, state in enumerate(tuple(VersionState)[1:], start=2):
            version = DocumentVersion(
                org_id=base.org_id,
                document_id=base.document_id,
                version_seq=version_seq,
                revision_label=f"Authz {state.value}",
                change_significance=base.change_significance,
                change_reason=f"{state.value} authorization fixture",
                version_state=state,
                source_blob_sha256=base.source_blob_sha256,
                metadata_snapshot=dict(base.metadata_snapshot),
                rendition_blob_sha256=None,
                effective_from=None,
                effective_to=None,
                superseded_by_version_id=None,
                dcr_id=None,
                imported=False,
                author_user_id=base.author_user_id,
                created_by=base.created_by,
            )
            session.add(version)
            versions[state] = version
        await session.flush()

        # Deliberately keep one live Effective version while the headline is UnderRevision.  A
        # specialized check that accidentally reuses the headline state will fail the lifecycle
        # predicate assertions below.
        document.current_state = DocumentCurrentState.UnderRevision
        document.current_effective_version_id = versions[VersionState.Effective].id
        await session.commit()

        return _VersionMatrix(
            document_id=document_id,
            version_ids={state: str(version.id) for state, version in versions.items()},
        )


async def _assert_detail_and_download(
    app_client: AsyncClient,
    headers: dict[str, str],
    matrix: _VersionMatrix,
    allowed_states: frozenset[VersionState],
) -> None:
    for state, version_id in matrix.version_ids.items():
        expected = 200 if state in allowed_states else 403
        detail = await app_client.get(
            f"/api/v1/documents/{matrix.document_id}/versions/{version_id}",
            headers=headers,
        )
        assert detail.status_code == expected, (state, detail.text)
        download = await app_client.get(
            f"/api/v1/documents/{matrix.document_id}/versions/{version_id}/download",
            headers=headers,
        )
        assert download.status_code == expected, (state, download.text)


async def test_version_list_detail_and_download_follow_the_state_matrix(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    matrix = await _seed_version_matrix(app_client, token_factory)
    cases = (
        ("base", ("document.read",), frozenset({VersionState.Effective})),
        (
            "draft",
            ("document.read", "document.read_draft"),
            frozenset({VersionState.Effective, *_DRAFT_STATES}),
        ),
        (
            "obsolete",
            ("document.read", "document.read_obsolete"),
            frozenset({VersionState.Effective, *_OBSOLETE_STATES}),
        ),
        (
            "all",
            ("document.read", "document.read_draft", "document.read_obsolete"),
            frozenset(VersionState),
        ),
    )

    for label, keys, allowed_states in cases:
        subject = _subject(label)
        await _grant(subject, keys)
        headers = _auth(token_factory, subject)
        listed = await app_client.get(
            f"/api/v1/documents/{matrix.document_id}/versions", headers=headers
        )
        assert listed.status_code == 200, (label, listed.text)
        payload = listed.json()
        assert {VersionState(row["version_state"]) for row in payload} == allowed_states
        assert [row["version_seq"] for row in payload] == sorted(
            (row["version_seq"] for row in payload), reverse=True
        )
        await _assert_detail_and_download(app_client, headers, matrix, allowed_states)

    specialized_only = _subject("specialized-only")
    await _grant(
        specialized_only,
        ("document.read_draft", "document.read_obsolete"),
    )
    denied = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions",
        headers=_auth(token_factory, specialized_only),
    )
    assert denied.status_code == 403, denied.text


async def test_version_resource_preserves_artifact_lifecycle_ip_and_deny_wins(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
) -> None:
    matrix = await _seed_version_matrix(app_client, token_factory)

    scoped_reader = _subject("scoped")
    await _grant(scoped_reader, ("document.read",))
    await _grant(
        scoped_reader,
        ("document.read_draft",),
        level=ScopeLevel.ARTIFACT,
        selector={"artifact_id": matrix.document_id},
        predicates={
            "lifecycle_state": [VersionState.Draft.value],
            "ip_allow": ["127.0.0.1"],
        },
    )
    scoped_headers = _auth(token_factory, scoped_reader)
    draft = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions/{matrix.version_ids[VersionState.Draft]}",
        headers=scoped_headers,
    )
    assert draft.status_code == 200, draft.text
    # The live headline is UnderRevision, but the Draft predicate admits an immutable Draft state.
    scoped_detail = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}",
        headers=scoped_headers,
    )
    assert scoped_detail.status_code == 200, scoped_detail.text
    assert scoped_detail.json()["capabilities"]["read_draft"] is True
    in_review = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions/"
        f"{matrix.version_ids[VersionState.InReview]}",
        headers=scoped_headers,
    )
    assert in_review.status_code == 403, in_review.text

    headline_reader = _subject("headline")
    await _grant(headline_reader, ("document.read",))
    await _grant(
        headline_reader,
        ("document.read_draft",),
        level=ScopeLevel.ARTIFACT,
        selector={"artifact_id": matrix.document_id},
        predicates={"lifecycle_state": [DocumentCurrentState.UnderRevision.value]},
    )
    headline_headers = _auth(token_factory, headline_reader)
    headline_detail = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}",
        headers=headline_headers,
    )
    assert headline_detail.status_code == 200, headline_detail.text
    assert headline_detail.json()["capabilities"]["read_draft"] is False
    headline_draft = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions/{matrix.version_ids[VersionState.Draft]}",
        headers=headline_headers,
    )
    assert headline_draft.status_code == 403, headline_draft.text

    deny_reader = _subject("deny")
    await _grant(deny_reader, ("document.read", "document.read_obsolete"))
    await _grant(
        deny_reader,
        ("document.read_obsolete",),
        effect=Effect.DENY,
        level=ScopeLevel.ARTIFACT,
        selector={"artifact_id": matrix.document_id},
        predicates={"lifecycle_state": [VersionState.Superseded.value]},
    )
    deny_headers = _auth(token_factory, deny_reader)
    superseded = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions/"
        f"{matrix.version_ids[VersionState.Superseded]}",
        headers=deny_headers,
    )
    assert superseded.status_code == 403, superseded.text
    obsolete = await app_client.get(
        f"/api/v1/documents/{matrix.document_id}/versions/"
        f"{matrix.version_ids[VersionState.Obsolete]}",
        headers=deny_headers,
    )
    assert obsolete.status_code == 200, obsolete.text


async def test_text_and_visual_diffs_require_both_version_permissions(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = await _seed_version_matrix(app_client, token_factory)
    monkeypatch.setattr(visual_diff_task, "delay", lambda *_args, **_kwargs: None)

    draft_id = matrix.version_ids[VersionState.Draft]
    effective_id = matrix.version_ids[VersionState.Effective]
    superseded_id = matrix.version_ids[VersionState.Superseded]
    mixed_path = (
        f"/api/v1/documents/{matrix.document_id}/versions/{superseded_id}/diff?from={draft_id}"
    )
    visual_path = (
        f"/api/v1/documents/{matrix.document_id}/versions/{superseded_id}"
        f"/visual-diff?from={draft_id}"
    )
    visual_page_path = (
        f"/api/v1/documents/{matrix.document_id}/versions/{superseded_id}"
        f"/visual-diff/page/0?from={draft_id}&layer=diff"
    )

    draft_reader = _subject("diff-draft")
    await _grant(draft_reader, ("document.read", "document.read_draft"))
    draft_headers = _auth(token_factory, draft_reader)
    obsolete_reader = _subject("diff-obsolete")
    await _grant(obsolete_reader, ("document.read", "document.read_obsolete"))
    obsolete_headers = _auth(token_factory, obsolete_reader)

    for method, path in (
        ("GET", mixed_path),
        ("POST", visual_path),
        ("GET", visual_path),
        ("GET", visual_page_path),
    ):
        for headers in (draft_headers, obsolete_headers):
            denied = await app_client.request(method, path, headers=headers)
            assert denied.status_code == 403, (method, path, denied.text)

    both_reader = _subject("diff-both")
    await _grant(
        both_reader,
        ("document.read", "document.read_draft", "document.read_obsolete"),
    )
    both_headers = _auth(token_factory, both_reader)
    set_text_extractor(_BytesDecodeExtractor())
    try:
        mixed = await app_client.get(mixed_path, headers=both_headers)
        assert mixed.status_code == 200, mixed.text

        # Effective + Draft needs only read_draft; Effective + Superseded needs only read_obsolete.
        effective_draft = await app_client.get(
            f"/api/v1/documents/{matrix.document_id}/versions/{draft_id}/diff?from={effective_id}",
            headers=draft_headers,
        )
        assert effective_draft.status_code == 200, effective_draft.text
        effective_obsolete = await app_client.get(
            f"/api/v1/documents/{matrix.document_id}/versions/{superseded_id}"
            f"/diff?from={effective_id}",
            headers=obsolete_headers,
        )
        assert effective_obsolete.status_code == 200, effective_obsolete.text
    finally:
        set_text_extractor(TikaTextExtractor())

    requested = await app_client.post(visual_path, headers=both_headers)
    assert requested.status_code == 202, requested.text
    polled = await app_client.get(visual_path, headers=both_headers)
    assert polled.status_code == 202, polled.text
    # The cache is still Pending, so an authorized page read reaches the normal availability 404.
    page = await app_client.get(visual_page_path, headers=both_headers)
    assert page.status_code == 404, page.text
