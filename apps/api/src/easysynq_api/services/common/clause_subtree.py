"""R63 clause-subtree membership — the ONE predicate the ★ coverage computations share.

A ★ clause's coverage counts documents mapped to the clause itself OR any DESCENDANT — the
``'.'``-anchored prefix admits children only, never siblings (``9.22`` is not under ``9.2``);
catalog-trusted clause numbers carry no LIKE metacharacters. Shared by the compliance checklist
(``services/reports/checklist.py``) and the §7.3 obsoletion-safety gate
(``services/vault/obsoletion.py``) so the gate and the report can never disagree about what
covers a ★ clause (the drift the R63 diff-critic review caught).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement


def clause_subtree_on(star: Any, member: Any) -> ColumnElement[bool]:
    """The JOIN condition making ``member`` a subtree member of ``star`` (itself or a descendant).

    ``star``/``member`` are the ``Clause`` entity or ``aliased(Clause)`` instances — both sides of
    a self-join over the same framework's catalog."""
    return sa.and_(
        member.framework_id == star.framework_id,
        sa.or_(member.id == star.id, member.number.like(star.number + ".%")),
    )


def code_in_subtree(star_number: str, code: str) -> bool:
    """Python-side subtree membership for projected clause CODES (the import projection leg)."""
    return code == star_number or code.startswith(star_number + ".")
