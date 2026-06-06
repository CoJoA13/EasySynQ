"""pack_scope_kind ADD VALUE FINDING/CAPA — evidence-pack Finding/CAPA scope (S-aud-capa-pack)

Close the Audits/CAPA family: extend ``pack_scope_kind`` (created by 0025 with CLAUSE/PROCESS) with
FINDING and CAPA so an Evidence Pack (UJ-7, doc 06 §7.1) can be scoped to one or more audit findings
or CAPAs. A FINDING/CAPA pack resolves the records linked AS EVIDENCE to the finding / the CAPA's
stages and bundles a synthesized, content-hash-sealed *dossier* (the finding's fields + the CAPA's
full stage trail + the e-signatures) so an auditor can "prove this NC was closed effectively".

This is a pure, additive ``ALTER TYPE … ADD VALUE`` migration — NO tables, columns, rows, or new
GRANTs (the EvidenceForTargetType.FINDING/CAPA_STAGE link targets were already live at S-aud-2 /
S-capa-3; pack creation/download ride the existing report.evidence_pack.generate / report.export keys,
so the permission catalog stays CLOSED). The two new members are declared in
db/models/_pack_enums.PackScopeKind too, so a from-scratch ``upgrade head`` rebuilds the type from
PACK_SCOPE_KIND_VALUES identically. None of the new values is used by a row in this migration → the
PG16 same-transaction rule holds. ``ALTER TYPE … ADD VALUE`` is irreversible in PostgreSQL, so the
downgrade is a no-op (the 0001 DROP / re-add-IF-NOT-EXISTS convention). Round-trips up↔down↔check on
PG16 (a pure enum-add perturbs no autogenerate diff — alembic check stays clean).

Revision ID: 0039_pack_finding_capa_scope
Revises: 0038_capa_action_plan_approval
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039_pack_finding_capa_scope"
down_revision: str | None = "0038_capa_action_plan_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_SCOPE_KINDS = ("FINDING", "CAPA")


def upgrade() -> None:
    for value in _NEW_SCOPE_KINDS:
        op.execute(f"ALTER TYPE pack_scope_kind ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # ALTER TYPE … ADD VALUE is irreversible in PostgreSQL → no-op (0001's downgrade DROPs the type
    # wholesale; a re-upgrade re-adds via ADD VALUE IF NOT EXISTS).
    pass
