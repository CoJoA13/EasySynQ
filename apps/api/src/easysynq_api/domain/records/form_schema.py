"""The Mode-B form-template field schema — a small, strict, dependency-free DSL + validator.

Slice S-rec-3 (doc 06 §4.2). A *Form/Template* is a controlled Document carrying a ``field_schema``;
filling it captures a structured Record whose ``form_field_values`` are **validated server-side
against the schema pinned in the template's Effective version** (doc 06 §4.2 — the drift-killing
path). The spec fixes the validation set as "types, required, ranges, enumerations" (doc 06 §4.2 /
doc 15 §8.9); this module is that set and nothing more — no regex/``pattern`` leg (so no ReDoS
surface), no nesting (every field is a scalar), and no third-party dependency (the project's
dependency-light discipline — the S-rec-2 hand-rolled ISO-8601 parser precedent).

Two pure entry points, both returning ``list[FieldError]`` (empty = valid) so the service maps a
non-empty result to a 422 ``validation_error`` with per-field ``errors[].field`` (the
``records/service._validation_error`` shape):

* :func:`validate_schema` — the schema **definition** is well-formed (called when an author sets the
  working schema, before it is versioned). Bounds the schema size (field count / key+label+enum
  lengths / total bytes) so an authenticated author cannot persist an unbounded JSONB blob.
* :func:`validate_values` — submitted ``form_field_values`` satisfy a (already-validated) schema:
  required present, no unknown keys, scalar-only, per-type + range/enum checks. Bounds the total
  serialized value size before it reaches the synchronous ``record_content_hash`` step.

The DSL shape (``field_schema``)::

    {"fields": [
        {"key": "operator", "label": "Operator", "type": "string",
         "required": true, "min": 1, "max": 120},
        {"key": "result", "type": "enum", "required": true,
         "enum": ["pass", "adjusted", "fail"]},
        {"key": "reading", "type": "number", "min": 0, "max": 100},
        {"key": "cal_date", "type": "date"},
    ]}

``min``/``max`` mean a **value range** for ``number``/``integer``, a **length range** for
``string``/``text``, and an **ISO-date bound** for ``date``; ignored for ``boolean``/``enum``.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
from typing import Any

FIELD_TYPES: frozenset[str] = frozenset(
    {"string", "text", "number", "integer", "boolean", "date", "enum"}
)
_NUMERIC = frozenset({"number", "integer"})
_TEXTUAL = frozenset({"string", "text"})

# Definition bounds (validate_schema).
MAX_FIELDS = 200
MAX_KEY_LEN = 100
MAX_LABEL_LEN = 200
MAX_ENUM_MEMBERS = 100
MAX_ENUM_MEMBER_LEN = 200
MAX_SCHEMA_BYTES = 64 * 1024

# Value bounds (validate_values).
MAX_VALUES_BYTES = 64 * 1024
# A default ceiling on a textual field's length when the field declares no explicit ``max`` — so an
# unbounded string can never be smuggled into the immutable record store / the synchronous hash.
DEFAULT_MAX_STRING_LEN = 10_000


@dataclasses.dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code, "message": self.message}


def _serialized_size(value: Any) -> int:
    """Byte length of the compact JSON serialization (the size guard's measure)."""
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def values_too_large(values: Any) -> bool:
    """Whether ``form_field_values`` exceeds the size bound — checked for EVERY capture (Mode-B or
    free-form) before the synchronous ``record_content_hash`` runs, so an authenticated holder can't
    persist an unbounded JSONB blob into the immutable record store (S-rec-3 hardening)."""
    return _serialized_size(values) > MAX_VALUES_BYTES


def _as_number(value: Any) -> float | None:
    """A JSON number (int/float) but NOT a bool (``bool`` is an ``int`` subclass in Python)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


# --- schema definition validation --------------------------------------------------------


def validate_schema(field_schema: Any) -> list[FieldError]:
    """Return the (possibly empty) list of problems with a form-template schema **definition**.

    A non-empty result means the schema is malformed and must be rejected (422) before it is stored
    as the working copy or versioned. Deterministic + pure (no DB, no IO)."""
    errors: list[FieldError] = []
    if not isinstance(field_schema, dict):
        return [FieldError("field_schema", "invalid", "field_schema must be an object")]
    if _serialized_size(field_schema) > MAX_SCHEMA_BYTES:
        return [FieldError("field_schema", "too_large", f"schema exceeds {MAX_SCHEMA_BYTES} bytes")]
    fields = field_schema.get("fields")
    if not isinstance(fields, list) or not fields:
        return [FieldError("fields", "required", "fields must be a non-empty list")]
    if len(fields) > MAX_FIELDS:
        errors.append(FieldError("fields", "too_many", f"at most {MAX_FIELDS} fields"))

    seen: set[str] = set()
    for idx, field in enumerate(fields):
        loc = f"fields[{idx}]"
        if not isinstance(field, dict):
            errors.append(FieldError(loc, "invalid", "each field must be an object"))
            continue
        key = field.get("key")
        if not isinstance(key, str) or not key.strip():
            errors.append(FieldError(f"{loc}.key", "required", "key must be a non-empty string"))
        else:
            if len(key) > MAX_KEY_LEN:
                errors.append(FieldError(f"{loc}.key", "too_long", f"key over {MAX_KEY_LEN} chars"))
            if key in seen:
                errors.append(FieldError(f"{loc}.key", "duplicate", f"duplicate key {key!r}"))
            seen.add(key)
        ftype = field.get("type")
        if ftype not in FIELD_TYPES:
            errors.append(
                FieldError(f"{loc}.type", "invalid", f"type must be one of {sorted(FIELD_TYPES)}")
            )
        label = field.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL_LEN):
            errors.append(FieldError(f"{loc}.label", "invalid", "label must be a short string"))
        if "required" in field and not isinstance(field.get("required"), bool):
            errors.append(FieldError(f"{loc}.required", "invalid", "required must be a boolean"))
        errors.extend(_validate_field_constraints(loc, field, ftype))
    return errors


def _validate_field_constraints(loc: str, field: dict[str, Any], ftype: Any) -> list[FieldError]:
    errors: list[FieldError] = []
    lo, hi = field.get("min"), field.get("max")
    if ftype in _NUMERIC or ftype in _TEXTUAL or ftype == "date":
        for bound_name, bound in (("min", lo), ("max", hi)):
            if bound is None:
                continue
            if ftype == "date":
                if not (isinstance(bound, str) and _parse_date(bound) is not None):
                    errors.append(
                        FieldError(f"{loc}.{bound_name}", "invalid", "date bound must be ISO-8601")
                    )
            elif _as_number(bound) is None:
                errors.append(
                    FieldError(f"{loc}.{bound_name}", "invalid", f"{bound_name} must be numeric")
                )
        # min <= max when both are present and comparable.
        if ftype == "date" and isinstance(lo, str) and isinstance(hi, str):
            dl, dh = _parse_date(lo), _parse_date(hi)
            if dl is not None and dh is not None and dl > dh:
                errors.append(FieldError(f"{loc}.max", "invalid", "max precedes min"))
        elif ftype != "date" and _as_number(lo) is not None and _as_number(hi) is not None:
            if _as_number(lo) > _as_number(hi):  # type: ignore[operator]
                errors.append(FieldError(f"{loc}.max", "invalid", "max precedes min"))
    if ftype == "enum":
        members = field.get("enum")
        if not isinstance(members, list) or not members:
            errors.append(FieldError(f"{loc}.enum", "required", "enum requires a non-empty list"))
        else:
            if len(members) > MAX_ENUM_MEMBERS:
                errors.append(FieldError(f"{loc}.enum", "too_many", "too many enum members"))
            if not all(isinstance(m, str) and 0 < len(m) <= MAX_ENUM_MEMBER_LEN for m in members):
                errors.append(
                    FieldError(f"{loc}.enum", "invalid", "enum members must be short strings")
                )
    elif "enum" in field:
        errors.append(FieldError(f"{loc}.enum", "invalid", "enum is only valid for type=enum"))
    return errors


def _parse_date(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


# --- submitted value validation ----------------------------------------------------------


def validate_values(field_schema: dict[str, Any], values: Any) -> list[FieldError]:
    """Return the (possibly empty) list of problems with submitted ``form_field_values`` against an
    **already-validated** schema. A non-empty result → 422 with per-field ``errors[].field``.

    Enforces: total size bound; object-shaped; no unknown keys; required present; scalar-only; and
    per-type + range/enum checks. Pure (no DB, no IO)."""
    if not isinstance(values, dict):
        return [FieldError("form_field_values", "invalid", "form_field_values must be an object")]
    if _serialized_size(values) > MAX_VALUES_BYTES:
        return [
            FieldError("form_field_values", "too_large", f"values exceed {MAX_VALUES_BYTES} bytes")
        ]
    fields = {
        f["key"]: f for f in field_schema.get("fields", []) if isinstance(f, dict) and "key" in f
    }
    errors: list[FieldError] = []

    for key in values:
        if key not in fields:
            errors.append(FieldError(key, "unknown_field", f"{key!r} is not a schema field"))

    for key, field in fields.items():
        present = key in values
        value = values.get(key)
        if not present or value is None:
            if field.get("required") is True:
                errors.append(FieldError(key, "required", f"{key!r} is required"))
            continue
        errors.extend(_validate_one_value(key, field, value))
    return errors


def _validate_one_value(key: str, field: dict[str, Any], value: Any) -> list[FieldError]:
    ftype = field.get("type")
    lo, hi = field.get("min"), field.get("max")

    if ftype in _NUMERIC:
        num = _as_number(value)
        if num is None or (ftype == "integer" and not float(num).is_integer()):
            want = "an integer" if ftype == "integer" else "a number"
            return [FieldError(key, "type", f"{key!r} must be {want}")]
        errs: list[FieldError] = []
        if (n := _as_number(lo)) is not None and num < n:
            errs.append(FieldError(key, "min", f"{key!r} below minimum {lo}"))
        if (n := _as_number(hi)) is not None and num > n:
            errs.append(FieldError(key, "max", f"{key!r} above maximum {hi}"))
        return errs

    if ftype in _TEXTUAL:
        if not isinstance(value, str):
            return [FieldError(key, "type", f"{key!r} must be a string")]
        max_len = hi if isinstance(hi, int) and not isinstance(hi, bool) else DEFAULT_MAX_STRING_LEN
        errs = []
        if isinstance(lo, int) and not isinstance(lo, bool) and len(value) < lo:
            errs.append(FieldError(key, "min", f"{key!r} shorter than {lo} chars"))
        if len(value) > max_len:
            errs.append(FieldError(key, "max", f"{key!r} longer than {max_len} chars"))
        return errs

    if ftype == "boolean":
        if isinstance(value, bool):
            return []
        return [FieldError(key, "type", f"{key!r} must be a boolean")]

    if ftype == "date":
        if not isinstance(value, str) or (d := _parse_date(value)) is None:
            return [FieldError(key, "type", f"{key!r} must be an ISO-8601 date")]
        errs = []
        if isinstance(lo, str) and (dl := _parse_date(lo)) is not None and d < dl:
            errs.append(FieldError(key, "min", f"{key!r} before {lo}"))
        if isinstance(hi, str) and (dh := _parse_date(hi)) is not None and d > dh:
            errs.append(FieldError(key, "max", f"{key!r} after {hi}"))
        return errs

    if ftype == "enum":
        members = field.get("enum") or []
        return [] if value in members else [FieldError(key, "enum", f"{key!r} not in {members}")]

    return [FieldError(key, "type", f"{key!r} has an unknown field type")]  # pragma: no cover
