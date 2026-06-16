"""Pin: the scheduled retained-backup verify selects the newest DURABLE archive and never a stray
restore-drill residue (Phase-1 I-7 / Codex P2 #155).

``_newest_retained_archive`` runs over ``policy.destination`` — the SAME directory the on-demand
G-C drill writes its transient ``easysynq-backup-<32-hex-uuid>.tar`` into. The drill unlinks it in
its ``finally``, but a hard-killed drill can leave one behind; because a bare-uuid stamp lexically
outsorts the year-prefixed durable stamp most of the time, a naive lexical-max over both families
would pick the (plaintext) residue and ``verify`` it PASS WITHOUT ever decrypting the real encrypted
backup. The finder must match the durable ``YYYYMMDDTHHMMSSZ-<uuid8>`` stamp EXACTLY.
"""

from pathlib import Path

from easysynq_api.services.backup.drill import _newest_retained_archive


def _touch(d: Path, name: str) -> None:
    (d / name).write_text("x")


def test_returns_none_when_absent_or_empty(tmp_path: Path) -> None:
    assert _newest_retained_archive(str(tmp_path / "does-not-exist")) is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _newest_retained_archive(str(empty)) is None


def test_picks_newest_durable_and_excludes_sidecars(tmp_path: Path) -> None:
    for name in (
        "easysynq-backup-20260101T000000Z-aaaaaaaa.tar.enc",
        "easysynq-backup-20260101T000000Z-aaaaaaaa.tar.enc.sha256",
        "easysynq-backup-20260615T120000Z-bbbbbbbb.tar.enc",
        "easysynq-backup-20260615T120000Z-bbbbbbbb.tar.enc.sha256",
        "easysynq-backup-20260301T000000Z-cccccccc.tar",  # keyless plaintext durable
        "easysynq-backup-20260301T000000Z-cccccccc.tar.sha256",
        "some-other-file.txt",
    ):
        _touch(tmp_path, name)
    newest = _newest_retained_archive(str(tmp_path))
    assert newest is not None
    # The June stamp is the chronological max; a .sha256 sidecar is never selected.
    assert newest.name == "easysynq-backup-20260615T120000Z-bbbbbbbb.tar.enc"


def test_ignores_a_hard_crash_drill_residue_even_when_it_lexically_outsorts(tmp_path: Path) -> None:
    """The regression: a drill residue whose bare-uuid stamp begins with 'f' sorts ABOVE every
    '2'-prefixed durable name, yet must NOT be picked — else the verify validates a plaintext drill
    artifact and never decrypts the real encrypted backup (the exact Codex-P2 gap)."""
    durable = "easysynq-backup-20260615T120000Z-bbbbbbbb.tar.enc"
    _touch(tmp_path, durable)
    _touch(tmp_path, durable + ".sha256")
    # A hard-killed run_drill leaves a bare-uuid32 plaintext .tar (+ sidecar) in the SAME dir.
    drill_residue = "easysynq-backup-fdeadbeefdeadbeefdeadbeefdeadbeef.tar"
    _touch(tmp_path, drill_residue)
    _touch(tmp_path, drill_residue + ".sha256")
    assert "f" > "2"  # the residue WOULD win a naive lexical-max

    newest = _newest_retained_archive(str(tmp_path))
    assert newest is not None
    assert newest.name == durable, newest.name  # the durable archive, not the residue


def test_returns_none_when_only_a_drill_residue_is_present(tmp_path: Path) -> None:
    """A directory holding ONLY a stray drill residue (no durable archive yet) is SKIP-worthy — the
    finder returns None rather than offering the residue to the verifier."""
    _touch(tmp_path, "easysynq-backup-fdeadbeefdeadbeefdeadbeefdeadbeef.tar")
    _touch(tmp_path, "easysynq-backup-fdeadbeefdeadbeefdeadbeefdeadbeef.tar.sha256")
    assert _newest_retained_archive(str(tmp_path)) is None
