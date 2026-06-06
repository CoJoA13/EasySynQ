"""Synthesized evidence-pack dossier — the PURE serializers + the sealed digest (S-aud-capa-pack).

A FINDING/CAPA evidence pack bundles, beyond the records linked as evidence, a synthesized JSON
*dossier* per scope subject so an external auditor can "prove this NC was closed effectively"
(doc 06 §7.1/§7.3) from the ZIP alone: the finding's fields + correction chain + linked CAPA, and
the CAPA's full append-only stage trail (RootCause → ActionPlan → Verify) with each stage's
e-signature metadata. The dossier is sealed (``dossier_digest`` folds into the v2 content hash).

This module is PURE (no DB, no I/O):

* ``project_user`` is the SOLE user-shaping function — it emits ONLY ``{user_id, display_name}``. A
  pack ZIP is externally shareable (the Ed25519 guest link), so a signer/creator ``email`` /
  ``keycloak_subject`` / status flag MUST NOT land in the dossier; routing every attribution through
  ``project_user`` makes that boundary structural, not a discipline.
* ``canonical_dossier_bytes`` is the on-disk per-subject file serialization (deterministic pretty
  JSON), shared so the build and any re-verifier agree byte-for-byte.
* ``dossier_digest`` seals the aggregate over the SORTED per-file sha256s (the same shas listed in
  ``manifest["dossier"]["files"]``) under a domain-separated preamble — reconstructable from the ZIP
  alone (the pack stays self-verifying per doc 06 §7.4).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from typing import Any

import rfc8785

# An evidence reference as passed from the service: (record_id, human_identifier). The id may be a
# UUID (the repo returns UUIDs) or a str — serialized via ``str(...)`` either way.
EvidenceRef = tuple["uuid.UUID | str", "str | None"]

DOSSIER_PREAMBLE = b"easysynq.evidencepack.dossier.v1\n"

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclasses.dataclass(frozen=True, slots=True)
class UserRef:
    """A signer/creator reference passed in from the service — a UUID + the OPTIONAL human display
    name. It deliberately carries NOTHING else (no email / keycloak_subject / status): the PII
    boundary is the type, so an externally-shareable dossier can never leak a work email."""

    user_id: str | None
    display_name: str | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SignatureRef:
    """A capa_stage e-signature as projected for the dossier (no Part-11 crypto columns, no PII
    beyond the projected signer)."""

    meaning: str
    signer: UserRef | None
    content_digest: str | None
    signed_at: str | None


def project_user(ref: UserRef | None) -> dict[str, Any] | None:
    """The ONLY shape a user is serialized as in a dossier: ``{user_id, display_name}``. Returns
    ``None`` for an absent user (e.g. a system-actor signature with no human signer)."""
    if ref is None or ref.user_id is None:
        return None
    return {"user_id": str(ref.user_id), "display_name": ref.display_name}


def _project_signature(sig: SignatureRef | None) -> dict[str, Any] | None:
    if sig is None:
        return None
    return {
        "meaning": sig.meaning,
        "signer": project_user(sig.signer),
        "content_digest": sig.content_digest,
        "signed_at": sig.signed_at,
    }


def _evidence_list(records: Iterable[EvidenceRef]) -> list[dict[str, Any]]:
    """Deterministic (sorted-by-record-id) list of a stage/finding's linked evidence records."""
    out = [{"record_id": str(rid), "identifier": ident} for rid, ident in records]
    return sorted(out, key=lambda e: e["record_id"])


def dossier_filename(kind: str, identifier: str | None, subject_id: str) -> str:
    """A ZIP path for a subject's dossier JSON — ``{findings|capas}/<safe>.json``. The human
    identifier when present, sanitized to a safe component (``area_code`` is unconstrained, so a
    stray ``/`` or ``..`` can never escape the folder); falls back to the UUID."""
    folder = "findings" if kind == "finding" else "capas"
    safe = _FILENAME_SAFE.sub("_", identifier or subject_id).strip("._") or subject_id
    return f"{folder}/{safe}.json"


def canonical_dossier_bytes(obj: dict[str, Any]) -> bytes:
    """The deterministic on-disk serialization of one dossier subject (human-readable, stable)."""
    return json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")


def _sorted_lower(values: Iterable[str]) -> list[str]:
    return sorted({str(v).lower() for v in values})


def dossier_digest(file_sha256s: Iterable[str]) -> str:
    """Seal over the SORTED per-file sha256s (the ones in ``manifest["dossier"]["files"]``); a
    re-verifier re-derives it from the ZIP alone: sha256(preamble + rfc8785({"file_shas": …})).
    Order/duplicate independent."""
    obj = {"file_shas": _sorted_lower(file_sha256s)}
    return "sha256:" + hashlib.sha256(DOSSIER_PREAMBLE + rfc8785.dumps(obj)).hexdigest()


# --- subject serializers (PURE — primitives in, dict out) --------------------------------


def serialize_finding_dossier(
    *,
    finding_id: str,
    identifier: str | None,
    summary: str | None,
    finding_type: str,
    severity: str | None,
    clause_ref: str | None,
    process_ref: str | None,
    captured_at: str | None,
    captured_by: UserRef | None,
    content_hash: str | None,
    audit: dict[str, Any] | None,
    correction_of: str | None,
    superseded_by_correction: str | None,
    linked_capa: dict[str, Any] | None,
    evidence_records: Iterable[EvidenceRef],
) -> dict[str, Any]:
    """One finding's dossier: its fields, the audit it came from, the correction chain, the linked
    (auto) CAPA reference, and the records linked as its evidence."""
    return {
        "kind": "finding",
        "id": str(finding_id),
        "identifier": identifier,
        "summary": summary,
        "finding_type": finding_type,
        "severity": severity,
        "clause_ref": clause_ref,
        "process_ref": process_ref,
        "captured_at": captured_at,
        "captured_by": project_user(captured_by),
        "content_hash": content_hash,
        "audit": audit,
        "correction_of": correction_of,
        "superseded_by_correction": superseded_by_correction,
        "linked_capa": linked_capa,
        "evidence_records": _evidence_list(evidence_records),
    }


def serialize_capa_stage(
    *,
    stage_id: str,
    stage: str,
    cycle_marker: int,
    created_at: str | None,
    created_by: UserRef | None,
    content_block: dict[str, Any],
    signature: SignatureRef | None,
    evidence_records: Iterable[EvidenceRef],
) -> dict[str, Any]:
    """One append-only CAPA stage block: its sealed narrative, the cycle it belongs to, its
    e-signature (ActionPlan=approval / Verify=verify; else null), and its linked evidence."""
    return {
        "id": str(stage_id),
        "stage": stage,
        "cycle_marker": cycle_marker,
        "created_at": created_at,
        "created_by": project_user(created_by),
        "content_block": content_block,
        "signature": _project_signature(signature),
        "evidence_records": _evidence_list(evidence_records),
    }


def serialize_capa_dossier(
    *,
    capa_id: str,
    identifier: str | None,
    title: str | None,
    source: str,
    severity: str,
    close_state: str,
    cycle_marker: int,
    process_id: str | None,
    captured_at: str | None,
    captured_by: UserRef | None,
    content_hash: str | None,
    origin_finding: dict[str, Any] | None,
    stages: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """One CAPA's dossier: its lifecycle fields, the origin finding reference (inline), and the full
    append-only stage trail (chronological; ``cycle_marker`` groups the effectiveness-loop loops).
    ``stages`` are serialize_capa_stage outputs; sorted by (created_at, id) for a stable order."""
    ordered = sorted(stages, key=lambda s: (s.get("created_at") or "", s["id"]))
    return {
        "kind": "capa",
        "id": str(capa_id),
        "identifier": identifier,
        "title": title,
        "source": source,
        "severity": severity,
        "close_state": close_state,
        "cycle_count": cycle_marker + 1,
        "process_id": str(process_id) if process_id else None,
        "captured_at": captured_at,
        "captured_by": project_user(captured_by),
        "content_hash": content_hash,
        "origin_finding": origin_finding,
        "stages": ordered,
    }
