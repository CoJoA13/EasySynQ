from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from easysynq_api.main import app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark by directory so CI's ``-m unit`` / ``-m integration`` selection gates the WHOLE
    pyramid — historically most ``tests/unit`` files carried no explicit ``pytest.mark.unit``, so
    ``pytest -m unit`` silently ran only the few that did (e.g. the AC#6 canonical golden-vector and
    the backup-archive tests were deselected). Marking by the parent directory closes that gap and
    keeps future files covered without a per-file marker. Explicit module markers are harmless dups.
    """
    for item in items:
        parent = item.path.parent.name
        if parent == "unit":
            item.add_marker(pytest.mark.unit)
        elif parent == "integration":
            item.add_marker(pytest.mark.integration)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
