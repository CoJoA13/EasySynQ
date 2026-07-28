"""User-facing problem-copy proofs for the risk-register lifecycle."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from easysynq_api.db.models._vault_enums import DocumentCurrentState
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.problems import ProblemException
from easysynq_api.services.risk import lifecycle

pytestmark = pytest.mark.unit


async def test_empty_publish_detail_is_ready_for_verbatim_user_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = AppUser(org_id=uuid.uuid4())
    head = DocumentedInformation(
        id=uuid.uuid4(),
        current_state=DocumentCurrentState.Draft,
    )
    monkeypatch.setattr(lifecycle, "find_head", AsyncMock(return_value=head))
    monkeypatch.setattr(lifecycle, "_working_register", AsyncMock(return_value={"rows": []}))

    with pytest.raises(ProblemException) as raised:
        await lifecycle.publish_register(
            AsyncMock(spec=AsyncSession),
            AsyncMock(),
            actor,
        )

    assert raised.value.detail == (
        "Add at least one risk or opportunity before publishing the register."
    )
