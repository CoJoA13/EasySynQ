"""Celery worker + Beat. The app instance is defined here so the worker/beat
containers start cleanly in S0; the first real task — the S4 future-dated release
sweep — is registered by importing ``lifecycle``. More (mirror-sync, audit
chain-linker, checkpoint, backup, partition-roll) are added in later slices (S6/S7).
"""

from . import lifecycle  # noqa: F401  (registers easysynq.release_due_versions)
from .app import app

__all__ = ["app"]
