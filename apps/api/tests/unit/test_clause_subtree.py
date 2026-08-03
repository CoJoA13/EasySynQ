"""R63 unit proofs — the shared clause-subtree membership predicate.

Both anchors are pinned here because the real seeded catalog cannot falsify them: its 83 numbers
contain no ambiguous prefix pair (no ``9.22`` beside ``9.2``), so a de-anchored regression would
stay green against seed-backed integration tests.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import aliased

from easysynq_api.db.models.clause import Clause
from easysynq_api.services.common.clause_subtree import clause_subtree_on, code_in_subtree

pytestmark = pytest.mark.unit


def test_code_in_subtree_truth_table() -> None:
    assert code_in_subtree("9.2", "9.2") is True  # itself
    assert code_in_subtree("9.2", "9.2.2") is True  # child
    assert code_in_subtree("9.2", "9.2.2.1") is True  # any depth
    assert code_in_subtree("9.2", "9.22") is False  # the dot anchor — NOT a descendant
    assert code_in_subtree("9.2", "9.20") is False
    assert code_in_subtree("9.2", "9") is False  # never ancestors
    assert code_in_subtree("9.2", "9.3") is False  # never siblings
    assert code_in_subtree("1", "10.2") is False  # the classic 1-vs-10 trap


def test_clause_subtree_on_sql_is_dot_anchored() -> None:
    """The compiled JOIN condition concatenates '.%' onto the star's number — a bare 'N%' would
    make clause 1 match 10 (the S-clause-rollup precedent pin)."""
    member = aliased(Clause)
    sql = str(clause_subtree_on(Clause, member).compile(compile_kwargs={"literal_binds": True}))
    assert "'.%'" in sql  # the dot rides with the wildcard suffix
    assert "LIKE" in sql
    assert ".id =" in sql or "id =" in sql  # the self arm survives
