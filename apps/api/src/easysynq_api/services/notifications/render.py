"""DB-backed versioned template render (spec §5).

Logic-free: only ``{{ var }}`` and ``{{ var | date }}`` over a whitelisted, HTML-escaped variable
set — no eval, no Jinja (the ast-whitelist/ReDoS posture).
"""

from __future__ import annotations

import dataclasses
import datetime
import html
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models.notification import NotificationTemplate
from .constants import VARIABLE_WHITELIST

# {{ name }} or {{ name | date }} — name is dotted word chars; nothing else is a slot.
_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*(?:\|\s*(date)\s*)?\}\}")
_PLACEHOLDER = "—"


@dataclasses.dataclass(frozen=True)
class RenderedForms:
    in_app_title: str
    in_app_body: str
    email_subject: str
    email_body: str
    template_id: uuid.UUID
    template_version: int


def _fmt_date(value: object) -> str:
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    return _PLACEHOLDER


def _substitute(text: str, variables: dict[str, object], allowed: frozenset[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        name, filt = m.group(1), m.group(2)
        if name not in allowed:
            return m.group(0)  # not a slot — leave literal (defense-in-depth)
        value = variables.get(name)
        if filt == "date":
            return _fmt_date(value)
        if value is None:
            return _PLACEHOLDER
        return html.escape(str(value))

    return _TOKEN.sub(repl, text)


async def render(
    session: AsyncSession, event_key: str, variables: dict[str, object], locale: str = "en"
) -> RenderedForms | None:
    tmpl = (
        await session.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.event_key == event_key,
                NotificationTemplate.locale == locale,
                NotificationTemplate.is_effective.is_(True),
            )
        )
    ).scalar_one_or_none()
    if tmpl is None:
        return None
    allowed = VARIABLE_WHITELIST.get(event_key, frozenset())
    return RenderedForms(
        in_app_title=_substitute(tmpl.in_app_title, variables, allowed),
        in_app_body=_substitute(tmpl.in_app_body, variables, allowed),
        email_subject=_substitute(tmpl.email_subject, variables, allowed),
        email_body=_substitute(tmpl.email_body, variables, allowed),
        template_id=tmpl.id,
        template_version=tmpl.version,
    )
