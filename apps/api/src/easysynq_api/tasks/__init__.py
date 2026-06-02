"""Celery worker + Beat. The app instance is defined here so the worker/beat
containers start cleanly in S0; tasks are registered by importing their modules:
``lifecycle`` (the S4 future-dated release sweep), ``audit`` (the S6 chain-linker,
chain-verify, checkpoint-anchor, and partition-rotation tasks), and ``mirror`` (the S7
read-only filesystem mirror reconcile).
"""

from . import audit, lifecycle, mirror  # noqa: F401  (registers the Celery tasks)
from .app import app

__all__ = ["app"]
