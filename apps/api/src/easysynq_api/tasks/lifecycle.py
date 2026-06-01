"""Celery tasks for the document lifecycle (slice S4).

``release_due_versions`` is the Beat cutover sweep: it releases every Approved version whose
``effective_from`` has arrived (future-dated go-live). It reuses the async cutover via
``asyncio.run`` (the worker is a sync process; ``release_due`` uses its own disposed engine so a
fresh event loop per invocation is safe).
"""

from __future__ import annotations

import asyncio

from .app import app


# Celery ships no type stubs (ignore_missing_imports), so app.task is an untyped decorator.
@app.task(name="easysynq.release_due_versions")  # type: ignore[untyped-decorator]
def release_due_versions() -> int:
    """Release all Approved versions whose ``effective_from <= now``; returns the count released."""
    from ..services.vault.lifecycle import release_due

    return len(asyncio.run(release_due()))
