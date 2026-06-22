"""Dev live-smoke seed for S-notify-fe: grant document.read to all org-AHT users (so a
notification's /documents/{id} deep-link lands on a viewable page) and seed a few in-app
notification rows per user (operational, NOT vault/WORM). Idempotent via a context.smoke
guard (the app role has no DELETE on `notification`, so we never delete — we skip if seeded).

Pipe into the worker container:

    MSYS_NO_PATHCONV=1 docker compose --env-file .env \
      -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
      exec -T worker sh -c "cd /app; uv run python -" < scripts/seed-notify-smoke.py
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from easysynq_api.cli.seed_personas import _ensure_system_overrides, _resolve_org
from easysynq_api.config import get_settings
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.db.models.notification import Notification

ORG = "AHT"
KEYS = ("document.read",)  # so a /documents/{id} deep-link lands on a real page


def main() -> None:
    engine = create_engine(get_settings().sync_dsn)
    now = dt.datetime.now(dt.UTC)
    try:
        with Session(engine) as s:
            org = _resolve_org(s, ORG)
            print(f"ORG {org.short_code} {org.id}")

            # Effective DOCUMENT rows → real deep-link targets.
            docs = [
                d
                for d in s.scalars(select(DocumentedInformation)).all()
                if str(getattr(d.kind, "value", d.kind)) == "DOCUMENT"
                and str(getattr(d.current_state, "value", d.current_state)) == "Effective"
            ]
            for d in docs[:5]:
                print(f"DOC {d.identifier} | {getattr(d, 'title', '')} | id={d.id}")
            d1 = docs[0] if docs else None
            d2 = docs[1] if len(docs) > 1 else d1

            def link(doc: object | None) -> tuple[str, object, str, str]:
                if doc is None:
                    return ("http://localhost/tasks", None, "a task", "")
                return (
                    f"http://localhost/documents/{doc.id}",
                    doc.id,
                    doc.identifier,
                    getattr(doc, "title", "") or "",
                )

            l1 = link(d1)
            l2 = link(d2)

            users = list(s.scalars(select(AppUser).where(AppUser.org_id == org.id)).all())
            for u in users:
                added = _ensure_system_overrides(s, u, KEYS)
                # Idempotency: skip if this user already has smoke rows.
                existing = s.scalars(
                    select(Notification).where(
                        Notification.recipient_user_id == u.id,
                        Notification.context["smoke"].as_boolean().is_(True),
                    )
                ).first()
                if existing is not None:
                    print(f"SKIP {u.display_name} (already seeded) [override {added or 'present'}]")
                    continue
                rows = [
                    Notification(
                        org_id=org.id,
                        recipient_user_id=u.id,
                        event_key="task.assigned",
                        subject_type="DOCUMENT",
                        subject_id=l1[1],
                        title=f"Review requested: {l1[2]}",
                        body=f'You have been assigned a review of "{l1[3]}".',
                        deep_link=l1[0],
                        context={"smoke": True},
                        created_at=now,
                    ),
                    Notification(
                        org_id=org.id,
                        recipient_user_id=u.id,
                        event_key="task.assigned",
                        subject_type="DOC_ACK",
                        subject_id=None,
                        title="Acknowledgement required",
                        body="Please read and acknowledge the latest effective version.",
                        deep_link="http://localhost/tasks",
                        context={"smoke": True},
                        created_at=now - dt.timedelta(hours=2),
                    ),
                    Notification(
                        org_id=org.id,
                        recipient_user_id=u.id,
                        event_key="task.assigned",
                        subject_type="DOCUMENT",
                        subject_id=l2[1],
                        title=f"Document released: {l2[2]}",
                        body=f'A new effective version of "{l2[3]}" is available.',
                        deep_link=l2[0],
                        context={"smoke": True},
                        created_at=now - dt.timedelta(days=1),
                        read_at=now - dt.timedelta(hours=20),
                    ),
                ]
                s.add_all(rows)
                print(f"SEED {u.display_name} +3 (2 unread, 1 read) [override {added or 'present'}]")
            s.commit()
            print("DONE")
    finally:
        engine.dispose()


main()
