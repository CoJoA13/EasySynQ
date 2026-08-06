"""Dev live-smoke helper: grant SYSTEM permission overrides to EVERY user in an org and
print the documents + DCRs so a Chrome-MCP smoke has the affordances + data it needs.

Run it by PIPING into the worker container (it has the app code + the DB at host `postgres`). The
organization code is required explicitly through ``EASYSYNQ_SMOKE_ORG``:

    export EASYSYNQ_SMOKE_ORG='ORG_EXAMPLE'
    MSYS_NO_PATHCONV=1 docker compose --env-file .env \
      -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
      exec -T -e EASYSYNQ_SMOKE_ORG worker sh -c "cd /app; uv run python -" \
      < scripts/grant-overrides.py

Grants to ALL org users (not just the demo row) to dodge the re-created-JIT-row trap: the
live Keycloak login JIT-creates a fresh app_user, so the override must land on whichever row
matches the current subject. Reuses the seed_personas operator internals (NOT app logic) and
is idempotent (re-run = no-op). It NEVER mutates the vault — only authz overrides.

Edit ``KEYS`` below for a different slice's smoke; never bake an installation's org code into this
tracked helper.
"""

from __future__ import annotations

import os

from easysynq_api.cli.seed_personas import _ensure_system_overrides, _resolve_org
from easysynq_api.config import get_settings
from easysynq_api.db.models.app_user import AppUser
from easysynq_api.db.models.dcr import Dcr
from easysynq_api.db.models.documented_information import DocumentedInformation
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

# --- edit these per slice -------------------------------------------------------------
KEYS = (
    "changeRequest.read",
    "changeRequest.create",
    "changeRequest.assess",
    "changeRequest.route",
    "changeRequest.implement",
    "changeRequest.close",
    "document.read",
)
# --------------------------------------------------------------------------------------


def main() -> None:
    org_code = os.environ.get("EASYSYNQ_SMOKE_ORG", "").strip()
    if not org_code:
        raise SystemExit("EASYSYNQ_SMOKE_ORG is required (the target org short_code)")
    engine = create_engine(get_settings().sync_dsn)
    try:
        with Session(engine) as s:
            org = _resolve_org(s, org_code)
            print(f"ORG {org.short_code} {org.id}")
            for u in s.scalars(select(AppUser).where(AppUser.org_id == org.id)).all():
                added = _ensure_system_overrides(s, u, KEYS)
                print(
                    f"OVERRIDE {u.display_name} <{str(u.keycloak_subject)[:12]}> "
                    f"-> {added or 'present'}"
                )
            s.commit()

            print("=== EFFECTIVE DOCUMENTS (targets for a REVISE/RETIRE DCR) ===")
            for d in s.scalars(select(DocumentedInformation)).all():
                kind = getattr(d.kind, "value", d.kind)
                state = getattr(d.current_state, "value", d.current_state)
                if str(kind) == "DOCUMENT" and str(state) == "Effective":
                    print(f"DOC {d.identifier} | {getattr(d, 'title', '')} | id={d.id}")

            print("=== DCRS ===")
            for d in s.scalars(select(Dcr)).all():
                print(
                    f"DCR {d.identifier} | state={getattr(d.state, 'value', d.state)} "
                    f"| type={getattr(d.change_type, 'value', d.change_type)} | id={d.id}"
                )
    finally:
        engine.dispose()


main()
