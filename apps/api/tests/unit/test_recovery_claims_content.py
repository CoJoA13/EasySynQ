"""Guard known recovery overclaims on explicitly named, current operator surfaces.

Historical evidence (`docs/slice-history.md` and doc 18), tests, generated contract output, and
stable internal identifiers are excluded by construction. The rules below target affirmative claim
shapes that previously shipped; they deliberately do not reject honest negative limitations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]

# Explicit union of the current, non-test truth surfaces changed across the Task 5 closure commits,
# plus the owner-approved setup mockups and the living OpenAPI source. Do not replace this with a
# repository-wide prose scan: historical/spec evidence and compatibility identifiers are
# intentional.
ACTIVE_SURFACES = (
    ".env.example",
    "README.md",
    "scripts/easysynq",
    "apps/api/src/easysynq_api/cli/backup.py",
    "apps/api/src/easysynq_api/cli/restore.py",
    "apps/api/src/easysynq_api/cli/upgrade.py",
    "apps/api/src/easysynq_api/services/backup/__init__.py",
    "apps/api/src/easysynq_api/services/backup/drill.py",
    "apps/api/src/easysynq_api/services/backup/realm_export.py",
    "apps/api/src/easysynq_api/services/backup/restore.py",
    "apps/api/src/easysynq_api/services/backup/service.py",
    "apps/api/src/easysynq_api/services/common/pg_locks.py",
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
    "docs/runbooks/install-ubuntu-server.md",
    "docs/runbooks/key-rotation.md",
    "docs/runbooks/minio-object-lock-prereq.md",
    "docs/runbooks/spof-fast-restart.md",
    "infra/compose/compose.yml",
    "mockup/easysynq-mockup.html",
    "mockup/screens/setup.html",
    "packages/contracts/openapi.yaml",
)

# These high-risk paths get an independent membership pin so a future allowlist refactor cannot
# silently omit the CLIs, owner-approved mockups, installation guidance, or shipped environment.
REQUIRED_ACTIVE_SURFACES = frozenset(
    {
        ".env.example",
        "apps/api/src/easysynq_api/cli/restore.py",
        "apps/api/src/easysynq_api/cli/upgrade.py",
        "docs/runbooks/install-ubuntu-server.md",
        "infra/compose/compose.yml",
        "mockup/easysynq-mockup.html",
        "mockup/screens/setup.html",
    }
)


@dataclass(frozen=True, order=True)
class ClaimRule:
    name: str
    pattern: re.Pattern[str]


@dataclass(frozen=True, order=True)
class Violation:
    path: str
    line_number: int
    claim: str
    excerpt: str


def _rule(name: str, pattern: str) -> ClaimRule:
    return ClaimRule(name, re.compile(pattern, re.IGNORECASE | re.MULTILINE | re.DOTALL))


# Context-specific affirmative forms that previously shipped. Avoid generic tokens such as
# "real backup", "restore-from-backup", or "prove recoverability": those are negation-blind and
# would reject truthful sentences such as "This is not a real backup."
FORBIDDEN_CLAIMS = (
    _rule(
        "restore_cli_cutover_instruction",
        r"^[ \t]*(?:print\([ \t]*f?[\"'][ \t]*)?next:[ \t]*cut[ \t]+over\b",
    ),
    _rule(
        "upgrade_cli_safety_net",
        r"^[ \t]*(?:print\([ \t]*f?[\"'][ \t]*)?pre-backup[ \t]+safety[ \t]+net[ \t]*:",
    ),
    _rule(
        "upgrade_cli_restore_then_cutover",
        r"^[ \t]*(?:print\([ \t]*f?[\"'][ \t]*)?to recover:[ \t]*easysynq restore\b"
        r".{0,100}\bthen cut[ \t]+over\b",
    ),
    _rule(
        "setup_claims_restore_proof",
        r"configure admin-controlled backups,\s*then prove a restore actually works\b",
    ),
    _rule(
        "setup_mockup_promotes_drill_to_recovery",
        r"(?:a backup is only trustworthy once it has been restored|"
        r"runs an end-to-end backup.{0,160}restores it into an isolated scratch namespace)",
    ),
    _rule(
        "drift_claims_restore_from_backup_repair",
        r"unresolved integrity findings.{0,160}restore from backup,\s*then re-run the verify",
    ),
    _rule(
        "setup_claims_recoverability_is_real",
        r"source-of-truth and recoverability are real before any content lands",
    ),
    _rule(
        "setup_claims_everything_recoverable",
        r"everything is audited and recoverable\s*:",
    ),
    _rule(
        "security_doc_claims_restore_establishes_trust",
        r"the only way to trust a backup is to have restored one",
    ),
    _rule(
        "roadmap_claims_setup_recovery_proof",
        r"(?:setup proves a \*\*test restore before data lands\*\*|"
        r"guided first-run that proves recoverability before data lands|"
        r"authority \+ recoverability before data|"
        r"\(recoverability before data\))",
    ),
    _rule(
        "scheduler_claims_real_backup",
        r"silently-rotting real backup",
    ),
    _rule(
        "scheduler_claims_retained_archive_restores",
        r"the stored(?:,\s*encrypted)? ones still restore",
    ),
    _rule(
        "backup_prose_calls_archive_real",
        r"(?:(?:produces|runs) a real backup(?: archive)?|"
        r"writes? a real,\s*(?:timestamped,\s*)?checksum-verified (?:backup )?archive)",
    ),
    _rule(
        "backup_prose_claims_retained_archive_restorable",
        r"(?:backup archive.{0,80}is restorable\s*\+\s*intact|"
        r"^\s*restorable\s*\+\s*intact\s+—\s+the gap)",
    ),
    _rule(
        "operator_prose_calls_scratch_restore_live",
        r"operator-grade worm-aware \*?live\*? restore|operator-grade live restore "
        r"\(restore-to-verified-target\)",
    ),
    _rule(
        "upgrade_prose_calls_archive_only_recovery_pointer",
        r"operator's only recovery pointer",
    ),
    _rule(
        "blob_alarm_claims_restore_resolves_failure",
        r"re-alarming every scan until restored",
    ),
)


def _claim_names(line: str) -> frozenset[str]:
    return frozenset(rule.name for rule in FORBIDDEN_CLAIMS if rule.pattern.search(line))


def _violations(relative_path: str, text: str) -> set[Violation]:
    by_pattern_and_line: dict[tuple[str, int, str], Violation] = {}
    for rule in FORBIDDEN_CLAIMS:
        for match in rule.pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            key = (relative_path, line_number, rule.name)
            by_pattern_and_line.setdefault(
                key,
                Violation(
                    relative_path,
                    line_number,
                    rule.name,
                    " ".join(match.group(0).split()),
                ),
            )
    return set(by_pattern_and_line.values())


@pytest.mark.parametrize(
    "honest_disclaimer",
    (
        "This is not a real backup.",
        "This check does not prove recoverability.",
        "No restore-from-backup procedure is supported.",
        "An unusable recovery artifact cannot authorize migration.",
    ),
)
def test_guard_accepts_honest_negative_limitations(honest_disclaimer: str) -> None:
    assert _claim_names(honest_disclaimer) == frozenset()


FORMER_CLAIM_ORACLES = (
    ("restore_cli_cutover_instruction", "  next: cut over per the restore runbook"),
    ("upgrade_cli_safety_net", "  pre-backup safety net: /backups/example.tar.enc"),
    (
        "upgrade_cli_restore_then_cutover",
        "  to recover: easysynq restore <pre-backup> then cut over (runbook)",
    ),
    (
        "setup_claims_restore_proof",
        "Configure admin-controlled backups, then prove a restore actually works.",
    ),
    (
        "setup_mockup_promotes_drill_to_recovery",
        "A backup is only trustworthy once it has been restored.",
    ),
    (
        "drift_claims_restore_from_backup_repair",
        "{cov.failing} unresolved integrity findings — re-alarming until restored. See the\n"
        "runbook (restore from backup, then re-run the verify).",
    ),
    (
        "setup_claims_recoverability_is_real",
        "The gates ensure the source-of-truth and recoverability are real before any content "
        "lands.",
    ),
    (
        "setup_claims_everything_recoverable",
        "Everything is audited and recoverable: every action writes audit.",
    ),
    (
        "security_doc_claims_restore_establishes_trust",
        "The only way to trust a backup is to have restored one.",
    ),
    (
        "roadmap_claims_setup_recovery_proof",
        "Setup proves a **test restore before data lands**.",
    ),
    ("scheduler_claims_real_backup", "Catch a silently-rotting REAL backup weekly."),
    (
        "scheduler_claims_retained_archive_restores",
        "This proves the stored, encrypted ones still restore.",
    ),
    (
        "backup_prose_calls_archive_real",
        "The drill produces a real backup archive at the configured destination.",
    ),
    (
        "backup_prose_claims_retained_archive_restorable",
        "Verify the retained backup archive is restorable + intact.",
    ),
    (
        "operator_prose_calls_scratch_restore_live",
        "S11: serialize the operator-grade LIVE restore (restore-to-verified-target).",
    ),
    (
        "upgrade_prose_calls_archive_only_recovery_pointer",
        "The operator's only recovery pointer is the pre-backup archive.",
    ),
    (
        "blob_alarm_claims_restore_resolves_failure",
        "Unresolved rows keep re-alarming every scan until restored.",
    ),
)


@pytest.mark.parametrize(("expected_claim", "former_claim"), FORMER_CLAIM_ORACLES)
def test_guard_detects_representative_former_claims(expected_claim: str, former_claim: str) -> None:
    assert expected_claim in _claim_names(former_claim)


def test_every_forbidden_claim_rule_has_a_positive_oracle() -> None:
    exercised_rules = {expected_claim for expected_claim, _former_claim in FORMER_CLAIM_ORACLES}
    assert exercised_rules == {rule.name for rule in FORBIDDEN_CLAIMS}


def test_multiline_claim_reports_starting_line_and_normalized_excerpt() -> None:
    former_drift_source = (
        "unrelated first line\n"
        "{cov.failing} unresolved integrity findings — re-alarming until restored. See the\n"
        "runbook (restore from backup, then re-run the verify).\n"
    )

    assert _violations("DriftStatusPage.tsx", former_drift_source) == {
        Violation(
            "DriftStatusPage.tsx",
            2,
            "drift_claims_restore_from_backup_repair",
            "unresolved integrity findings — re-alarming until restored. See the runbook "
            "(restore from backup, then re-run the verify",
        )
    }


def test_required_high_risk_surfaces_are_pinned_and_exist() -> None:
    missing_from_allowlist = REQUIRED_ACTIVE_SURFACES.difference(ACTIVE_SURFACES)
    missing_from_checkout = {
        path for path in REQUIRED_ACTIVE_SURFACES if not (ROOT / path).is_file()
    }

    assert missing_from_allowlist == frozenset()
    assert missing_from_checkout == set()


def test_active_surfaces_do_not_make_known_affirmative_recovery_overclaims() -> None:
    violations = sorted(
        violation
        for relative_path in ACTIVE_SURFACES
        for violation in _violations(relative_path, (ROOT / relative_path).read_text())
    )
    unique_lines = {(item.path, item.line_number) for item in violations}
    files = {item.path for item in violations}
    details = "\n".join(
        f"{item.path}:{item.line_number}: [{item.claim}] {item.excerpt}" for item in violations
    )

    assert violations == [], (
        f"{len(violations)} violation records across {len(unique_lines)} unique lines "
        f"in {len(files)} files:\n{details}"
    )
