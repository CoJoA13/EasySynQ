"""Version-diff use-case layer (slice S-dcr-3a) — doc 05 §8 redline (metadata + text)."""

from __future__ import annotations

from .extractor import TextExtractor, get_text_extractor, set_text_extractor
from .service import build_version_diff
from .visual import build_visual_diff, get_or_create_visual_diff, get_visual_diff

__all__ = [
    "TextExtractor",
    "build_version_diff",
    "build_visual_diff",
    "get_or_create_visual_diff",
    "get_text_extractor",
    "get_visual_diff",
    "set_text_extractor",
]
