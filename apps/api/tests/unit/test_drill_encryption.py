"""The drill must round-trip a full encrypted archive through its destination."""

from pathlib import Path

import pytest

from easysynq_api.config import Settings
from easysynq_api.services.backup import archive, crypto, drill


@pytest.fixture
def drill_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(backup_encryption_key="synthetic-drill-encryption-key")

    def capture(_dsn: str, path: Path) -> tuple[dict[str, int], list[archive.BlobRef]]:
        path.write_bytes(b"PGDMP" + b"synthetic-database-content" * 1000)
        return {"organization": 1}, []

    monkeypatch.setattr(drill, "_capture_and_dump", capture)
    monkeypatch.setattr(drill, "_sweep_stale_scratch", lambda *_args: None)
    monkeypatch.setattr(drill, "_create_scratch_db", lambda *_args: None)
    monkeypatch.setattr(drill, "_drop_scratch_db", lambda *_args: None)
    monkeypatch.setattr(drill, "_scratch_worm_bucket_names", lambda *_args: set())
    monkeypatch.setattr(drill, "_preflight_scratch_target", lambda *_args, **_kw: None)
    monkeypatch.setattr(drill, "_copy_blobs", lambda *_args, **_kw: None)
    monkeypatch.setattr(drill, "_delete_scratch_objects", lambda *_args, **_kw: None)
    monkeypatch.setattr(
        drill, "run_triad", lambda *_args: drill.DrillResult("PASS", "restore verified")
    )
    return settings


@pytest.mark.parametrize("key", ["", "CHANGE_ME"])
def test_missing_key_fails_before_dump_or_destination_write(
    drill_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str
) -> None:
    captures = []
    monkeypatch.setattr(drill, "_capture_and_dump", lambda *_args: captures.append(True))
    destination = tmp_path / "destination"
    result = drill.run_drill(
        drill_settings.model_copy(update={"backup_encryption_key": key}),
        destination=str(destination),
    )
    assert result.result == "FAIL"
    assert "BACKUP_ENCRYPTION_KEY" in result.reason
    assert captures == []
    assert not destination.exists()


@pytest.mark.parametrize("fail_after_restore", [False, True])
def test_restores_destination_ciphertext_and_cleans_up(
    drill_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fail_after_restore: bool,
) -> None:
    destination = tmp_path / "destination"
    restored = []
    observed = []

    def restore(_dsn: str, _db: str, dump: Path) -> None:
        restored.append(dump.read_bytes())
        paths = list(destination.iterdir())
        observed.extend(p.name for p in paths)
        encrypted = next(p for p in paths if p.name.endswith(".tar.enc"))
        assert encrypted.read_bytes().startswith(b"ESQBKP")
        assert archive.verify_archive(encrypted)
        plain = crypto.decrypt_archive(
            encrypted, tmp_path / "returned.tar", secret=drill_settings.backup_encryption_key
        )
        manifest = archive.read_manifest(plain)
        assert manifest["encrypted"] is True
        assert manifest["encryption_key_ref"] == crypto.ENCRYPTION_KEY_REF
        assert encrypted.stat().st_size > len(restored[0])

    def after_restore(_handle: drill.ScratchHandle) -> None:
        if fail_after_restore:
            raise archive.BackupError("synthetic post-restore failure")

    monkeypatch.setattr(archive, "restore_database", restore)
    result = drill.run_drill(
        drill_settings, destination=str(destination), after_restore=after_restore
    )
    assert result.result == ("FAIL" if fail_after_restore else "PASS"), result
    if fail_after_restore:
        assert result.reason == "synthetic post-restore failure"
    assert restored == [b"PGDMP" + b"synthetic-database-content" * 1000]
    assert len(observed) == 2
    assert all(name.endswith((".tar.enc", ".tar.enc.sha256")) for name in observed)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("stage", ["encrypt", "sidecar", "checksum", "authentication"])
def test_failed_destination_roundtrip_never_restores_and_cleans_residue(
    drill_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str
) -> None:
    destination = tmp_path / "destination"
    restored = []
    written = []
    real_encrypt = crypto.encrypt_archive
    real_sidecar = archive.write_sidecar

    def encrypt(plain: Path, target: Path, **kwargs: str) -> Path:
        result = real_encrypt(plain, target, **kwargs)
        written.append(target.read_bytes())
        if stage == "encrypt":
            raise OSError("synthetic interrupted ciphertext write")
        return result

    def sidecar(path: Path) -> Path:
        result = real_sidecar(path)
        if path.parent == destination:
            if stage == "sidecar":
                raise OSError("synthetic interrupted sidecar write")
            damaged = path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 1])
            path.write_bytes(damaged)
            if stage == "authentication":
                real_sidecar(path)  # checksum alone cannot authenticate a tampered archive
        return result

    monkeypatch.setattr(crypto, "encrypt_archive", encrypt)
    monkeypatch.setattr(archive, "write_sidecar", sidecar)
    monkeypatch.setattr(archive, "restore_database", lambda *_args: restored.append(True))
    result = drill.run_drill(drill_settings, destination=str(destination))
    assert result.result == "FAIL", result
    assert len(written) == 1 and written[0].startswith(b"ESQBKP")
    assert restored == []
    assert list(destination.iterdir()) == []


def test_cleanup_failure_strands_only_ciphertext(
    drill_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "destination"
    unlink = Path.unlink

    def deny_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path.parent == destination:
            raise OSError("synthetic destination unlink denied")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    monkeypatch.setattr(archive, "restore_database", lambda *_args: None)
    result = drill.run_drill(drill_settings, destination=str(destination))
    assert result.result == "PASS", result
    files = list(destination.iterdir())
    assert len(files) == 2
    assert not list(destination.glob("*.tar"))
    encrypted = next(p for p in files if p.name.endswith(".tar.enc"))
    assert archive.verify_archive(encrypted)
    assert encrypted.read_bytes().startswith(b"ESQBKP")
