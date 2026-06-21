"""Interested Parties register service (clause 4.2, S-interested-parties-1).

NB: ``governing_register`` (the GOVERNING Effective-snapshot read) + ``summarize_register`` land in
S-interested-parties-2 alongside ``GET /interested-parties/summary`` + the MR 9.3.2(b) consumer;
this slice is core + lifecycle only. Every public name is kept in ``__all__`` (the F401 /
sibling-fixture-shadow trap — a test-fn param named ``subj`` shadows an import → ruff strips it)."""

from __future__ import annotations

from .lifecycle import publish_register, start_interested_party_revision
from .service import (
    add_interested_party,
    find_head,
    get_interested_party,
    list_interested_parties,
    resolve_or_create_head,
    update_interested_party_row,
)

__all__ = [
    "add_interested_party",
    "find_head",
    "get_interested_party",
    "list_interested_parties",
    "publish_register",
    "resolve_or_create_head",
    "start_interested_party_revision",
    "update_interested_party_row",
]
