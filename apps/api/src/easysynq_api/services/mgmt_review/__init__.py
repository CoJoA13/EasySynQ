"""Management Review service (S-mr-1, clause 9.3). ``create_review`` reuses the vault
``create_document`` (kind=DOCUMENT, type MR), auto-maps to clause 9.3, and adds the satellite;
``submit_review_for_review`` freezes the minutes + submits; ``release_review`` rides the generic
release cutover. Reads live in ``repository``; serializers in ``api/mgmt_review``."""

from .compile import compile_inputs
from .repository import (
    get_review,
    get_review_doc,
    list_inputs,
    list_outputs,
    list_reviews,
    open_review_exists,
)
from .service import (
    add_output,
    create_review,
    delete_output,
    release_review,
    submit_review_for_review,
    update_output,
    update_review_meta,
)

__all__ = [
    "add_output",
    "compile_inputs",
    "create_review",
    "delete_output",
    "get_review",
    "get_review_doc",
    "list_inputs",
    "list_outputs",
    "list_reviews",
    "open_review_exists",
    "release_review",
    "submit_review_for_review",
    "update_output",
    "update_review_meta",
]
