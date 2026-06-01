"""Celery application. Redis is the broker and result backend (D4)."""

from __future__ import annotations

from celery import Celery

from ..config import get_settings

_settings = get_settings()

app = Celery(
    "easysynq",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)
app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={},  # populated in S6/S7 (cutover sweep, chain-linker, partition roll, backup)
)
