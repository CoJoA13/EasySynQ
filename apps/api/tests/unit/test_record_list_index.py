"""The records candidate-page model index matches the deterministic SQL ordering."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from easysynq_api.db.models.record import Record

_RECORD_PAGE_INDEX = "ix_record_org_id_captured_at_id_desc"


def test_record_page_index_matches_tenant_and_descending_keyset_order() -> None:
    index = next(
        (
            candidate
            for candidate in Record.__table__.indexes
            if candidate.name == _RECORD_PAGE_INDEX
        ),
        None,
    )

    assert index is not None
    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    assert ddl.endswith("ON record (org_id, captured_at DESC, id DESC)")
