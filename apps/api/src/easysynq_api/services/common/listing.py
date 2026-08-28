"""Shared listing bounds (audit U14, the S-web-2 posture).

``REGISTER_SCAN_CAP`` bounds the pre-authz candidate window a register listing loads —
previously /capas, /risks, /improvement-initiatives, and /audits loaded EVERY org row and
per-row authorized it on each request. The cap mirrors the documents list's ``_LIST_SCAN_CAP``
(newest-first, so the window is the most recent slice). A response that hits the cap sets
``truncated: true`` — never a silent cap (the register is audit-facing; silently missing old
rows would read as "covered everything").
"""

from __future__ import annotations

from typing import Final

REGISTER_SCAN_CAP: Final = 2000
