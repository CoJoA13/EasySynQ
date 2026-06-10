"""Unit tests for the additive ``context_users`` assignee resolution (S-drift-1)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.services.workflow.engine import _context_user_ids

pytestmark = pytest.mark.unit


def test_resolves_a_single_context_user() -> None:
    uid = uuid.uuid4()
    assert _context_user_ids({"owner_user_id": str(uid)}, "owner_user_id") == [uid]


def test_resolves_a_list_of_context_users() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    assert _context_user_ids({"reviewers": [str(a), str(b)]}, "reviewers") == [a, b]


def test_missing_key_resolves_empty_fail_closed() -> None:
    assert _context_user_ids({}, "owner_user_id") == []
    assert _context_user_ids(None, "owner_user_id") == []


def test_malformed_values_are_skipped() -> None:
    uid = uuid.uuid4()
    assert _context_user_ids({"x": ["not-a-uuid", str(uid), None]}, "x") == [uid]
