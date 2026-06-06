"""S-aud-2 unit proofs — the pure audit-finding close-gate predicate (doc 10 §5.3, R39)."""

from __future__ import annotations

import pytest

from easysynq_api.db.models._capa_enums import CapaCloseState
from easysynq_api.db.models._iso_audit_enums import FindingType
from easysynq_api.domain.audits import finding_blocks_close

pytestmark = pytest.mark.unit


def test_finding_type_tokens() -> None:
    assert {t.value for t in FindingType} == {"NC", "OBSERVATION", "OFI"}


def test_live_nc_without_closed_capa_blocks() -> None:
    # Raised / RootCause / Verify / Rejected / no-CAPA — every non-Closed state blocks a live NC.
    for state in (
        CapaCloseState.Raised,
        CapaCloseState.Containment,
        CapaCloseState.RootCause,
        CapaCloseState.ActionPlan,
        CapaCloseState.Implement,
        CapaCloseState.Verify,
        CapaCloseState.Rejected,
        None,
    ):
        assert finding_blocks_close(FindingType.NC, False, state) is True


def test_live_nc_with_closed_capa_passes() -> None:
    assert finding_blocks_close(FindingType.NC, False, CapaCloseState.Closed) is False


def test_superseded_nc_never_blocks() -> None:
    # A corrected (declassified or re-typed) NC is out of the live set regardless of its CAPA state.
    for state in (CapaCloseState.Rejected, CapaCloseState.Raised, None, CapaCloseState.Closed):
        assert finding_blocks_close(FindingType.NC, True, state) is False


def test_observation_and_ofi_never_block() -> None:
    for ft in (FindingType.OBSERVATION, FindingType.OFI):
        for superseded in (True, False):
            for state in (None, CapaCloseState.Rejected, CapaCloseState.Closed):
                assert finding_blocks_close(ft, superseded, state) is False
