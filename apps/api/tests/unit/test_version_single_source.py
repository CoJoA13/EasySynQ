"""Audit U42 — the repo's ``VERSION`` file and ``Settings.version`` must not drift.

Two places carry the release string. ``VERSION`` is what ``scripts/app-images.sh`` tags the
air-gap bundle's images with (C13); ``Settings.version`` is what the API reports through
``/healthz``, the OpenAPI document, and the ``app_version`` stamped into generated reports.

They cannot simply be collapsed: the api image ships ``apps/api/src`` and ``migrations`` but not
the repo-root ``VERSION``, so reading the file at runtime would fail inside the container. Pinning
them together is what keeps a released bundle's image tag and the version the running API reports
the same string.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from easysynq_api.config import Settings


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "VERSION").exists():
            return parent
    raise AssertionError("VERSION not found above the test directory")


def test_settings_version_matches_the_version_file() -> None:
    file_version = (_repo_root() / "VERSION").read_text().strip()
    assert Settings().version == file_version, (
        f"VERSION says {file_version!r} but Settings.version is {Settings().version!r} — the "
        f"air-gap bundle would tag its images with one string while /healthz reports the other"
    )


def test_the_contract_document_declares_the_same_version_the_api_serves() -> None:
    """``main.py`` builds ``FastAPI(version=settings.version)``.

    So the SERVED OpenAPI takes its version from Settings while the checked-in
    ``packages/contracts/openapi.yaml`` carries its own literal. Nothing compares them, so they
    drift silently and the published contract ends up declaring a version the API never serves.
    """
    spec = (_repo_root() / "packages/contracts/openapi.yaml").read_text()
    declared = re.search(r"^  version:\s*(\S+)\s*$", spec, re.M)
    assert declared is not None, "openapi.yaml has no info.version"
    assert declared.group(1) == Settings().version


@pytest.mark.parametrize("manifest", ["apps/web/package.json", "packages/contracts/package.json"])
def test_workspace_manifests_carry_the_same_release_string(manifest: str) -> None:
    """The two npm workspaces version independently of the Python side."""
    data = json.loads((_repo_root() / manifest).read_text())
    assert data["version"] == Settings().version, (
        f"{manifest} says {data['version']!r} but the release string is {Settings().version!r}"
    )


@pytest.mark.parametrize("removed", ["easysynq_env", "easysynq_profile", "s3_object_lock_mode"])
def test_settings_no_longer_advertises_knobs_nothing_reads(removed: str) -> None:
    """These fields were read by nothing.

    ``S3_OBJECT_LOCK_MODE`` was the sharp one: ``.env.example`` advertised it as a "hardened
    opt-in", but the object-lock mode an install actually uses is the per-org ``storage_config``
    row written by the setup wizard. An operator who set the variable got no hardening and no
    warning.
    """
    assert removed not in Settings.model_fields, (
        f"{removed} was removed as unread. If a later slice genuinely needs it, drop it from "
        f"this "
        f"parametrization and say in the commit what reads it — the pin exists to stop it coming "
        f"back as decoration, not to forbid the capability."
    )


def test_env_example_does_not_advertise_the_dead_object_lock_knob() -> None:
    env_example = (_repo_root() / ".env.example").read_text()
    assert "S3_OBJECT_LOCK_MODE=" not in env_example
    assert "EASYSYNQ_ENV=" not in env_example
    # EASYSYNQ_PROFILE stays: install.sh, doctor.sh and the Keycloak helper scripts read it.
    assert "EASYSYNQ_PROFILE=" in env_example
