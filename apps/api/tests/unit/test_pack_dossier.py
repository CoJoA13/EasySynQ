"""Pure tests for the synthesized FINDING/CAPA pack dossier (``domain/packs/dossier``,
S-aud-capa-pack): the PII boundary (``project_user`` emits ONLY user_id + display_name), the
ZIP-reconstructable ``dossier_digest`` (deterministic + order/dup independent), filename
sanitization, deterministic serialization, cycle grouping, and signature projection."""

from __future__ import annotations

import json

from easysynq_api.domain.packs.dossier import (
    DOSSIER_PREAMBLE,
    SignatureRef,
    UserRef,
    canonical_dossier_bytes,
    dossier_digest,
    dossier_filename,
    project_user,
    serialize_capa_dossier,
    serialize_capa_stage,
    serialize_finding_dossier,
)


def test_project_user_emits_only_id_and_display_name() -> None:
    # The PII boundary: even though a UserRef *could* be constructed loosely, the projected dict has
    # EXACTLY {user_id, display_name} — no email / keycloak_subject can ever appear.
    out = project_user(UserRef(user_id="u1", display_name="Mara Q"))
    assert out == {"user_id": "u1", "display_name": "Mara Q"}
    assert project_user(UserRef(user_id="u2", display_name=None)) == {
        "user_id": "u2",
        "display_name": None,
    }
    assert project_user(None) is None
    assert project_user(UserRef(user_id=None)) is None  # a system-actor signature


def test_dossier_digest_deterministic_order_dup_independent() -> None:
    a = dossier_digest(["AB", "cd", "ab"])  # mixed case + a dup
    b = dossier_digest(["cd", "ab"])  # different order
    assert a == b
    assert a.startswith("sha256:")
    assert dossier_digest(["ab"]) != dossier_digest(["cd"])
    assert dossier_digest([]) != dossier_digest(["ab"])  # an empty dossier seals distinctly


def test_dossier_digest_uses_domain_preamble() -> None:
    import hashlib

    import rfc8785

    expected = (
        "sha256:"
        + hashlib.sha256(DOSSIER_PREAMBLE + rfc8785.dumps({"file_shas": ["ab", "cd"]})).hexdigest()
    )
    assert dossier_digest(["cd", "ab"]) == expected


def test_dossier_filename_sanitizes_and_routes() -> None:
    assert dossier_filename("finding", "REC-QMS-0042", "uuid-x") == "findings/REC-QMS-0042.json"
    assert dossier_filename("capa", "REC-CAPA-0007", "uuid-y") == "capas/REC-CAPA-0007.json"
    # Path traversal / separators in an (unconstrained) identifier are neutralized — cannot escape
    # the folder, and leading/trailing dots/underscores are stripped (no hidden/relative names).
    traversal = dossier_filename("finding", "../../etc/passwd", "uuid-z")
    assert traversal == "findings/etc_passwd.json"
    # No identifier → fall back to the UUID; an all-separator identifier also falls back.
    assert dossier_filename("capa", None, "abc-123") == "capas/abc-123.json"
    assert dossier_filename("finding", "///", "fallback-id") == "findings/fallback-id.json"


def test_canonical_dossier_bytes_deterministic() -> None:
    obj = {"b": 2, "a": 1, "nested": {"y": 1, "x": 2}}
    out = canonical_dossier_bytes(obj)
    assert canonical_dossier_bytes(obj) == out  # stable
    assert json.loads(out) == obj
    # sort_keys → "a" precedes "b" in the bytes.
    assert out.index(b'"a"') < out.index(b'"b"')


def test_serialize_finding_dossier_shape_and_no_pii() -> None:
    d = serialize_finding_dossier(
        finding_id="f1",
        identifier="REC-QMS-0001",
        summary="bolts under-torqued",
        finding_type="NC",
        severity="Major",
        clause_ref="8.4",
        process_ref=None,
        captured_at="2026-06-01T00:00:00+00:00",
        captured_by=UserRef(user_id="u1", display_name="Ingrid A"),
        content_hash="sha256:aa",
        audit={"id": "a1", "identifier": "REC-AUD-0001"},
        correction_of=None,
        superseded_by_correction=None,
        linked_capa={"id": "c1", "identifier": "REC-CAPA-0001", "close_state": "Closed"},
        evidence_records=[("r2", "REC-EV-0002"), ("r1", "REC-EV-0001")],
    )
    assert d["kind"] == "finding"
    assert d["finding_type"] == "NC" and d["severity"] == "Major"
    assert d["captured_by"] == {"user_id": "u1", "display_name": "Ingrid A"}
    assert d["linked_capa"]["close_state"] == "Closed"
    # evidence sorted by record_id for a stable seal.
    assert [e["record_id"] for e in d["evidence_records"]] == ["r1", "r2"]
    # No PII leaks anywhere in the serialized bytes.
    blob = canonical_dossier_bytes(d).decode()
    assert "email" not in blob and "keycloak" not in blob


def test_serialize_capa_dossier_cycle_grouping_and_signature() -> None:
    s_rc = serialize_capa_stage(
        stage_id="s1",
        stage="RootCause",
        cycle_marker=0,
        created_at="2026-06-01T00:00:00+00:00",
        created_by=UserRef(user_id="u1", display_name="Diego P"),
        content_block={"root_cause": "rc"},
        signature=None,
        evidence_records=[],
    )
    s_ver = serialize_capa_stage(
        stage_id="s2",
        stage="Verify",
        cycle_marker=1,
        created_at="2026-06-03T00:00:00+00:00",
        created_by=UserRef(user_id="u2", display_name="Ken K"),
        content_block={"decision": "effective"},
        signature=SignatureRef(
            meaning="verify",
            signer=UserRef(user_id="u2", display_name="Ken K"),
            content_digest="sha256:bb",
            signed_at="2026-06-03T00:00:00+00:00",
        ),
        evidence_records=[("r9", "REC-EV-0009")],
    )
    # Pass the stages OUT of chronological order — the serializer must re-sort by (created_at, id).
    d = serialize_capa_dossier(
        capa_id="c1",
        identifier="REC-CAPA-0001",
        title="Re-torque",
        source="audit",
        severity="Minor",
        close_state="Closed",
        cycle_marker=1,
        process_id=None,
        captured_at="2026-06-01T00:00:00+00:00",
        captured_by=UserRef(user_id="u1", display_name="Diego P"),
        content_hash="sha256:cc",
        origin_finding={"id": "f1", "identifier": "REC-QMS-0001"},
        stages=[s_ver, s_rc],
    )
    assert d["kind"] == "capa" and d["close_state"] == "Closed"
    assert d["cycle_count"] == 2  # cycle_marker 1 → two iterations (0 and 1)
    assert [s["id"] for s in d["stages"]] == ["s1", "s2"]  # re-sorted chronological
    sig = d["stages"][1]["signature"]
    assert sig["meaning"] == "verify"
    assert sig["signer"] == {"user_id": "u2", "display_name": "Ken K"}
    assert d["stages"][0]["signature"] is None  # RootCause is unsigned
    blob = canonical_dossier_bytes(d).decode()
    assert "email" not in blob and "keycloak" not in blob
