from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.routing import BaseRoute, Match

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


@pytest.fixture
def resolve_route_endpoint() -> Callable[[FastAPI, str, str], str | None]:
    """Resolve a path+method to the leaf endpoint name the wired app would dispatch to.

    FastAPI >=0.137 stopped flattening ``include_router``: each included sub-router is now an
    ``_IncludedRouter`` wrapper in ``app.router.routes`` (it exposes ``.original_router`` but no
    ``.endpoint``), so the mount-order guards can no longer read ``route.endpoint`` off the flat
    list. Walk the tree the way Starlette dispatches — first matching route at each level wins, an
    included sub-router delegates to its own routes in definition order — keeping the
    static-before-``{id}`` ordering invariants provable app-level without HTTP (S-pack-2)."""

    def resolve(app: FastAPI, path: str, method: str) -> str | None:
        scope = {"type": "http", "path": path, "method": method}

        def walk(routes: Iterable[BaseRoute]) -> str | None:
            for route in routes:
                match = route.matches(scope)[0]
                if match is Match.NONE:
                    continue
                # _IncludedRouter (FastAPI >=0.137) matches by prefix (method-agnostic) + delegates
                # to its own routes; recurse to the leaf instead of reading a (missing) .endpoint.
                included = getattr(route, "original_router", None)
                if included is not None:
                    leaf = walk(included.routes)
                    if leaf is not None:
                        return leaf
                    continue  # prefix matched but no leaf did — keep scanning siblings
                # A LEAF route only wins on Match.FULL (path AND method). A Match.PARTIAL means the
                # path matched but the method did not — Starlette keeps scanning for a FULL match
                # (it falls back to PARTIAL only for a 405), so a partial must NOT win here — else a
                # method-sensitive guard (e.g. PATCH on a path whose GET is declared first) would
                # resolve to the wrong endpoint and silently pass.
                if match is not Match.FULL:
                    continue
                endpoint = getattr(route, "endpoint", None)
                if endpoint is not None:
                    return str(endpoint.__name__)
            return None

        return walk(app.router.routes)

    return resolve
