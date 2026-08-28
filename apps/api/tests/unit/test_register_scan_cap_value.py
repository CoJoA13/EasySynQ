"""[Audit U14] Pin the SHIPPED scan-window default.

The integration tests monkeypatch ``REGISTER_SCAN_CAP`` to exercise the at-cap behaviour, so an
absurd shipped value (or a silent removal of the bound) passes every one of them. This unit line
is the only thing that pins the default the registers actually run with.
"""

from __future__ import annotations

from easysynq_api.services.common import listing


def test_register_scan_cap_default() -> None:
    assert listing.REGISTER_SCAN_CAP == 2000
