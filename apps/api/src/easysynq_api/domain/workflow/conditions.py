"""Pure, sandboxed condition evaluation for the declarative workflow engine (doc 10 §2.4-2.5).

No I/O. The grammar is the doc-10 subset — ``==`` / ``!=`` / ``in`` / ``not in`` / ``and`` / ``or``
/ ``not`` over an instance ``context`` snapshot (e.g. ``{"severity": "Critical"}``) and literals. It
is parsed with the ``ast`` module under a strict node whitelist — **never ``eval``** (no calls, no
attribute access, no names beyond bare context keys), so an org-authored predicate cannot run code
(the rule-pack ReDoS-confinement spirit, applied to predicates).

Totality (doc 10 §2.3 "auditability over convenience"): every function is TOTAL — a ``None``
context, a missing key, or a malformed predicate yields a defined result, never an exception.
``evaluate_condition`` returns ``False`` for a missing key; ``resolve_conditional`` distinguishes an
*absent discriminator* (→ ``None`` = fail-closed → NEEDS_ATTENTION) from *present-but-unmatched*
(→ default).
"""

from __future__ import annotations

import ast
from typing import Any

_MISSING = object()


class _ConditionError(ValueError):
    """A predicate that is malformed or uses a disallowed construct — treated as fail-closed."""


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(e) for e in node.elts]
    raise _ConditionError("non-literal in comparison operand")


def _value(node: ast.AST, context: dict[str, Any]) -> Any:
    """A bare ``Name`` resolves from context (missing → _MISSING sentinel); else a literal."""
    if isinstance(node, ast.Name):
        return context.get(node.id, _MISSING)
    return _literal(node)


def _eval(node: ast.AST, context: dict[str, Any]) -> bool:
    if isinstance(node, ast.BoolOp):
        results = [_eval(v, context) for v in node.values]
        return all(results) if isinstance(node.op, ast.And) else any(results)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, context)
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise _ConditionError("chained comparisons are not allowed")
        left = _value(node.left, context)
        right = _value(node.comparators[0], context)
        op = node.ops[0]
        if left is _MISSING or right is _MISSING:
            return False  # a missing key compares false (totality)
        if isinstance(op, ast.Eq):
            return bool(left == right)
        if isinstance(op, ast.NotEq):
            return bool(left != right)
        if isinstance(op, ast.In):
            return bool(right) and left in right
        if isinstance(op, ast.NotIn):
            return not (bool(right) and left in right)
        raise _ConditionError("unsupported comparison operator")
    raise _ConditionError("unsupported expression node")


def evaluate_condition(condition: str, context: dict[str, Any] | None) -> bool:
    """Evaluate a predicate against ``context``. TOTAL: a ``None``/empty context, a missing key, or
    a malformed/disallowed predicate all yield ``False`` (never raises)."""
    ctx = context or {}
    try:
        tree = ast.parse(condition, mode="eval")
        return _eval(tree.body, ctx)
    except (_ConditionError, SyntaxError, ValueError):
        return False


def referenced_keys(condition: str) -> set[str]:
    """The bare context keys a predicate references (to detect an absent discriminator). Returns an
    empty set on a malformed predicate."""
    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def resolve_conditional(
    spec: dict[str, Any] | None, context: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Resolve a (possibly conditional) quorum/assignee spec against ``context`` (doc 10 §2.4).

    A flat spec (``type`` != ``conditional``) is returned unchanged. A conditional spec is
    ``{"type": "conditional", "rule": [{"when": "<pred>", "quorum": {...}}, …, {"default":
    {...}}]}``;
    its ``rule`` is walked in order and the first matching ``when`` wins. Returns:
    - the matched/branch spec, OR
    - the ``default`` branch when the discriminator key(s) ARE present but no ``when`` matched, OR
    - ``None`` (FAIL CLOSED → the engine sets NEEDS_ATTENTION) when the discriminator key(s) are
      ABSENT from the context, or the spec is malformed.
    """
    if not isinstance(spec, dict):
        return None
    if spec.get("type") != "conditional":
        return spec
    rule = spec.get("rule")
    if not isinstance(rule, list):
        return None
    default: dict[str, Any] | None = None
    refs: set[str] = set()
    for entry in rule:
        if not isinstance(entry, dict):
            continue
        if "default" in entry:
            default = entry["default"] if isinstance(entry["default"], dict) else None
            continue
        when = entry.get("when")
        quorum = entry.get("quorum")
        if not isinstance(when, str) or not isinstance(quorum, dict):
            continue
        refs |= referenced_keys(when)
        if evaluate_condition(when, context):
            return quorum
    # No when matched. Fail closed if ANY referenced discriminator key is absent (a multi-key
    # conjunction with a missing key must NOT silently fall to default — doc 10 §2.3); only when
    # every referenced key is present (so the non-match is genuine) do we take the default.
    if not (refs <= set(context or {})):
        return None
    return default
