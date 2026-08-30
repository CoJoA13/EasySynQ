"""Issue #330 — the lifecycle-independent Document metadata read boundary.

The live ``documented_information`` row is metadata, not version content.  Its three canonical
read surfaces therefore keep ``document.read`` as the one authorization key for every headline
lifecycle state:

* Library: ``GET /documents`` (filter, not 403);
* detail: ``GET /documents/{id}`` (403 on deny);
* Master Document List: ``GET /reports/document-control`` (``report.read`` surface gate,
  then per-row ``document.read``).

``document.read_draft`` and ``document.read_obsolete`` remain version-content/history permissions;
neither substitutes for ``document.read`` on these metadata surfaces.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.db.models.authz_grant import PermissionOverride
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.permission import Permission
from easysynq_api.db.models.scope import Scope
from easysynq_api.db.session import get_sessionmaker
from easysynq_api.domain.authz.types import Effect, ScopeLevel

from . import s5_helpers as s5
from .test_vault import _auth, _ensure_user

pytestmark = pytest.mark.integration

_REGISTER_ROUTE = "/api/v1/reports/document-control"


@pytest.fixture
def subj() -> SimpleNamespace:
    salt = uuid.uuid4().hex[:10]
    return SimpleNamespace(
        creator=f"kc-meta-creator-{salt}",
        reader=f"kc-meta-reader-{salt}",
        version_reader=f"kc-meta-version-{salt}",
    )


async def _grant(subject: str, *keys: str) -> None:
    """Grant each key at SYSTEM scope, isolating permission-key selection from scope matching."""
    async with get_sessionmaker()() as session:
        user = await _ensure_user(session, subject)
        permissions = {
            permission.key: permission
            for permission in (
                await session.execute(select(Permission).where(Permission.key.in_(keys)))
            )
            .scalars()
            .all()
        }
        assert set(permissions) == set(keys)
        for key in keys:
            scope = Scope(org_id=user.org_id, level=ScopeLevel.SYSTEM)
            session.add(scope)
            await session.flush()
            session.add(
                PermissionOverride(
                    org_id=user.org_id,
                    user_id=user.id,
                    permission_id=permissions[key].id,
                    effect=Effect.ALLOW,
                    scope_id=scope.id,
                )
            )
        await session.commit()


async def _seed_every_lifecycle_state(
    client: AsyncClient, creator_headers: dict[str, str]
) -> tuple[dict[DocumentCurrentState, str], str, str]:
    """Create valid document rows, then vary only the headline state used by the authorization PDP.

    The lifecycle transition engine has its own integration suite.  Directly setting this one enum
    keeps this regression focused on permission-key selection and covers all seven contract states,
    including ``Superseded`` (which is uncommon as a document headline in ordinary workflows).
    """
    type_id = await s5.type_id("SOP")
    marker = f"metadata-policy-{uuid.uuid4().hex[:10]}"
    ids_by_state: dict[DocumentCurrentState, str] = {}
    owner_user_id = ""

    for state in DocumentCurrentState:
        response = await client.post(
            "/api/v1/documents",
            headers=creator_headers,
            json={
                "title": f"{marker}-{state.value}",
                "document_type_id": type_id,
                "area_code": "QMS",
            },
        )
        assert response.status_code == 201, response.text
        document = response.json()
        ids_by_state[state] = document["id"]
        owner_user_id = document["owner_user_id"]

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(DocumentedInformation).where(
                    DocumentedInformation.id.in_(
                        uuid.UUID(document_id) for document_id in ids_by_state.values()
                    )
                )
            )
        ).scalars()
        states_by_id = {
            uuid.UUID(document_id): state for state, document_id in ids_by_state.items()
        }
        for row in rows:
            row.current_state = states_by_id[row.id]
        await session.commit()

    return ids_by_state, marker, owner_user_id


@pytest.fixture
async def lifecycle_metadata_rows(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
) -> AsyncIterator[tuple[dict[DocumentCurrentState, str], str, str]]:
    """Yield the seven-state test set, then restore valid Draft headlines for the shared test DB."""
    await s5.grant_lifecycle(subj.creator)
    creator_headers = _auth(token_factory, subj.creator)
    seeded = await _seed_every_lifecycle_state(app_client, creator_headers)
    try:
        yield seeded
    finally:
        async with get_sessionmaker()() as session:
            await session.execute(
                update(DocumentedInformation)
                .where(
                    DocumentedInformation.id.in_(
                        uuid.UUID(document_id) for document_id in seeded[0].values()
                    )
                )
                .values(current_state=DocumentCurrentState.Draft)
            )
            await session.commit()


async def test_document_read_is_the_metadata_key_for_every_lifecycle_state(
    app_client: AsyncClient,
    token_factory: Callable[..., str],
    subj: SimpleNamespace,
    lifecycle_metadata_rows: tuple[dict[DocumentCurrentState, str], str, str],
) -> None:
    """A plain metadata reader sees all states; version-only keys see none on all three surfaces.

    This is mutation-distinguishing for both failure modes raised by #330:

    * replacing ``document.read`` with a state-selected draft/obsolete key makes the first half
      fail;
    * treating those specialized keys as metadata alternatives makes the second half fail.
    """
    ids_by_state, marker, owner_user_id = lifecycle_metadata_rows
    expected = {document_id: state.value for state, document_id in ids_by_state.items()}

    await _grant(subj.reader, "document.read", "report.read")
    reader_headers = _auth(token_factory, subj.reader)

    library = await app_client.get(
        f"/api/v1/documents?q={marker}&limit=100", headers=reader_headers
    )
    assert library.status_code == 200, library.text
    library_states = {
        row["id"]: row["current_state"] for row in library.json()["data"] if row["id"] in expected
    }
    assert library_states == expected

    for document_id, expected_state in expected.items():
        detail = await app_client.get(f"/api/v1/documents/{document_id}", headers=reader_headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["current_state"] == expected_state

    register = await app_client.get(
        f"{_REGISTER_ROUTE}?filter[owner_user_id][eq]={owner_user_id}",
        headers=reader_headers,
    )
    assert register.status_code == 200, register.text
    register_states = {
        row["id"]: row["current_state"] for row in register.json()["rows"] if row["id"] in expected
    }
    assert register_states == expected

    await _grant(
        subj.version_reader,
        "document.read_draft",
        "document.read_obsolete",
        "report.read",
    )
    version_headers = _auth(token_factory, subj.version_reader)

    hidden_library = await app_client.get(
        f"/api/v1/documents?q={marker}&limit=100", headers=version_headers
    )
    assert hidden_library.status_code == 200, hidden_library.text
    assert not ({row["id"] for row in hidden_library.json()["data"]} & set(expected))

    for document_id in expected:
        denied_detail = await app_client.get(
            f"/api/v1/documents/{document_id}", headers=version_headers
        )
        assert denied_detail.status_code == 403, denied_detail.text

    hidden_register = await app_client.get(
        f"{_REGISTER_ROUTE}?filter[owner_user_id][eq]={owner_user_id}",
        headers=version_headers,
    )
    assert hidden_register.status_code == 200, hidden_register.text
    assert not ({row["id"] for row in hidden_register.json()["rows"]} & set(expected))
