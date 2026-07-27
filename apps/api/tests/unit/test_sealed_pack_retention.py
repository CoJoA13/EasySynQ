"""M7 proofs for the reserved sealed-pack retention-policy identity."""

from __future__ import annotations

import uuid

from easysynq_api.services.records.repository import sealed_pack_policy_id


def test_sealed_pack_policy_id_is_stable_and_org_scoped() -> None:
    org = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert sealed_pack_policy_id(org) == uuid.UUID("76bf6c33-82bf-1a0c-391a-c5e17598094e")
    assert sealed_pack_policy_id(uuid.UUID(int=2)) != sealed_pack_policy_id(uuid.UUID(int=3))
