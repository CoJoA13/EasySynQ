"""Shared notification-email transport readiness predicates."""

from __future__ import annotations

from ...config import Settings


def smtp_transport_configured(settings: Settings) -> bool:
    """Return whether the deployment has a deliverable SMTP transport."""
    return bool(settings.smtp_host)


def email_delivery_ready(*, org_email_enabled: bool, settings: Settings) -> bool:
    """Return whether org policy and deployment transport both allow delivery."""
    return org_email_enabled and smtp_transport_configured(settings)
