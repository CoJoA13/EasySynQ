from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from easysynq_api.cli import restore as restore_cli
from easysynq_api.cli import upgrade as upgrade_cli

ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


async def _resolved_org_id() -> uuid.UUID:
    return ORG_ID


def test_restore_pass_is_integrity_only_not_cutover_authorization(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def run_restore(
        org_id: uuid.UUID, *, archive_path: str, audit_checkpoint_ack: bool
    ) -> dict[str, object]:
        assert org_id == ORG_ID
        assert archive_path == "/backups/verified.tar.enc"
        assert audit_checkpoint_ack is False
        return {
            "result": "PASS",
            "reason": "integrity checks passed",
            "scratch_db": "restore_easysynq_test",
            "scratch_bucket": "restore-scratch",
        }

    monkeypatch.setattr(restore_cli, "_resolve_org_id", _resolved_org_id)
    monkeypatch.setattr(restore_cli, "run_restore", run_restore)

    exit_code = restore_cli.main(["/backups/verified.tar.enc", "--confirm"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "verification target: db=restore_easysynq_test bucket=restore-scratch" in output
    assert "INTEGRITY VERIFICATION ONLY — NOT CUTOVER-READY" in output
    assert "source-store dependency" in output
    assert "currently configured object store" in output
    assert "next: cut over" not in output


def test_upgrade_failure_preserves_archive_pointer_without_safety_net_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = "/backups/pre-upgrade.tar.enc"

    async def run_upgrade(org_id: uuid.UUID) -> dict[str, object]:
        assert org_id == ORG_ID
        return {
            "result": "FAILED",
            "stage": "migrate",
            "reason": "RuntimeError: migration failed",
            "pre_backup_archive": archive,
        }

    monkeypatch.setattr(upgrade_cli, "_resolve_org_id", _resolved_org_id)
    monkeypatch.setattr(upgrade_cli, "run_upgrade", run_upgrade)

    exit_code = upgrade_cli.main(["--confirm"])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert f"pre-upgrade archive (non-self-contained): {archive}" in output
    assert "database dump + blob manifest; it does not contain object bytes" in output
    assert "keep the service closed and preserve the source object store" in output
    assert "pre-backup safety net" not in output
    assert "then cut over" not in output


def test_upgrade_ok_does_not_claim_production_eligibility(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def run_upgrade(org_id: uuid.UUID) -> dict[str, object]:
        assert org_id == ORG_ID
        return {
            "result": "OK",
            "head": "0123_head",
            "pre_backup_archive": "/backups/pre-upgrade.tar.enc",
        }

    monkeypatch.setattr(upgrade_cli, "_resolve_org_id", _resolved_org_id)
    monkeypatch.setattr(upgrade_cli, "run_upgrade", run_upgrade)

    exit_code = upgrade_cli.main(["--confirm"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "non-self-contained pre-upgrade archive" in output
    assert "command completion is not production eligibility" in output
    assert "remains blocked pending a self-contained recovery proof" in output
    assert "safety net" not in output


def test_host_cli_help_does_not_advertise_unsupported_recovery() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(  # noqa: S603 - fixed repository administration script
        [repo_root / "scripts/easysynq", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "integrity verification target" in result.stdout.lower()
    assert "not cutover-ready" in result.stdout.lower()
    assert "non-self-contained" in result.stdout.lower()
    assert "production eligibility" in result.stdout.lower()
    assert "remains blocked pending recovery proof" in result.stdout.lower()
    assert "preserve and investigate the exact source bytes" in result.stdout.lower()
    assert "separately validated direct repair" in result.stdout.lower()
    assert "re-run after a restore to clear the alarm" not in result.stdout.lower()
    assert "cut over per runbook" not in result.stdout.lower()
    assert "disaster safety net" not in result.stdout.lower()
