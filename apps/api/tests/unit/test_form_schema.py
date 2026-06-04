"""S-rec-3 unit proofs — the bespoke Mode-B field-schema DSL + validator (pure, no DB).

Covers the schema-DEFINITION checks (``validate_schema``) and the submitted-VALUE checks
(``validate_values``): types, required, ranges, enumerations, unknown-key rejection, scalar-only,
and the size guards. Every entry point returns ``list[FieldError]`` (empty = valid)."""

from __future__ import annotations

from easysynq_api.domain.records.form_schema import (
    DEFAULT_MAX_STRING_LEN,
    MAX_FIELDS,
    MAX_VALUES_BYTES,
    FieldError,
    validate_schema,
    validate_values,
    values_too_large,
)

_GOOD_SCHEMA = {
    "fields": [
        {"key": "operator", "label": "Operator", "type": "string", "required": True, "max": 120},
        {"key": "reading", "type": "number", "min": 0, "max": 100},
        {"key": "count", "type": "integer", "min": 0},
        {"key": "passed", "type": "boolean"},
        {"key": "cal_date", "type": "date", "min": "2020-01-01"},
        {"key": "result", "type": "enum", "required": True, "enum": ["pass", "adjusted", "fail"]},
    ]
}


def _codes(errors: list[FieldError]) -> set[str]:
    return {e.code for e in errors}


# --- validate_schema (the definition) ----------------------------------------------------


def test_valid_schema_has_no_errors() -> None:
    assert validate_schema(_GOOD_SCHEMA) == []


def test_schema_must_be_object_with_nonempty_fields() -> None:
    assert validate_schema([]) != []  # not a dict
    assert validate_schema({}) != []  # no fields
    assert validate_schema({"fields": []}) != []  # empty fields


def test_schema_rejects_duplicate_and_empty_keys() -> None:
    dup = {"fields": [{"key": "a", "type": "string"}, {"key": "a", "type": "string"}]}
    assert "duplicate" in _codes(validate_schema(dup))
    empty = {"fields": [{"key": "  ", "type": "string"}]}
    assert "required" in _codes(validate_schema(empty))


def test_schema_rejects_unknown_type() -> None:
    assert "invalid" in _codes(validate_schema({"fields": [{"key": "x", "type": "money"}]}))


def test_schema_enum_requires_members_and_only_for_enum() -> None:
    no_members = {"fields": [{"key": "x", "type": "enum"}]}
    assert "required" in _codes(validate_schema(no_members))
    enum_on_string = {"fields": [{"key": "x", "type": "string", "enum": ["a"]}]}
    assert "invalid" in _codes(validate_schema(enum_on_string))


def test_schema_rejects_min_greater_than_max() -> None:
    assert validate_schema({"fields": [{"key": "x", "type": "number", "min": 5, "max": 1}]}) != []
    assert (
        validate_schema(
            {"fields": [{"key": "d", "type": "date", "min": "2025-01-01", "max": "2024-01-01"}]}
        )
        != []
    )


def test_schema_rejects_non_numeric_and_bad_date_bounds() -> None:
    assert validate_schema({"fields": [{"key": "x", "type": "number", "min": "lots"}]}) != []
    assert validate_schema({"fields": [{"key": "d", "type": "date", "max": "not-a-date"}]}) != []


def test_schema_caps_field_count() -> None:
    big = {"fields": [{"key": f"f{i}", "type": "string"} for i in range(MAX_FIELDS + 1)]}
    assert "too_many" in _codes(validate_schema(big))


# --- validate_values (the submission) ----------------------------------------------------


def test_valid_values_pass() -> None:
    values = {
        "operator": "Mara",
        "reading": 42.5,
        "count": 3,
        "passed": True,
        "cal_date": "2024-06-01",
        "result": "pass",
    }
    assert validate_values(_GOOD_SCHEMA, values) == []


def test_required_fields_enforced() -> None:
    # operator + result are required; reading/count/passed/cal_date are optional.
    errors = validate_values(_GOOD_SCHEMA, {})
    assert {e.field for e in errors if e.code == "required"} == {"operator", "result"}


def test_unknown_keys_rejected() -> None:
    errors = validate_values(_GOOD_SCHEMA, {"operator": "x", "result": "pass", "ghost": 1})
    assert any(e.field == "ghost" and e.code == "unknown_field" for e in errors)


def test_type_mismatches_rejected() -> None:
    bad = {
        "operator": 5,  # not a string
        "reading": "hot",  # not a number
        "count": 1.5,  # not an integer
        "passed": "yes",  # not a boolean
        "cal_date": "nope",  # not a date
        "result": "maybe",  # not in enum
    }
    errors = validate_values(_GOOD_SCHEMA, bad)
    assert {e.field for e in errors} == {
        "operator",
        "reading",
        "count",
        "passed",
        "cal_date",
        "result",
    }


def test_numeric_range_enforced() -> None:
    over = validate_values(_GOOD_SCHEMA, {"operator": "x", "result": "pass", "reading": 999})
    assert any(e.field == "reading" and e.code == "max" for e in over)
    under = validate_values(_GOOD_SCHEMA, {"operator": "x", "result": "pass", "reading": -1})
    assert any(e.field == "reading" and e.code == "min" for e in under)


def test_string_length_range_enforced() -> None:
    errors = validate_values(_GOOD_SCHEMA, {"operator": "z" * 200, "result": "pass"})
    assert any(e.field == "operator" and e.code == "max" for e in errors)


def test_date_bound_enforced() -> None:
    errors = validate_values(
        _GOOD_SCHEMA, {"operator": "x", "result": "pass", "cal_date": "1999-01-01"}
    )
    assert any(e.field == "cal_date" and e.code == "min" for e in errors)


def test_bool_is_not_a_number() -> None:
    # A Python bool is an int subclass — the validator must NOT accept True for a number field.
    errors = validate_values(_GOOD_SCHEMA, {"operator": "x", "result": "pass", "reading": True})
    assert any(e.field == "reading" and e.code == "type" for e in errors)


def test_default_string_length_cap_when_no_max_declared() -> None:
    schema = {"fields": [{"key": "note", "type": "text"}]}
    assert validate_values(schema, {"note": "z" * (DEFAULT_MAX_STRING_LEN + 1)}) != []
    assert validate_values(schema, {"note": "ok"}) == []


def test_value_size_guard() -> None:
    huge = {"note": "z" * (MAX_VALUES_BYTES + 10)}
    assert values_too_large(huge) is True
    assert values_too_large({"note": "small"}) is False
    schema = {"fields": [{"key": "note", "type": "text", "max": MAX_VALUES_BYTES * 2}]}
    assert any(e.code == "too_large" for e in validate_values(schema, huge))
