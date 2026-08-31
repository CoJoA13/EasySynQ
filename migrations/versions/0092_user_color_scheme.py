"""Give each user an account-level interface colour-scheme preference (R69).

The SPA has shipped ``MantineProvider defaultColorScheme="auto"`` since S-ui-1, but nothing ever
called ``setColorScheme``, so the preference machinery had no writer and the scheme simply followed
the operating system. R69 makes the preference a real, stored choice.

It is stored on the ACCOUNT rather than only in the browser because the SPA keeps its tokens in
memory only — every reload starts logged-out and re-authenticates — so a browser-only preference is
the one thing that survives, while an account-only one is unreadable during the token-less window.
The SPA therefore caches it in ``localStorage`` too and treats this column as the authority; that is
a client concern and needs no schema of its own.

``AUTO`` is a real selectable value, not merely the initial one, so a user who picks LIGHT or DARK
can return to OS-following. No permission key: a user editing their own preference rides the
authentication-only ``/me`` precedent.

The column is added nullable, backfilled, then set NOT NULL, and carries NO ``server_default`` on
either side — an enum default reflects back as ``'AUTO'::color_scheme`` and is a standing
``alembic check`` drift source. This mirrors the existing ``app_user.status`` column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from easysynq_api.db.models.app_user import COLOR_SCHEME_VALUES

revision: str = "0092_user_color_scheme"
down_revision: str | None = "0091_documents_list_index"
branch_labels: str | None = None
depends_on: str | None = None

# Sourced from the ORM tuple rather than retyped — the 0010 precedent.
_COLOR_SCHEME = postgresql.ENUM(*COLOR_SCHEME_VALUES, name="color_scheme", create_type=False)

_DEFAULT = "AUTO"


def upgrade() -> None:
    bind = op.get_bind()
    _COLOR_SCHEME.create(bind, checkfirst=True)

    op.add_column("app_user", sa.Column("color_scheme", _COLOR_SCHEME, nullable=True))
    # Every existing account keeps exactly the behaviour it had before this migration: follow the
    # operating system. A populated database therefore sees no visible change until a user chooses.
    # Explicit CAST rather than PostgreSQL's double-colon cast shorthand: that shorthand collides
    # with SQLAlchemy's own bind-parameter syntax, so text() never registers the bind at all and the
    # migration dies at load with "doesn't define a bound parameter".
    op.execute(
        sa.text(
            "UPDATE app_user SET color_scheme = CAST(:d AS color_scheme) WHERE color_scheme IS NULL"
        ).bindparams(d=_DEFAULT)
    )
    op.alter_column("app_user", "color_scheme", nullable=False)


def downgrade() -> None:
    op.drop_column("app_user", "color_scheme")
    # Drop the type only after its last consumer column is gone.
    _COLOR_SCHEME.drop(op.get_bind(), checkfirst=True)
