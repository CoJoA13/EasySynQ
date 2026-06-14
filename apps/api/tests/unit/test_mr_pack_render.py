from __future__ import annotations

import io

from pypdf import PdfReader

from easysynq_api.domain.mgmt_review.pack_render import render_minutes_pdf


def _args(**over):
    base = dict(
        identifier="MR-GEN-003",
        title="Annual Management Review 2026",
        current_state="Effective",
        close_state="ActionsTracked",
        revision_label="1.0",
        effective_from="2026-06-14T00:00:00+00:00",
        version_id="11111111-1111-1111-1111-111111111111",
        source_digest="abc123def456",
        minutes={
            "period_label": "FY2026",
            "review_date": "2026-06-10",
            "attendees": [{"name": "Mara QM", "role": "Quality Manager", "user_id": "u-mara"}],
            "inputs": [
                {
                    "input_type": "OBJECTIVES_STATUS",
                    "available": True,
                    "source_ref": {"on_track": 4, "at_risk": 1},
                    "position": 1,
                },
                {
                    "input_type": "AUDIT_RESULTS",
                    "available": False,
                    "source_ref": None,
                    "position": 2,
                },
            ],
            "outputs": [
                {
                    "output_type": "ACTION",
                    "description": "Re-baseline the supplier KPI.",
                    "owner_user_id": "u-diego",
                    "due_date": "2026-09-30",
                },
                {
                    "output_type": "DECISION",
                    "description": "Approve the 2027 objectives.",
                    "owner_user_id": None,
                    "due_date": None,
                },
            ],
            "compiled_at": "2026-06-10T09:00:00+00:00",
        },
        name_of={"u-diego": "Diego PO", "u-mara": "Mara QM"},
        signatures=[
            {
                "signer": "Ken Approver",
                "meaning": "approval",
                "when": "2026-06-12T10:00:00+00:00",
                "method": "SESSION",
            },
            {
                "signer": None,
                "meaning": "release",
                "when": "2026-06-14T00:00:00+00:00",
                "method": "SESSION",
            },
        ],
    )
    base.update(over)
    return base


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)


def test_render_produces_pdf_with_minutes_content():
    pdf = render_minutes_pdf(**_args())
    assert pdf[:4] == b"%PDF"
    text = _text(pdf)
    assert "MR-GEN-003" in text
    assert "Annual Management Review 2026" in text
    assert "Mara QM" in text
    assert "Re-baseline the supplier KPI." in text
    assert "Diego PO" in text
    assert "Ken Approver" in text
    assert "approval" in text and "release" in text
    assert "abc123def456" in text


def test_render_is_byte_deterministic():
    assert render_minutes_pdf(**_args()) == render_minutes_pdf(**_args())


def test_render_null_signer_shows_system():
    pdf = render_minutes_pdf(**_args())
    assert "system" in _text(pdf)


def test_render_handles_empty_sections():
    args = _args()
    args["minutes"] = {**args["minutes"], "attendees": [], "inputs": [], "outputs": []}
    args["signatures"] = []
    pdf = render_minutes_pdf(**args)
    assert pdf[:4] == b"%PDF"
    assert "none recorded" in _text(pdf).lower()


def test_render_tolerates_odd_source_ref_shapes():
    args = _args()
    args["minutes"] = {
        **args["minutes"],
        "inputs": [
            {"input_type": "X", "available": True, "source_ref": "a bare string", "position": 1},
            {"input_type": "Y", "available": True, "source_ref": ["a", "list"], "position": 2},
            {"input_type": "Z", "available": True, "source_ref": None, "position": 3},
        ],
    }
    pdf = render_minutes_pdf(**args)
    assert pdf[:4] == b"%PDF"
