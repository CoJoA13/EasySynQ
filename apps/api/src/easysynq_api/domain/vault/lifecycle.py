"""The document-lifecycle finite-state machine — a pure function (no DB, no I/O), so the
acceptance proofs can drive it with hand-built inputs (the PDP pattern, doc 18 §5.2).

The machine is keyed on the **document** ``current_state`` (the 7-state headline a user reasons
about), because that is what determines which action is legal. Each legal transition also says
which version-state the *subject version* must be in (``from_version_state``) and the new
version-state (``to_version_state``); ``None`` means there is no version subject (``start_revision``
opens a new draft while the Effective version keeps governing — a *version* is never literally
``UnderRevision``, register R1/C1).

MVP ships **T1-T4, T6, T7, T9-T12**; **T5** (rescind-approval) and **T8** (discard-draft) are
deferred to v1 (doc 18 §11 D-5) — so they are simply absent from the table and raise as illegal.
Two transitions are not document-state-keyed and so live outside this table: **T10**
(Effective → Superseded) is applied to the *prior* Effective version inside the release cutover; and
**T12** (Superseded → Obsolete) is a version-level archive (a document's ``current_state`` is never
``Superseded`` — versions are superseded while the document stays Effective via the newer version),
handled as a documented special case in the ``obsolete`` service when a Superseded ``version_id``
is supplied.
"""

from __future__ import annotations

import dataclasses
import enum

from ...db.models._vault_enums import DocumentCurrentState, VersionState

V = VersionState
D = DocumentCurrentState


class Action(enum.Enum):
    """A lifecycle action — one per named POST sub-resource on ``/documents/{id}``."""

    submit_review = "submit_review"
    approve = "approve"
    request_changes = "request_changes"
    release = "release"
    start_revision = "start_revision"
    obsolete = "obsolete"


@dataclasses.dataclass(frozen=True, slots=True)
class Transition:
    """The effect of a legal transition.

    ``from_version_state`` is the state the subject version must currently be in (the service uses
    it to locate/validate that version); ``to_version_state`` is its new state. Both are ``None``
    for ``start_revision`` (no version changes state — a new working draft is opened instead).
    """

    action: Action
    from_version_state: VersionState | None
    to_version_state: VersionState | None
    to_doc_state: DocumentCurrentState


class IllegalTransition(Exception):
    """Raised when an action is not legal from the document's current state. ``allowed`` lists the
    action names legal from here — surfaced to the client as ``409`` ``allowed_transitions``."""

    def __init__(self, action: Action, doc_state: DocumentCurrentState, allowed: list[str]) -> None:
        self.action = action
        self.doc_state = doc_state
        self.allowed = allowed
        super().__init__(
            f"{action.value} is not legal from current_state={doc_state.value}; "
            f"allowed: {allowed or '(none — terminal state)'}"
        )


# (document current_state, action) -> Transition. The single source of truth for the FSM.
_TABLE: dict[tuple[DocumentCurrentState, Action], Transition] = {
    (D.Draft, Action.submit_review): Transition(  # T2
        Action.submit_review, V.Draft, V.InReview, D.InReview
    ),
    (D.UnderRevision, Action.submit_review): Transition(  # T9
        Action.submit_review, V.Draft, V.InReview, D.InReview
    ),
    (D.InReview, Action.approve): Transition(  # T4
        Action.approve, V.InReview, V.Approved, D.Approved
    ),
    (D.InReview, Action.request_changes): Transition(  # T3
        Action.request_changes, V.InReview, V.Draft, D.Draft
    ),
    (D.Approved, Action.release): Transition(  # T6 (+ T10 on the prior version, in the cutover)
        Action.release, V.Approved, V.Effective, D.Effective
    ),
    (D.Effective, Action.start_revision): Transition(  # T7
        Action.start_revision, None, None, D.UnderRevision
    ),
    (D.Effective, Action.obsolete): Transition(  # T11
        Action.obsolete, V.Effective, V.Obsolete, D.Obsolete
    ),
    # T12 (Superseded version → Obsolete) is version-level, not document-state-keyed — handled in
    # the obsolete service when a Superseded version_id is supplied (doc current_state unchanged).
}


def allowed_actions(doc_state: DocumentCurrentState) -> list[str]:
    """The action names legal from ``doc_state`` (stable sorted), for the 409 payload."""
    return sorted(a.value for (ds, a) in _TABLE if ds == doc_state)


def apply_transition(doc_state: DocumentCurrentState, action: Action) -> Transition:
    """Resolve ``(doc_state, action)`` to its :class:`Transition`, or raise
    :class:`IllegalTransition` (deny-by-default: anything not in the table is illegal)."""
    transition = _TABLE.get((doc_state, action))
    if transition is None:
        raise IllegalTransition(action, doc_state, allowed_actions(doc_state))
    return transition
