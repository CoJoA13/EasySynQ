"""Celery worker + Beat. The app instance is defined here so the worker/beat
containers start cleanly in S0; real tasks (mirror-sync, audit chain-linker,
checkpoint, backup, partition-roll) are added in later slices (S6/S7).
"""

from .app import app

__all__ = ["app"]
