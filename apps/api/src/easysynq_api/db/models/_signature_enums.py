"""Native-PG enum bindings for ``signature_event`` (slice S5, doc 14 §8, register R2).

The append-only Part-11-shaped record of an approval/release/obsolete decision. The ``meaning``
type declares all ten values — only the first eight are emitted in v1; ``authored`` /
``responsibility`` are reserved for the future Part-11 phase (declared but never emitted, D3).
``method`` carries the v1 session-assurance values ``app_click`` / ``SESSION`` (v1 emits
``SESSION`` — the value the doc 15 §8.8 decision response returns) plus the reserved step-up
methods. ``signed_object_type`` is polymorphic over the signed entity (doc 14 §8 governs the shape
over doc 18 §15.4's typed-FK form). Created by the Alembic migration; ``create_type=False`` here.
"""

from __future__ import annotations

import enum

from sqlalchemy import Enum as SAEnum


class SignatureMeaning(enum.Enum):
    # v1, emitted (verbatim order — register R2)
    review = "review"
    approval = "approval"
    release = "release"
    obsolete = "obsolete"
    verify = "verify"
    disposition = "disposition"
    import_baseline = "import_baseline"
    review_confirmed = "review_confirmed"
    # reserved for the Part-11 phase — declared, never emitted in v1 (D3)
    authored = "authored"
    responsibility = "responsibility"


class SignatureMethod(enum.Enum):
    app_click = "app_click"
    SESSION = "SESSION"
    # reserved step-up methods (Part-11) — declared, never emitted in v1
    password_reauth = "password_reauth"  # noqa: S105 — enum label, not a credential
    mfa_totp = "mfa_totp"
    mfa_webauthn = "mfa_webauthn"


class SignedObjectType(enum.Enum):
    document_version = "document_version"
    record = "record"
    capa_stage = "capa_stage"
    dcr = "dcr"  # S-dcr-4: a DCR approval signature (per-approver, signed_object_id = the DCR id)
    # S-improvement-4: a signed Top-Management authorization of an Improvement Initiative
    # (signed_object_id = the Closed stage-event row id; meaning='verify') — the own-table analogue
    # of capa_stage. Added via ``ALTER TYPE signed_object_type ADD VALUE`` in 0053.
    improvement_initiative_stage_event = "improvement_initiative_stage_event"


def _vals(e: type[enum.Enum]) -> list[str]:
    return [m.value for m in e]


signature_meaning_enum = SAEnum(
    SignatureMeaning, name="signature_meaning", values_callable=_vals, create_type=False
)
signature_method_enum = SAEnum(
    SignatureMethod, name="signature_method", values_callable=_vals, create_type=False
)
signed_object_type_enum = SAEnum(
    SignedObjectType, name="signed_object_type", values_callable=_vals, create_type=False
)
