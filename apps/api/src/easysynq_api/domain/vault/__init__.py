"""Pure vault domain helpers — identifier + revision-label formatting. No DB, no I/O."""

from .identifier import format_identifier, revision_label

__all__ = ["format_identifier", "revision_label"]
