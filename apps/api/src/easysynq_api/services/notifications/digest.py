"""The daily-digest bundler (spec §5.2). A pure item renderer + an async per-user sweep that claims
a user's due notification rows, renders ONE summary email (summaries + deep links only — never
controlled content), creates a PENDING digest NotificationEmail (the existing outbox_drain sends
it), and stamps digested_at. One idempotent txn per user; a re-run finds nothing pending → no-op."""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...config import Settings
from ...db.models._notification_enums import NotificationEmailKind
from ...db.models.app_user import AppUser, UserStatus
from ...db.models.notification import Notification, NotificationEmail, NotificationPreference
from ...db.models.system_config import SystemConfig
from .constants import EVENT_DIGEST_DAILY
from .preferences import effective_preferences
from .render import render
from .subjects import prefs_link

logger = logging.getLogger("easysynq.notifications.digest")

_INACTIVE = {UserStatus.LOCKED, UserStatus.DISABLED, UserStatus.RETIRED}


def render_digest_items(notes: list[Notification]) -> tuple[str, int]:
    """(items_block, raw_count). Group identical (event_key, subject_id) rows into one line with a
    xN count; list each distinct item as '* {title} -- {deep_link}'. Plain text (email body)."""
    seen: dict[tuple[str, str], list[Notification]] = {}
    order: list[tuple[str, str]] = []
    for n in notes:
        key = (n.event_key, str(n.subject_id))
        if key not in seen:
            seen[key] = []
            order.append(key)
        seen[key].append(n)
    lines: list[str] = []
    for key in order:
        group = seen[key]
        head = group[0]
        suffix = f" x{len(group)}" if len(group) > 1 else ""
        lines.append(f"* {head.title}{suffix} -- {head.deep_link}")
    return "\n".join(lines), len(notes)


async def due_user_ids(session: AsyncSession, now: datetime.datetime) -> list[uuid.UUID]:
    rows = (
        (
            await session.execute(
                select(Notification.recipient_user_id)
                .where(
                    Notification.digest_due_at.is_not(None),
                    Notification.digest_due_at <= now,
                    Notification.digested_at.is_(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def bundle_user_digest(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    settings: Settings,
    now: datetime.datetime,
) -> bool:
    """One idempotent txn: claim the user's due rows, re-check eligibility, create the digest email
    (or just stamp if ineligible), stamp digested_at. Returns True iff an email was created."""
    notes = (
        (
            await session.execute(
                select(Notification)
                .where(
                    Notification.recipient_user_id == user_id,
                    Notification.digest_due_at.is_not(None),
                    Notification.digest_due_at <= now,
                    Notification.digested_at.is_(None),
                )
                .order_by(Notification.created_at)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if not notes:
        return False

    user = await session.get(AppUser, user_id)
    org_id = notes[0].org_id
    cfg = await session.get(SystemConfig, org_id)
    pref = await session.get(NotificationPreference, user_id)
    eff = effective_preferences(pref)
    eligible = (
        bool(settings.smtp_host)
        and bool(cfg and cfg.notifications_email_enabled)
        and eff.email_enabled
        and user is not None
        and user.status not in _INACTIVE
        and bool(user.email)
    )

    created = False
    if eligible and user is not None and user.email is not None:
        items, count = render_digest_items(list(notes))
        first_name = (user.display_name or "there").split()[0]
        forms = await render(
            session,
            EVENT_DIGEST_DAILY,
            {
                "recipient.first_name": first_name,
                "item_count": count,
                "items": items,
                "prefs_link": prefs_link(),
            },
        )
        if forms is not None:
            session.add(
                NotificationEmail(
                    org_id=org_id,
                    notification_id=None,
                    recipient_user_id=user_id,
                    recipient_email=user.email,
                    subject=forms.email_subject,
                    body=forms.email_body,
                    email_kind=NotificationEmailKind.DIGEST,
                    item_count=count,
                )
            )
            created = True

    for n in notes:
        n.digested_at = now
    await session.commit()
    return created


async def sweep_digests(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
    now: datetime.datetime,
) -> dict[str, int]:
    """Bundle each due user's pending notifications into ONE summary email (hourly).

    Fresh session per user — the MissingGreenlet guard (engineering-patterns)."""
    counts: dict[str, int] = {"users": 0, "emails": 0}
    async with sessionmaker() as session:
        users = await due_user_ids(session, now)
    for user_id in users:
        async with sessionmaker() as session:
            created = await bundle_user_digest(session, user_id=user_id, settings=settings, now=now)
        counts["users"] += 1
        if created:
            counts["emails"] += 1
    return counts
