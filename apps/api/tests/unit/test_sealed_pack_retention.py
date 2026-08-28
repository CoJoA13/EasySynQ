"""M7 proofs for the reserved sealed-pack retention-policy identity."""

from __future__ import annotations

import uuid

from easysynq_api.services.records.repository import sealed_pack_policy_id


def test_sealed_pack_policy_id_is_stable_and_org_scoped() -> None:
    org = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert sealed_pack_policy_id(org) == uuid.UUID("76bf6c33-82bf-1a0c-391a-c5e17598094e")
    assert sealed_pack_policy_id(uuid.UUID(int=2)) != sealed_pack_policy_id(uuid.UUID(int=3))


def test_import_report_policy_id_is_stable_org_scoped_and_distinct() -> None:
    """[Audit U7] The managed Import Report policy id: deterministic per org, distinct across
    orgs, and never colliding with the sealed-pack managed id for the same org."""
    from easysynq_api.services.records.repository import import_report_policy_id

    org = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert import_report_policy_id(org) == uuid.UUID("b398109f-8a2f-70d6-70d3-a8bfd4f6fea9")
    assert import_report_policy_id(uuid.UUID(int=2)) != import_report_policy_id(uuid.UUID(int=3))
    assert import_report_policy_id(org) != sealed_pack_policy_id(org)
