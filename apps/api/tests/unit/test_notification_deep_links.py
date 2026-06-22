"""deep_link_for maps every slice-1 subject type, with a /tasks fallback (refute L5-2)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.services.notifications.subjects import deep_link_for

pytestmark = pytest.mark.unit

_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_document_link() -> None:
    assert deep_link_for("DOCUMENT", _ID).endswith(f"/documents/{_ID}")


def test_capa_link() -> None:
    assert deep_link_for("CAPA", _ID).endswith(f"/capa?capa={_ID}")


def test_unknown_subject_falls_back_to_tasks() -> None:
    assert deep_link_for("SOMETHING_NEW", _ID).endswith("/tasks")


def test_leadership_and_periodic_have_links() -> None:
    # Both are real engine subject types (refute L5-2) — must not be a broken/empty link.
    assert deep_link_for("LEADERSHIP_AUTHORIZATION", _ID)
    assert deep_link_for("PERIODIC_REVIEW", _ID)


def test_dcr_link() -> None:
    # DCR drawer opens via /dcrs?dcr=<id> query param
    assert deep_link_for("DCR", _ID).endswith(f"/dcrs?dcr={_ID}")


def test_improvement_initiative_link() -> None:
    assert deep_link_for("IMPROVEMENT_INITIATIVE", _ID).endswith(f"/improvement?initiative={_ID}")


def test_mgmt_review_link() -> None:
    assert deep_link_for("MGMT_REVIEW", _ID).endswith(f"/management-reviews/{_ID}")


def test_doc_ack_link() -> None:
    # DOC_ACK resolves against the underlying document
    assert deep_link_for("DOC_ACK", _ID).endswith(f"/documents/{_ID}")
