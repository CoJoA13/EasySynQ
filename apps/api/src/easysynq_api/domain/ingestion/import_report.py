"""The §12.1 Import Report — a pure markdown renderer (slice S-ing-5, doc 09 §12.1).

The commit worker assembles a plain ``ImportReportData`` from the run + the commit ledger + the
staging tables and renders it to human-readable markdown, which is then WORM-sealed as an immutable
RETAIN_PERMANENT EVIDENCE Record (and exported to ``_ImportReport/`` in the mirror). Kept pure (no
IO, no ORM) so it is trivially unit-testable. The disposition table lists the actionable items
(committed + failed) explicitly and summarises the bulk (excluded/redundant/quarantined) by count —
a faithful, bounded record of how the QMS was populated (a literal per-file row for a
multi-thousand-file import would be unwieldy; the per-file outcomes also live in the run counts +
the staging tables until TTL)."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class CommittedItem:
    identifier: str
    kind: str
    source_rel_path: str
    decided_by: str  # engine_confirmed | human_corrected


@dataclasses.dataclass(frozen=True, slots=True)
class FailedItem:
    source_rel_path: str
    error: str


@dataclasses.dataclass(frozen=True, slots=True)
class ImportReportData:
    run_id: str
    source_root: str
    created_by: str | None
    committed_by: str | None
    classifier_version: str | None
    final_status: str
    counts: dict[str, object]
    committed: list[CommittedItem]
    failed: list[FailedItem]
    star_coverage: dict[str, object] | None


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def render_import_report(data: ImportReportData) -> str:
    """Render the §12.1 Import Report as markdown."""
    lines: list[str] = [
        f"# Import Report — {data.source_root}",
        "",
        "## Run",
        "",
        *_table(
            ["Field", "Value"],
            [
                ["Run", data.run_id],
                ["Source root", data.source_root],
                ["Created by", data.created_by or "—"],
                ["Committed by", data.committed_by or "—"],
                ["Classifier", data.classifier_version or "—"],
                ["Final status", data.final_status],
                ["Committed", str(len(data.committed))],
                ["Failed", str(len(data.failed))],
            ],
        ),
        "",
        "## Counts",
        "",
    ]
    counts = data.counts or {}
    if counts:
        lines += _table(
            ["Key", "Value"], [[k, str(v)] for k, v in sorted(counts.items(), key=lambda kv: kv[0])]
        )
    else:
        lines.append("_(no counts recorded)_")
    lines += ["", "## Committed items", ""]
    if data.committed:
        lines += _table(
            ["Identifier", "Kind", "Source", "Classification"],
            [
                [c.identifier, c.kind, c.source_rel_path, c.decided_by]
                for c in sorted(data.committed, key=lambda c: c.identifier)
            ],
        )
    else:
        lines.append("_(nothing committed)_")
    lines += ["", "## Failed items", ""]
    if data.failed:
        lines += _table(
            ["Source", "Error"],
            [
                [f.source_rel_path, f.error]
                for f in sorted(data.failed, key=lambda f: f.source_rel_path)
            ],
        )
    else:
        lines.append("_(no failures)_")
    lines += ["", "## Mandatory ★ coverage (advisory)", ""]
    if data.star_coverage:
        sc = data.star_coverage
        lines += _table(
            ["Metric", "Value"],
            [
                [k, str(v)]
                for k, v in sorted(sc.items(), key=lambda kv: kv[0])
                if not isinstance(v, (list, dict))
            ],
        )
    else:
        lines.append("_(coverage not computed)_")
    lines.append("")
    return "\n".join(lines)
