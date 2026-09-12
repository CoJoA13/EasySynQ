"""Shared legacy verifier contract without database or application settings imports."""

from __future__ import annotations

import datetime
from typing import Any, Protocol


class LegacySignatureVerifier(Protocol):
    def __call__(
        self,
        *,
        org_id: Any,
        latest_id: int,
        latest_row_hash: bytes,
        timestamp: datetime.datetime,
        signature: bytes | None,
    ) -> bool: ...
