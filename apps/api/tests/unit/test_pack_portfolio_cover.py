"""Pure tests for the portfolio PDF cover/index text (``services/packs/portfolio``): a FINDING/CAPA
(v2) pack must instruct re-verifiers to use the v2 scheme and render the gap report as N/A, while a
CLAUSE/PROCESS (v1) pack keeps the v1 scheme + the clause-coverage gap line. A wrong scheme on the
PDF would make an auditor compute the wrong content hash and see a false tamper signal."""

from __future__ import annotations

import uuid

from easysynq_api.db.models._pack_enums import PackScopeKind
from easysynq_api.db.models.evidence_pack import EvidencePack
from easysynq_api.services.packs.portfolio import _cover_lines, _index_lines


def _pack(scope_kind: PackScopeKind, gap: dict[str, object]) -> EvidencePack:
    return EvidencePack(
        id=uuid.uuid4(),
        title="t",
        scope_kind=scope_kind,
        scope_selector={},
        content_hash="sha256:ab",
        item_count=1,
        gap_summary=gap,
        exclusion_summary={
            "permission_count": 0,
            "absence_count": 0,
            "permission": [],
            "absence": [],
        },
    )


def test_clause_pack_cover_uses_v1_scheme_and_clause_gap() -> None:
    pack = _pack(PackScopeKind.CLAUSE, {"gap_count": 1, "in_scope_star_clauses": 3, "clauses": []})
    text = "\n".join(_cover_lines(pack, None))
    assert "easysynq.evidencepack.v1" in text
    assert "easysynq.evidencepack.v2" not in text
    assert "1 of 3 in-scope mandatory clauses" in text
    assert "N/A (finding/CAPA scope)" not in text


def test_capa_pack_cover_uses_v2_scheme_and_na_gap() -> None:
    pack = _pack(
        PackScopeKind.CAPA,
        {"applicable": False, "gap_count": 0, "in_scope_star_clauses": 0, "clauses": []},
    )
    text = "\n".join(_cover_lines(pack, None))
    # v2 — must match the seal so a re-verifier computes the right hash.
    assert "easysynq.evidencepack.v2" in text
    assert "easysynq.evidencepack.v1" not in text
    # gap is N/A, never a misleading "0 of 0".
    assert "Gap report:    N/A (finding/CAPA scope)" in text
    assert "0 of 0" not in text
    # the dossier note points the reader to the ZIP variant.
    assert "narrative + e-signatures are in the ZIP" in text


def test_finding_pack_index_gap_is_na() -> None:
    pack = _pack(
        PackScopeKind.FINDING,
        {"applicable": False, "gap_count": 0, "in_scope_star_clauses": 0, "clauses": []},
    )
    text = "\n".join(_index_lines([], [], pack))
    assert "N/A — gap analysis does not apply to finding/CAPA scope." in text
    assert "all in-scope mandatory clauses have current evidence" not in text
