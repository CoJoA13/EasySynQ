"""Contract checks for the bounded provider read-multiset failure receipt."""

from __future__ import annotations

import copy

import pytest

from tests.integration import audit_history_collection_runtime_acceptance as acceptance

pytestmark = pytest.mark.unit

_PREFIX = "provider exact-read multiset differs; history_provider_v1 "
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _expected() -> list[list[object]]:
    return [
        ["bucket-a", "checkpoints/a", "version-a", 3, _DIGEST_A],
        ["bucket-b", "checkpoints/b", "version-b", 5, _DIGEST_B],
    ]


def _case(
    *,
    outcome: str = "report",
    status: str | None = "traversed",
    reads: list[list[object]] | None = None,
    attempted_reads: int | None = None,
    pages: list[object] | None = None,
    attempted_pages: int | None = None,
    elapsed_ms: int = 42,
    terminal_witnesses: int = 2,
    unavailable_reads: int = 0,
) -> dict[str, object]:
    actual_reads = _expected() if reads is None else reads
    actual_pages = [{}] if pages is None else pages
    report = None
    if status is not None:
        report = {
            "status": status,
            "witnesses": [
                {"terminal_reached": True, "unavailable_reads": unavailable_reads},
                {"terminal_reached": terminal_witnesses == 2, "unavailable_reads": 0},
            ],
        }
    return {
        "outcome": outcome,
        "report": report,
        "elapsed_ms": elapsed_ms,
        "reads": actual_reads,
        "attempted_exact_reads": len(actual_reads) if attempted_reads is None else attempted_reads,
        "pages": actual_pages,
        "list_attempts": len(actual_pages) if attempted_pages is None else attempted_pages,
    }


def _receipt(**fields: object) -> str:
    values: dict[str, object] = {
        "outcome": "report",
        "status": "traversed",
        "elapsed_ms": 42,
        "attempted_reads": 2,
        "returned_reads": 2,
        "attempted_pages": 1,
        "returned_pages": 1,
        "expected_reads": 2,
        "locator_matches": 2,
        "exact_matches": 2,
        "terminal_witnesses": 2,
        "unavailable_reads": 0,
    }
    values.update(fields)
    return _PREFIX + " ".join(f"{name}={value}" for name, value in values.items())


@pytest.mark.parametrize(
    ("case", "expected_message"),
    [
        (_case(), _receipt()),
        (
            _case(outcome="DEADLINE_EXCEEDED", status=None, reads=[], pages=[]),
            _receipt(
                outcome="DEADLINE_EXCEEDED",
                status="none",
                returned_reads=0,
                attempted_reads=0,
                returned_pages=0,
                attempted_pages=0,
                locator_matches=0,
                exact_matches=0,
                terminal_witnesses="none",
                unavailable_reads="none",
            ),
        ),
        (
            _case(outcome="cancelled", status=None, reads=[], pages=[]),
            _receipt(
                outcome="cancelled",
                status="none",
                returned_reads=0,
                attempted_reads=0,
                returned_pages=0,
                attempted_pages=0,
                locator_matches=0,
                exact_matches=0,
                terminal_witnesses="none",
                unavailable_reads="none",
            ),
        ),
        (
            _case(outcome="STORAGE_FAILED", status=None, reads=[], pages=[]),
            _receipt(
                outcome="STORAGE_FAILED",
                status="none",
                returned_reads=0,
                attempted_reads=0,
                returned_pages=0,
                attempted_pages=0,
                locator_matches=0,
                exact_matches=0,
                terminal_witnesses="none",
                unavailable_reads="none",
            ),
        ),
        (
            _case(status="incomplete"),
            _receipt(status="incomplete"),
        ),
        (
            _case(reads=[_expected()[0]], attempted_reads=2, unavailable_reads=1),
            _receipt(returned_reads=1, locator_matches=1, exact_matches=1, unavailable_reads=1),
        ),
        (
            _case(reads=[["bucket-a", "changed", "version-a", 3, _DIGEST_A], _expected()[1]]),
            _receipt(locator_matches=1, exact_matches=1),
        ),
        (
            _case(reads=[_expected()[0], _expected()[0]]),
            _receipt(locator_matches=1, exact_matches=1),
        ),
        (
            _case(
                reads=[
                    [_expected()[0][0], _expected()[0][1], _expected()[0][2], 4, _DIGEST_B],
                    _expected()[1],
                ]
            ),
            _receipt(locator_matches=2, exact_matches=1),
        ),
    ],
)
def test_provider_failure_message_reduces_only_bounded_counts_and_enums(
    case: dict[str, object], expected_message: str
) -> None:
    assert acceptance._provider_failure_message(case, _expected()) == expected_message


@pytest.mark.parametrize(
    "case",
    [
        _case(elapsed_ms=True),
        _case(reads=[["bucket-a", "checkpoints/a", "version-a", True, _DIGEST_A]]),
        _case(reads=[["bucket-a", "checkpoints/a", "version-a", -1, _DIGEST_A]]),
        _case(reads=[["bucket-a", "checkpoints/a", "version-a", 3, ["private-digest"]]]),
        _case(reads="private-read-list"),
        _case(pages="private-page-list"),
        _case(attempted_reads=6_001),
        _case(pages=[{}] * 17),
        _case(status=None, outcome="report"),
        _case(outcome="private-outcome"),
        _case(status="private-status"),
        dict(
            _case(),
            report={
                "status": "traversed",
                "witnesses": [
                    {"terminal_reached": True, "unavailable_reads": 0},
                    {"terminal_reached": True, "unavailable_reads": 0},
                    {"terminal_reached": True, "unavailable_reads": 0},
                ],
            },
        ),
        dict(
            _case(),
            report={
                "status": "traversed",
                "witnesses": [{"terminal_reached": "private-flag", "unavailable_reads": 0}],
            },
        ),
        dict(
            _case(),
            report={
                "status": "traversed",
                "witnesses": [{"terminal_reached": True, "unavailable_reads": 6_001}],
            },
        ),
    ],
)
def test_provider_failure_message_rejects_malformed_or_private_values(
    case: dict[str, object],
) -> None:
    message = acceptance._provider_failure_message(case, _expected())

    assert message == _PREFIX + "unavailable"
    assert "private" not in message


@pytest.mark.parametrize(
    "mutate",
    [
        lambda case: case["reads"].pop(),
        lambda case: case["reads"].__setitem__(
            0, ["bucket-a", "changed", "version-a", 3, _DIGEST_A]
        ),
        lambda case: case["reads"].__setitem__(1, copy.deepcopy(case["reads"][0])),
        lambda case: case["reads"].__setitem__(
            0, ["bucket-a", "checkpoints/a", "version-a", 4, _DIGEST_B]
        ),
    ],
)
def test_first_provider_read_assertion_keeps_its_comparison_and_adds_receipt(
    mutate: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case()
    assert callable(mutate)
    mutate(case)
    monkeypatch.setattr(acceptance, "_assert_runtime", lambda _result: None)

    with pytest.raises(AssertionError) as caught:
        acceptance._assert_provider({"provider": case}, [], _expected(), [])

    assert str(caught.value).startswith(_PREFIX)
