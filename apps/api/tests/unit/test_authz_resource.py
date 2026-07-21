"""#333: the shared document ResourceContext builder populates the FULL scope tuple.

Pure unit proofs (no DB) that ``resource_from_doc`` sets every selector ``pdp._matches_scope``
compares for a document — in particular ``framework_id`` (FRAMEWORK scope) and ``kind`` (DOC_CLASS
scope), which the canonical builder previously omitted so a scoped DENY was silently dropped
(deny-always-wins violated). Each deny-wins proof is mutation-distinguishing: blanking the field
(the pre-#333 canonical-builder shape) flips DENY -> ALLOW.
"""

from __future__ import annotations

import dataclasses
import datetime
import uuid

import pytest

from easysynq_api.db.models._vault_enums import DocumentCurrentState, DocumentKind
from easysynq_api.db.models.documented_information import DocumentedInformation
from easysynq_api.domain.authz import (
    Effect,
    RequestContext,
    ResolvedGrant,
    ScopeLevel,
    authorize,
)
from easysynq_api.services.authz.resource import resource_from_doc

pytestmark = pytest.mark.unit

_CTX = RequestContext(now=datetime.datetime(2026, 6, 1, 12, 0, tzinfo=datetime.UTC))


def _doc(
    *, framework_id: uuid.UUID, kind: DocumentKind = DocumentKind.DOCUMENT
) -> DocumentedInformation:
    """A detached DocumentedInformation carrying only the attributes the builder reads (never
    flushed — no session, no NOT-NULL constraint check)."""
    return DocumentedInformation(
        id=uuid.uuid4(),
        framework_id=framework_id,
        kind=kind,
        current_state=DocumentCurrentState.Draft,
        folder_path="SOPs.Purchasing",
    )


def _grant(effect: Effect, level: ScopeLevel, selector: dict[str, object]) -> ResolvedGrant:
    return ResolvedGrant(
        effect=effect, level=level, selector=selector, predicates={}, source="test"
    )


def test_resource_from_doc_populates_full_scope_tuple() -> None:
    fw = uuid.uuid4()
    doc = _doc(framework_id=fw)
    r = resource_from_doc(doc, document_level="L2_PROCEDURE", process_ids=frozenset({"purchasing"}))
    assert r.artifact_id == str(doc.id)
    assert r.folder_path == "SOPs.Purchasing"
    assert r.document_level == "L2_PROCEDURE"
    assert r.kind == "DOCUMENT"
    assert r.framework_id == str(fw)
    assert r.lifecycle_state == DocumentCurrentState.Draft.value
    assert r.process_ids == frozenset({"purchasing"})
    # concrete_type stays unset (unimplemented; #345) unless a caller supplies it.
    assert r.concrete_type is None


def test_framework_scoped_deny_wins_over_broad_system_allow() -> None:
    """A FRAMEWORK-scoped document.read DENY + a broad SYSTEM ALLOW -> DENY, but ONLY because the
    resource carries framework_id. Blanking it (the pre-#333 canonical-builder shape) drops the DENY
    and the ALLOW wins — the mutation this fix closes."""
    fw = uuid.uuid4()
    resource = resource_from_doc(
        _doc(framework_id=fw), document_level="L2_PROCEDURE", process_ids=frozenset()
    )
    grants = [
        _grant(Effect.ALLOW, ScopeLevel.SYSTEM, {}),
        _grant(Effect.DENY, ScopeLevel.FRAMEWORK, {"framework_id": str(fw)}),
    ]
    assert authorize(grants, "document.read", resource, _CTX).allow is False  # deny wins

    old_shape = dataclasses.replace(resource, framework_id=None)
    assert authorize(grants, "document.read", old_shape, _CTX).allow is True  # DENY dropped


def test_kind_scoped_doc_class_deny_wins_over_broad_system_allow() -> None:
    """Same shape for a DOC_CLASS DENY narrowed by ``kind``: it needs ``resource.kind`` to match."""
    resource = resource_from_doc(
        _doc(framework_id=uuid.uuid4(), kind=DocumentKind.DOCUMENT),
        document_level="L2_PROCEDURE",
        process_ids=frozenset(),
    )
    grants = [
        _grant(Effect.ALLOW, ScopeLevel.SYSTEM, {}),
        _grant(
            Effect.DENY,
            ScopeLevel.DOC_CLASS,
            {"document_level": "L2_PROCEDURE", "kind": "DOCUMENT"},
        ),
    ]
    assert authorize(grants, "document.read", resource, _CTX).allow is False  # deny wins

    old_shape = dataclasses.replace(resource, kind=None)
    assert authorize(grants, "document.read", old_shape, _CTX).allow is True  # DENY dropped


def test_concrete_type_threaded_when_supplied() -> None:
    """concrete_type is deferred (#345) so it defaults None, but the shared helper threads it so the
    single completion point works once a source is defined."""
    r = resource_from_doc(
        _doc(framework_id=uuid.uuid4()),
        document_level="L2_PROCEDURE",
        process_ids=frozenset(),
        concrete_type="Procedure",
    )
    assert r.concrete_type == "Procedure"
