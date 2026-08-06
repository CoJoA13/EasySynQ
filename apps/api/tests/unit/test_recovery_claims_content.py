"""Static guard against reintroducing known false recovery claims on active surfaces.

This intentionally scans a named allowlist. Historical evidence (`docs/slice-history.md`, doc 18),
tests, generated contract output, and stable internal identifiers are excluded by construction.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]

ACTIVE_SURFACES = (
    "README.md",
    "scripts/easysynq",
    "apps/api/src/easysynq_api/cli/backup.py",
    "apps/api/src/easysynq_api/cli/restore.py",
    "apps/api/src/easysynq_api/services/common/pg_locks.py",
    "apps/api/src/easysynq_api/services/backup/__init__.py",
    "apps/api/src/easysynq_api/services/backup/drill.py",
    "apps/api/src/easysynq_api/services/backup/restore.py",
    "apps/api/src/easysynq_api/services/backup/service.py",
    "apps/api/src/easysynq_api/services/upgrade.py",
    "apps/api/src/easysynq_api/tasks/app.py",
    "apps/api/src/easysynq_api/tasks/backup.py",
    "apps/api/src/easysynq_api/tasks/blob_verify.py",
    "apps/web/src/SetupWizard.tsx",
    "apps/web/src/features/drift/DriftStatusPage.tsx",
    "docs/03-architecture-and-stack.md",
    "docs/08-setup-and-onboarding.md",
    "docs/11-ui-ux-design-system.md",
    "docs/12-security-and-audit.md",
    "docs/16-roadmap.md",
    "docs/dev-workflow.md",
    "docs/manuals/administrator-it-manual.md",
    "docs/runbooks/00-index.md",
    "docs/runbooks/backup-restore.md",
    "docs/runbooks/blob-integrity-verify.md",
    "packages/contracts/openapi.yaml",
)

FORBIDDEN_CLAIMS = {
    "recoverability asserted real": re.compile(r"recoverability\s+are\s+real", re.IGNORECASE),
    "everything asserted recoverable": re.compile(
        r"everything\s+is\s+audited\s+and\s+recoverable", re.IGNORECASE
    ),
    "retained archive asserted still restorable": re.compile(r"\bstill\s+restore\b", re.IGNORECASE),
    "archive called a real backup": re.compile(r"\breal\s+backup\b", re.IGNORECASE),
    "restore claimed to actually work": re.compile(
        r"prove\s+(?:a\s+)?restore\s+actually\s+works", re.IGNORECASE
    ),
    "restore-from-backup repair": re.compile(r"restore[- ]from[- ]backup", re.IGNORECASE),
    "operator-grade live restore": re.compile(
        r"operator[- ]grade[^\n]{0,50}\blive\b[^\n]{0,30}\brestore\b", re.IGNORECASE
    ),
    "end-to-end restore proof": re.compile(r"end[- ]to[- ]end\s+restore[- ]test", re.IGNORECASE),
    "setup test restore asserted recovery proof": re.compile(
        r"setup\s+proves\s+a\s+\*\*test\s+restore\s+before\s+data\s+lands\*\*",
        re.IGNORECASE,
    ),
    "archive called restorable and intact": re.compile(r"restorable\s*\+\s*intact", re.IGNORECASE),
    "archive called a recovery artifact": re.compile(
        r"unusable\s+recovery\s+artifact", re.IGNORECASE
    ),
    "archive called the recovery pointer": re.compile(r"only\s+recovery\s+pointer", re.IGNORECASE),
    "alarm asserted resolved by restore": re.compile(
        r"re-alarming\s+every\s+scan\s+until\s+restored", re.IGNORECASE
    ),
    "backup called trustworthy after restore": re.compile(
        r"backup\s+is\s+only\s+trustworthy\s+once\s+it\s+has\s+been\s+restored",
        re.IGNORECASE,
    ),
    "pre-upgrade archive called safety net": re.compile(
        r"pre-backup\s+safety\s+net", re.IGNORECASE
    ),
    "restore output directs cutover": re.compile(r"next:\s*cut\s+over", re.IGNORECASE),
    "recoverability asserted before data": re.compile(
        r"recoverability\s+before\s+data", re.IGNORECASE
    ),
    "setup asserted to prove recoverability": re.compile(
        r"prove(?:s|d)?\s+recoverability", re.IGNORECASE
    ),
    "archive marketed as real": re.compile(r"\bwrites?\s+a\s+real\b", re.IGNORECASE),
}


def test_active_surfaces_do_not_claim_source_independent_recovery() -> None:
    violations: list[str] = []
    for relative_path in ACTIVE_SURFACES:
        for line_number, line in enumerate(
            (ROOT / relative_path).read_text().splitlines(), start=1
        ):
            for claim, pattern in FORBIDDEN_CLAIMS.items():
                if pattern.search(line):
                    violations.append(f"{relative_path}:{line_number}: {claim}: {line.strip()}")

    assert violations == [], "active recovery claim contradictions:\n" + "\n".join(violations)
