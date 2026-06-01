"""The approval-workflow use-case layer (slice S5): instantiation + the decision trigger."""

from .service import decide, instantiate_approval

__all__ = ["decide", "instantiate_approval"]
