"""Shared request-boundary validation types for API payloads."""

from typing import Annotated

from pydantic import StringConstraints

SHA256_HEX_PATTERN = r"^[0-9a-f]{64}$"
type Sha256Hex = Annotated[
    str,
    StringConstraints(
        min_length=64,
        max_length=64,
        pattern=SHA256_HEX_PATTERN,
    ),
]
