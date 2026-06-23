"""Unit test: deep_link_for routes OBJ/MR document notifications to dedicated surfaces (task 9)."""

from __future__ import annotations

import uuid

import pytest

from easysynq_api.services.notifications.subjects import deep_link_for

pytestmark = pytest.mark.unit


def test_objective_routes_to_objectives_surface() -> None:
    i = uuid.uuid4()
    assert deep_link_for("DOCUMENT", i, document_type_code="OBJ").endswith(f"/objectives/{i}")


def test_mr_routes_to_management_reviews_surface() -> None:
    i = uuid.uuid4()
    assert deep_link_for("DOCUMENT", i, document_type_code="MR").endswith(
        f"/management-reviews/{i}"
    )


def test_plain_document_unchanged() -> None:
    i = uuid.uuid4()
    assert deep_link_for("DOCUMENT", i, document_type_code="POL").endswith(f"/documents/{i}")
    assert deep_link_for("DOCUMENT", i).endswith(f"/documents/{i}")
