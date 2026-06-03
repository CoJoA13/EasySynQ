"""Pure, DB-free records domain helpers (slice S-rec-1, doc 06).

``content_hash`` seals a captured record's immutable identity; ``retention`` resolves the applicable
retention policy by doc-06 §5.1 precedence. Both are pure (no DB/IO) so unit tests drive them with
hand-built inputs — the PDP / ``mirror._placement_dirs`` style.
"""
