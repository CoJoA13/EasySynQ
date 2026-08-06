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

MOCKUP_SETUP_SURFACES = (
    "mockup/easysynq-mockup.html",
    "mockup/screens/setup.html",
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
    _rule(
        "blob_task_claims_restore_from_backup_runbook_action",
        r"blobs\s*[—-]\s*restore-from-backup is the runbook action",
    ),
)

# Supported direct-negation grammar is deliberately small and adjacency-based. A match is negated
# only by one of these same-line forms ending exactly where the guarded text starts:
#
# * ``do not [claim that] <claim>`` / ``is not [a|an|the] <claim>``;
# * ``not true that [the] <claim>`` / ``false that [the] <claim>``; or
# * ``no <claim>`` / ``never <claim>``.
#
# The corresponding postfix form must begin exactly where the match ends: ``<claim> is not
# supported`` or ``<claim> — not true`` (with the other explicit status words below). This is not a
# prose-window heuristic: any intervening word, sentence, or line break prevents the exemption.
_DIRECT_NEGATION_PREFIX = re.compile(
    r"(?:"
    r"\b(?:do|does|did)[ \t]+not[ \t]+(?:claim[ \t]+that[ \t]+)?|"
    r"\b(?:is|are|was|were)[ \t]+not[ \t]+(?:(?:a|an|the)[ \t]+)?|"
    r"\b(?:not[ \t]+true|false)[ \t]+that[ \t]+(?:the[ \t]+)?|"
    r"\b(?:no|never)[ \t]+"
    r")\Z",
    re.IGNORECASE,
)
_DIRECT_NEGATION_SUFFIX = re.compile(
    r"\A[ \t]*(?:"
    r"(?:is|are|was|were)[ \t]+not[ \t]+"
    r"(?:supported|true|shipped|implemented|available|enabled|provided|allowed)|"
    r"(?:[—-][ \t]*)?not[ \t]+"
    r"(?:supported|true|shipped|implemented|available|enabled|provided|allowed)|"
    r"(?:[—-][ \t]*)?(?:false|unsupported|unavailable|unshipped|disabled)"
    r")\b",
    re.IGNORECASE,
)


def _is_supported_direct_negation(text: str, match: re.Match[str]) -> bool:
    return bool(
        _DIRECT_NEGATION_PREFIX.search(text[: match.start()])
        or _DIRECT_NEGATION_SUFFIX.match(text[match.end() :])
    )


def _violations(relative_path: str, text: str) -> set[Violation]:
    by_pattern_and_line: dict[tuple[str, int, str], Violation] = {}
    for rule in FORBIDDEN_CLAIMS:
        for match in rule.pattern.finditer(text):
            if _is_supported_direct_negation(text, match):
                continue
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
    assert _violations("honest-disclaimer.txt", honest_disclaimer) == set()


CLAIM_ORACLES = (
    (
        "restore_cli_cutover_instruction",
        "  next: cut over per the restore runbook",
        "next: cut over is not supported.",
    ),
    (
        "upgrade_cli_safety_net",
        "  pre-backup safety net: /backups/example.tar.enc",
        "pre-backup safety net: not provided.",
    ),
    (
        "upgrade_cli_restore_then_cutover",
        "  to recover: easysynq restore <pre-backup> then cut over (runbook)",
        "to recover: easysynq restore <pre-backup> then cut over is not supported.",
    ),
    (
        "setup_claims_restore_proof",
        "Configure admin-controlled backups, then prove a restore actually works.",
        "Do not configure admin-controlled backups, then prove a restore actually works.",
    ),
    (
        "setup_mockup_promotes_drill_to_recovery",
        "A backup is only trustworthy once it has been restored.",
        "It is not true that a backup is only trustworthy once it has been restored.",
    ),
    (
        "drift_claims_restore_from_backup_repair",
        "{cov.failing} unresolved integrity findings — re-alarming until restored. See the\n"
        "runbook (restore from backup, then re-run the verify).",
        "Do not claim that unresolved integrity findings require operators to restore from "
        "backup, then re-run the verify.",
    ),
    (
        "setup_claims_recoverability_is_real",
        "The gates ensure the source-of-truth and recoverability are real before any content "
        "lands.",
        "It is not true that the source-of-truth and recoverability are real before any "
        "content lands.",
    ),
    (
        "setup_claims_everything_recoverable",
        "Everything is audited and recoverable: every action writes audit.",
        "It is not true that everything is audited and recoverable:",
    ),
    (
        "security_doc_claims_restore_establishes_trust",
        "The only way to trust a backup is to have restored one.",
        "It is not true that the only way to trust a backup is to have restored one.",
    ),
    (
        "roadmap_claims_setup_recovery_proof",
        "Setup proves a **test restore before data lands**.",
        "It is not true that setup proves a **test restore before data lands**.",
    ),
    (
        "scheduler_claims_real_backup",
        "Catch a silently-rotting REAL backup weekly.",
        "This is not a silently-rotting REAL backup.",
    ),
    (
        "scheduler_claims_retained_archive_restores",
        "This proves the stored, encrypted ones still restore.",
        "It is not true that the stored, encrypted ones still restore.",
    ),
    (
        "backup_prose_calls_archive_real",
        "The drill produces a real backup archive at the configured destination.",
        "The drill produces a real backup archive — not true.",
    ),
    (
        "backup_prose_claims_retained_archive_restorable",
        "Verify the retained backup archive is restorable + intact.",
        "It is not true that the backup archive is restorable + intact.",
    ),
    (
        "operator_prose_calls_scratch_restore_live",
        "S11: serialize the operator-grade LIVE restore (restore-to-verified-target).",
        "S11 is not an operator-grade live restore (restore-to-verified-target).",
    ),
    (
        "upgrade_prose_calls_archive_only_recovery_pointer",
        "The operator's only recovery pointer is the pre-backup archive.",
        "This is not the operator's only recovery pointer.",
    ),
    (
        "blob_alarm_claims_restore_resolves_failure",
        "Unresolved rows keep re-alarming every scan until restored.",
        "This task is not re-alarming every scan until restored.",
    ),
    (
        "blob_task_claims_restore_from_backup_runbook_action",
        "blobs — restore-from-backup is the runbook action",
        "For blobs — restore-from-backup is the runbook action — not true.",
    ),
)


@pytest.mark.parametrize(("expected_claim", "former_claim", "_direct_negative"), CLAIM_ORACLES)
def test_guard_detects_representative_former_claims_through_violations(
    expected_claim: str, former_claim: str, _direct_negative: str
) -> None:
    claims = {item.claim for item in _violations("former-claim.txt", former_claim)}
    assert expected_claim in claims


@pytest.mark.parametrize(("_expected_claim", "_former_claim", "direct_negative"), CLAIM_ORACLES)
def test_guard_accepts_direct_negation_of_each_guarded_claim(
    _expected_claim: str, _former_claim: str, direct_negative: str
) -> None:
    assert _violations("direct-negative.txt", direct_negative) == set()


def test_every_forbidden_claim_rule_has_exactly_one_both_polarity_oracle() -> None:
    oracle_names = [expected_claim for expected_claim, _former, _negative in CLAIM_ORACLES]

    assert len(oracle_names) == len(set(oracle_names))
    assert set(oracle_names) == {rule.name for rule in FORBIDDEN_CLAIMS}


@pytest.mark.parametrize(
    "unrelated_negation",
    (
        "This note is not a cutover instruction; nevertheless, ",
        "No recovery generation is shipped; nevertheless, ",
        "Never infer source independence; nevertheless, ",
    ),
)
def test_unrelated_nearby_negation_does_not_hide_affirmative_claim(
    unrelated_negation: str,
) -> None:
    text = unrelated_negation + "the drill produces a real backup archive."

    claims = {item.claim for item in _violations("nearby-negation.txt", text)}

    assert "backup_prose_calls_archive_real" in claims


@pytest.mark.parametrize(
    "direct_negative",
    (
        "No silently-rotting real backup exists.",
        "The drill never produces a real backup archive.",
    ),
)
def test_guard_accepts_supported_direct_no_and_never_forms(direct_negative: str) -> None:
    assert _violations("direct-no-never.txt", direct_negative) == set()


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


def test_historical_blob_runbook_claim_reports_starting_line() -> None:
    former_blob_task_source = (
        "Daily rolling re-hash of vault blobs.\n"
        "A finding re-alarms until the operator restores the object (no auto-correction for\n"
        "blobs — restore-from-backup is the runbook action); the pin clears on a pass.\n"
    )

    assert _violations("tasks/blob_verify.py", former_blob_task_source) == {
        Violation(
            "tasks/blob_verify.py",
            3,
            "blob_task_claims_restore_from_backup_runbook_action",
            "blobs — restore-from-backup is the runbook action",
        )
    }


@pytest.mark.parametrize("relative_path", MOCKUP_SETUP_SURFACES)
def test_mockup_backup_destination_is_filesystem_only(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text()

    assert 'value="/var/lib/easysynq/backups"' in text
    assert "Mounted local/NFS filesystem path" in text
    active_s3_field = re.search(
        r'<input\b[^>]*(?:value|placeholder)="[^"]*(?:s3://|s3-compatible bucket)',
        text,
        re.IGNORECASE,
    )
    assert active_s3_field is None


@pytest.mark.parametrize("relative_path", MOCKUP_SETUP_SURFACES)
def test_mockup_pitr_is_explicitly_unshipped(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text()

    active_pitr_switch = re.search(
        r'<span class="es-switch is-on".{0,320}WAL / point-in-time recovery \(PITR\)',
        text,
        re.DOTALL,
    )
    assert active_pitr_switch is None
    assert "WAL / point-in-time recovery (PITR) · not shipped" in text
    assert "Archive reachable" not in text
    assert "Streams write-ahead logs for sub-day recovery" not in text


@pytest.mark.parametrize("relative_path", MOCKUP_SETUP_SURFACES)
def test_mockup_retention_pruning_is_explicitly_unshipped(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text()

    assert "Retention counts are recorded only; automatic pruning is not shipped." in text
    for count in ("7", "4", "6"):
        assert f'<input class="es-input es-num" value="{count}" disabled>' in text


@pytest.mark.parametrize("relative_path", MOCKUP_SETUP_SURFACES)
def test_mockup_does_not_claim_current_pitr_rpo_benefit(relative_path: str) -> None:
    text = (ROOT / relative_path).read_text()

    assert "PITR narrows to minutes" not in text
    assert "nightly filesystem archive · no current WAL/PITR benefit" in text


@pytest.mark.parametrize(
    "marker",
    (
        '<label class="es-label">Backup destination</label>',
        "WAL / point-in-time recovery (PITR)",
        '<span class="es-metric__label">Est. RPO</span>',
    ),
)
def test_corresponding_setup_mockup_lines_are_byte_aligned(marker: str) -> None:
    lines = []
    for relative_path in MOCKUP_SETUP_SURFACES:
        matching_lines = [
            line.strip()
            for line in (ROOT / relative_path).read_text().splitlines()
            if marker in line
        ]
        assert len(matching_lines) == 1
        lines.append(matching_lines[0])

    assert lines[0] == lines[1]


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
