"""``AuditEvent.scope_ref`` must be bounded so it can never break its btree index.

Migration 0075 indexes ``(org_id, scope_ref, id)``, and a PostgreSQL btree tuple cannot exceed 2704
bytes. ``scope_ref`` is unbounded ``Text`` fed from caller-supplied values — ``pep._scope_ref``
interpolates ``resource.folder_path`` (no length limit on ``DocumentCreate``/``MetadataUpdate``),
and the vault path uses ``documented_information.identifier``/``legacy_identifier``, also unbounded.

Empirically, on PG16, an incompressible value of ~2700 characters produces
``index row size 2768 exceeds btree version 4 maximum 2704``. (A *compressible* value of the same
length is fine — PostgreSQL compresses before indexing — which is why a naive
``repeat('a', 10000)`` probe wrongly suggests there is no problem.)

Without the cap the consequence is not a tidy 400: ``DbAuthzAuditSink.record`` commits in its own
transaction, so the failure propagates out of ``pep.enforce`` and the caller gets a **500 instead of
a 403, with the denial never recorded** — an audit-integrity gap, not just a bad status code.
"""

from __future__ import annotations

from easysynq_api.db.models.audit_event import (
    _SCOPE_REF_CAPPED_CHARS,
    _SCOPE_REF_MAX_CHARS,
    AuditEvent,
    bound_scope_ref,
)


def _scope_ref_of(value: str | None) -> str | None:
    """Round-trip through real attribute assignment, so the test exercises the ``@validates`` hook
    the way production does rather than calling the function directly."""
    return AuditEvent(scope_ref=value).scope_ref


def test_short_value_is_untouched() -> None:
    assert _scope_ref_of("artifact:SOP-PUR-001") == "artifact:SOP-PUR-001"


def test_none_is_untouched() -> None:
    assert _scope_ref_of(None) is None


def test_value_exactly_at_the_cap_is_untouched() -> None:
    exact = "f" * _SCOPE_REF_MAX_CHARS
    assert _scope_ref_of(exact) == exact


def test_over_long_value_is_capped() -> None:
    capped = _scope_ref_of("folder:" + "x" * 5000)
    assert capped is not None
    assert len(capped) == _SCOPE_REF_CAPPED_CHARS
    assert capped.startswith("folder:xxx")


def test_capped_value_is_safely_under_the_btree_limit_even_in_utf8() -> None:
    """The cap counts CHARACTERS, so the byte-length claim needs its own assertion.

    Worst-case UTF-8 is 4 bytes/char; this uses a 4-byte emoji throughout to pin the actual bound
    rather than the ASCII-only happy path.
    """
    capped = _scope_ref_of("folder:" + "🙂" * 5000)
    assert capped is not None
    # 2704 is the hard btree tuple limit; org_id (16) + id (8) + overhead also live in that budget.
    assert len(capped.encode("utf-8")) <= 2400


def test_capping_is_deterministic() -> None:
    value = "folder:" + "y" * 5000
    assert _scope_ref_of(value) == _scope_ref_of(value)


def test_read_key_matches_what_the_write_path_stores() -> None:
    """The write/read symmetry the per-document audit history depends on.

    ``api/audit.py::document_audit_events`` looks rows up by ``scope_ref``, so its search key must
    canonicalize exactly as the write path does. ``documented_information.identifier`` is unbounded
    ``Text`` and an import can preserve an arbitrarily long legacy identifier, so for such a
    document a raw-identifier comparison matches NOTHING once the cap applies — the endpoint would
    return an incomplete audit trail with no error, which is worse than failing outright.
    """
    for identifier in ("SOP-PUR-001", "LEGACY-" + "q" * 4000):
        assert bound_scope_ref(identifier) == AuditEvent(scope_ref=identifier).scope_ref


def test_a_capped_key_can_never_alias_another_rows_untouched_value() -> None:
    """The security property, and the reason this function is deliberately NOT idempotent.

    Documents A and B are distinct rows — identifiers are arbitrary strings on the import path and
    the uniqueness constraint is on the raw value, so both can exist. If B's long identifier capped
    to a key that A's shorter identifier passes through to unchanged, both documents' audit events
    would land on ONE scope_ref and ``document_scope_match`` would silently merge two documents'
    histories. Making the emitted length exceed the pass-through threshold makes the two sets
    disjoint by construction.

    Idempotence is the direct trade: ``bound(bound(x)) == bound(x)`` would mean a capped value
    passes through untouched, which is exactly the alias. Safe only because every caller applies
    this exactly once to a RAW value — never to one read back from the database.
    """
    b_identifier = "LEGACY-" + "q" * 4000
    a_identifier = bound_scope_ref(
        b_identifier
    )  # A adopts B's capped key as its literal identifier
    assert a_identifier is not None

    assert bound_scope_ref(a_identifier) != bound_scope_ref(b_identifier), (
        "a capped key aliased another document's untouched identifier — audit histories would merge"
    )
    # The general invariant behind that case: nothing passes through at the emitted length.
    assert _SCOPE_REF_CAPPED_CHARS > _SCOPE_REF_MAX_CHARS


def test_history_query_searches_the_stored_key_for_a_long_identifier() -> None:
    """Guards the endpoint's key-building, not just the helper.

    A regression to a plain ``scope_ref == identifier`` passes every other test in this file,
    because they all use short identifiers where the cap is the identity function. This one uses a
    long identifier and asserts the compiled predicate actually contains the CAPPED key — which is
    what the rows hold — plus the raw one, so pre-cap rows are still found.
    """
    from easysynq_api.api.audit import document_scope_match

    identifier = "LEGACY-" + "k" * 4000
    stored = bound_scope_ref(identifier)
    assert stored is not None and stored != identifier  # precondition: this identifier IS capped

    sql = str(document_scope_match(identifier).compile(compile_kwargs={"literal_binds": True}))
    assert stored in sql, "the capped key the rows are actually stored under is not searched"
    assert identifier in sql, "pre-cap rows holding the raw identifier would be missed"


def test_two_over_long_values_sharing_a_prefix_stay_distinguishable() -> None:
    """The load-bearing case: a naive ``value[:512]`` truncation passes every test above and fails
    this one. Two different folder paths that agree for the first 512 characters would collapse to
    the same audit scope reference — silently merging the audit trails of two distinct scopes.
    """
    shared = "folder:" + "z" * 4000
    a, b = _scope_ref_of(shared + "-alpha"), _scope_ref_of(shared + "-beta")
    assert a != b
    assert a is not None and b is not None
    assert len(a) == len(b) == _SCOPE_REF_CAPPED_CHARS
