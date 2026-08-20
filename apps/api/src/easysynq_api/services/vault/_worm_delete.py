"""Internal exact-version WORM deletion primitive.

This module is deliberately unexported and unwired.  Ordinary vault code must not acquire
retention-bypass or legal-hold-release authority; the reviewed lifecycle workflow will be its only
future caller.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .storage import _client
from .worm import (
    WormObjectLocator,
    WormReadbackMismatch,
    WormStorageError,
    _legal_hold_content_md5,
    _provider_error_code,
    _raise_provider_failure,
)


def _exact_parameters(locator: WormObjectLocator) -> dict[str, str]:
    return {
        "Bucket": locator.bucket,
        "Key": locator.object_key,
        "VersionId": locator.object_version_id,
    }


def _confirm_exact_version_absent(client: Any, exact: dict[str, str]) -> None:
    try:
        probe = client.get_object(**exact)
    except Exception as exc:  # noqa: BLE001 - boto3 exposes multiple provider failure types
        if _provider_error_code(exc) == "NoSuchVersion":
            return
        _raise_provider_failure(exc)

    if not isinstance(probe, dict):
        raise WormReadbackMismatch
    body = probe.get("Body")
    if body is None:
        raise WormReadbackMismatch
    try:
        body.close()
    except Exception as exc:  # noqa: BLE001 - streaming bodies expose no common error base
        _raise_provider_failure(exc)
    if probe.get("VersionId") != exact["VersionId"]:
        raise WormReadbackMismatch
    raise WormStorageError("the exact WORM version remained after deletion")


def _delete_worm_version_sync(
    locator: WormObjectLocator,
    *,
    release_hold: bool,
    bypass_governance: bool,
) -> None:
    if not isinstance(release_hold, bool) or not isinstance(bypass_governance, bool):
        raise ValueError("WORM deletion controls must be booleans")

    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001 - boto3 client bootstrap has multiple failure types
        _raise_provider_failure(exc)
    exact = _exact_parameters(locator)

    if release_hold:
        try:
            client.put_object_legal_hold(
                **exact,
                LegalHold={"Status": "OFF"},
                ContentMD5=_legal_hold_content_md5("OFF"),
            )
        except Exception as exc:  # noqa: BLE001 - boto3 exposes multiple provider failure types
            if _provider_error_code(exc) == "NoSuchVersion":
                _confirm_exact_version_absent(client, exact)
                return
            _raise_provider_failure(exc)
        try:
            hold_response = client.get_object_legal_hold(**exact)
        except Exception as exc:  # noqa: BLE001 - boto3 exposes multiple provider failure types
            if _provider_error_code(exc) == "NoSuchVersion":
                _confirm_exact_version_absent(client, exact)
                return
            _raise_provider_failure(exc)
        if not isinstance(hold_response, dict):
            raise WormReadbackMismatch
        hold = hold_response.get("LegalHold")
        if not isinstance(hold, dict) or hold.get("Status") != "OFF":
            raise WormReadbackMismatch

    delete_parameters: dict[str, Any] = dict(exact)
    if bypass_governance:
        delete_parameters["BypassGovernanceRetention"] = True
    try:
        client.delete_object(**delete_parameters)
    except Exception as exc:  # noqa: BLE001 - boto3 exposes multiple provider failure types
        if _provider_error_code(exc) != "NoSuchVersion":
            _raise_provider_failure(exc)

    _confirm_exact_version_absent(client, exact)


async def delete_worm_version(
    locator: WormObjectLocator,
    *,
    release_hold: bool,
    bypass_governance: bool,
) -> None:
    """Delete one exact version after optional hold release and exact absence confirmation."""
    await asyncio.to_thread(
        _delete_worm_version_sync,
        locator,
        release_hold=release_hold,
        bypass_governance=bypass_governance,
    )
