"""Pure where-used projection (slice S-dcr-2; doc 05 §7.1/§7.2). No I/O — fully unit-testable.

:func:`bucket_links` maps the raw ``document_link`` rows (from
``services.dcr.repository.list_document_links`` — each a dict with ``link_type`` ∈
{parent_of,child_of,references,supersedes}, ``direction`` ∈ {outbound,inbound}, + the neighbour
document's id/identifier/title/state/level) into the doc 05 §7.2 categories. Directionality is
resolved deterministically: a ``parent_of`` link from A→B means B is A's child, so for the subject A
its OUTBOUND parent_of neighbour is a child; the INBOUND parent_of neighbour is a parent (and
``child_of`` is the mirror). ``references`` outbound to an ``L4_FORM`` document is surfaced under
``forms_templates`` (§7.2). The service composes these buckets with processes / records / clauses /
caused-by + the obsoletion check into the full where-used response.
"""

from __future__ import annotations

from typing import Any

_FORM_LEVEL = "L4_FORM"


def bucket_links(links: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bucket raw ``document_link`` rows into the doc 05 §7.2 document-relationship categories."""
    out: dict[str, list[dict[str, Any]]] = {
        "child_documents": [],
        "parent_documents": [],
        "referenced_by": [],
        "references_out": [],
        "forms_templates": [],
        "supersedes": [],
        "superseded_by": [],
    }
    for link in links:
        lt = link["link_type"]
        outbound = link["direction"] == "outbound"
        if lt == "parent_of":
            out["child_documents" if outbound else "parent_documents"].append(link)
        elif lt == "child_of":
            out["parent_documents" if outbound else "child_documents"].append(link)
        elif lt == "references":
            if not outbound:
                out["referenced_by"].append(link)
            elif link.get("document_level") == _FORM_LEVEL:
                out["forms_templates"].append(link)
            else:
                out["references_out"].append(link)
        elif lt == "supersedes":
            out["supersedes" if outbound else "superseded_by"].append(link)
    return out
