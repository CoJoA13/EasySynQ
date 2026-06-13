"""Celery worker + Beat. The app instance is defined here so the worker/beat
containers start cleanly in S0; tasks are registered by importing their modules:
``lifecycle`` (the S4 future-dated release sweep), ``audit`` (the S6 chain-linker,
chain-verify, checkpoint-anchor, and partition-rotation tasks), ``mirror`` (the S7
read-only filesystem mirror reconcile), and ``backup`` (the S8b2 nightly durable
backup + the restore-test drill / gate G-C).
"""

from . import (  # noqa: F401  (registers the Celery tasks)
    ack,
    audit,
    backup,
    blob_verify,
    ingestion,
    lifecycle,
    mgmt_review,
    mirror,
    packs,
    records,
    review,
    visual_diff,
)
from .app import app

__all__ = ["app"]
