"""S11 NFR P95 smoke (doc 03 §7 / doc 18 §7 [PROOF], doc 18 §8 'NFR P95 smoke').

Server-side P95 latency over the in-process ASGI transport (no network/browser — the dev host has
no headless browser; CLAUDE.md). The SPEC budgets are: metadata read P95 ≤300ms, interactive P95
≤1.5s, search P95 ≤800ms, cached-PDF first page ≤2s, Office→PDF 20pp ≤15s async (doc 03 §7).

The cached-PDF + Office→PDF budgets need Gotenberg (not run in CI) → they are a MANUAL dev-stack
proof (docs/runbooks/nfr-budgets.md), not gated here. For the PG/app-tier endpoints the CI GATE uses
GENEROUS regression bounds (~5x spec), NOT the SLO: single-host shared CI runners are noisy, so the
gate catches a CATASTROPHIC regression (an accidental N+1, a dropped index), not the production
budget. The spec budgets live in the docstring + the runbook.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from statistics import mean
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from . import s5_helpers as s5
from .test_vault import _auth, _create

pytestmark = pytest.mark.integration

# CI regression ceilings (ms) — ~5x the doc-03 §7 SLO to absorb shared-runner jitter. A breach means
# a catastrophic regression, not "the SLO is missed on a laptop". SLOs (documented): metadata 300,
# interactive 1500, search 800.
_GATE_METADATA_MS = 1500.0
_GATE_INTERACTIVE_MS = 3000.0
_GATE_SEARCH_MS = 2500.0

_N_DOCS = 40
_ITERS = 25


def _p95(samples_ms: list[float]) -> float:
    """Nearest-rank P95 (pure stdlib — no numpy)."""
    ordered = sorted(samples_ms)
    idx = min(len(ordered) - 1, max(0, round(0.95 * len(ordered)) - 1))
    return ordered[idx]


async def _time(client: AsyncClient, method: str, url: str, headers: dict[str, str]) -> float:
    start = time.perf_counter()
    resp = await client.request(method, url, headers=headers)
    elapsed = (time.perf_counter() - start) * 1000.0
    assert resp.status_code == 200, f"{url} -> {resp.status_code}: {resp.text[:200]}"
    return elapsed


async def test_nfr_p95_smoke(app_client: AsyncClient, token_factory: Callable[..., str]) -> None:
    salt = uuid.uuid4().hex[:8]
    subj = SimpleNamespace(a=f"kc-nfr-{salt}")
    await s5.grant_lifecycle(subj.a)
    ha = _auth(token_factory, subj.a)
    type_id = await s5.type_id("SOP")

    doc_ids = [(await _create(app_client, ha, type_id))["id"] for _ in range(_N_DOCS)]

    # metadata read (single doc) — SLO ≤300ms
    meta = [
        await _time(app_client, "GET", f"/api/v1/documents/{doc_ids[i % _N_DOCS]}", ha)
        for i in range(_ITERS)
    ]
    # interactive: the list (S10 clause_refs-join N+1 guard) + the My-Tasks aggregation — SLO ≤1.5s
    listing = [
        await _time(app_client, "GET", "/api/v1/documents?limit=50", ha) for _ in range(_ITERS)
    ]
    me = [await _time(app_client, "GET", "/api/v1/me", ha) for _ in range(_ITERS)]
    # search (Postgres-FTS; CI-feasible, no OpenSearch) — SLO ≤800ms
    search = [
        await _time(app_client, "GET", f"/api/v1/search?q=quality-{salt}", ha)
        for _ in range(_ITERS)
    ]

    p95_meta, p95_list, p95_me, p95_search = _p95(meta), _p95(listing), _p95(me), _p95(search)
    summary = (
        f"P95(ms) metadata={p95_meta:.0f} (mean {mean(meta):.0f}) "
        f"list={p95_list:.0f} me={p95_me:.0f} search={p95_search:.0f} "
        f"[gates: meta≤{_GATE_METADATA_MS} interactive≤{_GATE_INTERACTIVE_MS} "
        f"search≤{_GATE_SEARCH_MS}; SLOs: meta300 interactive1500 search800]"
    )
    assert p95_meta <= _GATE_METADATA_MS, summary
    assert p95_list <= _GATE_INTERACTIVE_MS, summary
    assert p95_me <= _GATE_INTERACTIVE_MS, summary
    assert p95_search <= _GATE_SEARCH_MS, summary
