"""Celery worker + Beat. The app instance is defined here so the worker/beat
containers start cleanly in S0; tasks are registered by importing their modules:
``lifecycle`` (the S4 future-dated release sweep) and ``audit`` (the S6 chain-linker,
chain-verify, checkpoint-anchor, and partition-rotation tasks).
"""

from . import audit, lifecycle  # noqa: F401  (registers the Celery tasks)
from .app import app

__all__ = ["app"]
