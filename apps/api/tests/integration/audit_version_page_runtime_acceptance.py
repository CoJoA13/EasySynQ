"""Owned actual-image acceptance for strict supplied version-page decoding.

This module deliberately does not start with ``test_``. The repository-owned runner invokes it
with the immutable application image built from the checked source manifest.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from testcontainers.core.container import DockerContainer, ExecConfig
from testcontainers.core.labels import LABEL_SESSION_ID, SESSION_ID

from .audit_external_runtime_acceptance import _api_image as _api_image
from .audit_external_runtime_acceptance import _ApiImage

pytestmark = pytest.mark.integration

_IMAGE_PYTHON = "/app/.venv/bin/python"
_PROBE_IN_CONTAINER = "/run/audit-version-page-runtime-probe.py"
_RUN_LABEL = "com.easynq.audit-external.run"
_PROOF_PREFIX = "AUDIT_VERSION_PAGE_PROOF "
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CASE_NAMES = (
    "mixed-records-and-opaque-labels",
    "thousand-observation-boundary",
    "combined-page-limit",
    "truncation-numeric-one",
    "truncation-numeric-zero",
    "truncation-uppercase-true",
    "truncation-garbage",
    "truncation-empty",
    "missing-truncation",
    "duplicate-truncation-false-true",
    "duplicate-truncation-true-false",
    "duplicate-root-prefix",
    "malformed-percent-short",
    "malformed-percent-nonhex",
    "invalid-utf8-percent",
    "single-percent-decode",
    "literal-plus-preserved",
    "malicious-doctype",
    "malicious-comment",
    "malformed-xml",
    "scope-binding",
    "key-only-cursor-replay",
    "opaque-two-part-cursor-replay",
    "request-marker-echo",
    "immediate-cursor-repeat",
    "late-entry-failure",
    "exact-body-boundary",
    "body-boundary-one-over",
    "bomless-utf16-little-endian",
    "bomless-utf16-big-endian",
    "raw-nul-xml",
    "percent-decoded-nul-key",
    "valid-utf8-replacement-character",
)
_OUTCOMES = {
    "mixed-records-and-opaque-labels": "accepted",
    "thousand-observation-boundary": "accepted",
    "combined-page-limit": "PAGE_LIMIT",
    "truncation-numeric-one": "RESPONSE_INVALID",
    "truncation-numeric-zero": "RESPONSE_INVALID",
    "truncation-uppercase-true": "RESPONSE_INVALID",
    "truncation-garbage": "RESPONSE_INVALID",
    "truncation-empty": "RESPONSE_INVALID",
    "missing-truncation": "RESPONSE_INVALID",
    "duplicate-truncation-false-true": "RESPONSE_INVALID",
    "duplicate-truncation-true-false": "RESPONSE_INVALID",
    "duplicate-root-prefix": "RESPONSE_INVALID",
    "malformed-percent-short": "RESPONSE_INVALID",
    "malformed-percent-nonhex": "RESPONSE_INVALID",
    "invalid-utf8-percent": "RESPONSE_INVALID",
    "single-percent-decode": "accepted",
    "literal-plus-preserved": "accepted",
    "malicious-doctype": "RESPONSE_INVALID",
    "malicious-comment": "RESPONSE_INVALID",
    "malformed-xml": "RESPONSE_INVALID",
    "scope-binding": "SCOPE_MISMATCH",
    "key-only-cursor-replay": "accepted",
    "opaque-two-part-cursor-replay": "accepted",
    "request-marker-echo": "CURSOR_INVALID",
    "immediate-cursor-repeat": "CURSOR_INVALID",
    "late-entry-failure": "RESPONSE_INVALID",
    "exact-body-boundary": "accepted",
    "body-boundary-one-over": "BODY_LIMIT",
    "bomless-utf16-little-endian": "RESPONSE_INVALID",
    "bomless-utf16-big-endian": "RESPONSE_INVALID",
    "raw-nul-xml": "RESPONSE_INVALID",
    "percent-decoded-nul-key": "accepted",
    "valid-utf8-replacement-character": "accepted",
}


def _probe_source() -> Path:
    path = Path(__file__).with_name("audit_version_page_runtime_probe.py")
    assert path.is_file() and not path.is_symlink()
    return path


def _environment(entries: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries or []:
        name, separator, value = entry.partition("=")
        assert separator and name and name not in result
        result[name] = value
    return result


@contextmanager
def _probe_container(image: _ApiImage) -> Iterator[DockerContainer]:
    run_id = os.environ["EASYSYNQ_ACCEPTANCE_RUN_ID"]
    container = DockerContainer(
        image.image_id,
        command=["sleep", "300"],
        volumes=[(str(_probe_source()), _PROBE_IN_CONTAINER, "ro")],
        user="10001:10001",
        read_only=True,
        network_mode="none",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges:true"],
        labels={_RUN_LABEL: run_id},
    )
    with container:
        wrapped = container.get_wrapped_container()
        wrapped.reload()
        attrs = wrapped.attrs
        assert attrs["Image"] == image.image_id
        assert attrs["Config"]["User"] == "10001:10001"
        assert attrs["HostConfig"]["ReadonlyRootfs"] is True
        assert attrs["HostConfig"]["NetworkMode"] == "none"
        assert attrs["HostConfig"]["CapDrop"] == ["ALL"]
        assert "no-new-privileges:true" in attrs["HostConfig"]["SecurityOpt"]
        assert attrs["Config"]["Labels"][LABEL_SESSION_ID] == SESSION_ID
        assert attrs["Config"]["Labels"][_RUN_LABEL] == run_id
        assert _environment(attrs["Config"].get("Env")) == image.environment
        assert len(attrs["Mounts"]) == 1 and attrs["Mounts"][0]["RW"] is False
        yield container


def _exec_probe(container: DockerContainer, image_id: str) -> dict[str, Any]:
    command = [
        _IMAGE_PYTHON,
        _PROBE_IN_CONTAINER,
        "--application-image-id",
        image_id,
    ]
    result = container.exec(ExecConfig(command=command, environment={}))
    assert result.exit_code == 0
    decoded = result.output.decode("utf-8")
    value = json.loads(decoded)
    assert type(value) is dict
    return value


def test_version_page_decoder_runtime_rejects_lossy_provider_pages(
    _api_image: _ApiImage,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _probe_container(_api_image) as container:
        receipt = _exec_probe(container, _api_image.image_id)

    assert set(receipt) == {
        "case_count",
        "case_names",
        "application_image_id",
        "uid",
        "sdk_not_used",
        "input_vectors_sha256",
        "outcomes",
        "runtime_versions",
        "dev_packages_absent",
        "cleanup_complete",
    }
    assert receipt["case_count"] == len(_CASE_NAMES)
    assert receipt["case_names"] == list(_CASE_NAMES)
    assert receipt["application_image_id"] == _api_image.image_id
    assert receipt["uid"] == 10001
    assert receipt["sdk_not_used"] is True
    digest = receipt["input_vectors_sha256"]
    assert isinstance(digest, str) and _SHA256.fullmatch(digest) is not None
    assert receipt["outcomes"] == _OUTCOMES
    versions = receipt["runtime_versions"]
    assert isinstance(versions, dict) and set(versions) == {"python", "expat"}
    assert all(isinstance(value, str) and value for value in versions.values())
    assert receipt["dev_packages_absent"] == ["mypy", "pytest", "ruff"]
    assert receipt["cleanup_complete"] is True

    with capsys.disabled():
        print(_PROOF_PREFIX + json.dumps(receipt, sort_keys=True, separators=(",", ":")))
