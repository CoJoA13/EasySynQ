"""Operator CLI for the audit trail (slice S6) — runs inside the api image (DB reachable there).

    python -m easysynq_api.cli.audit ensure-partitions   # Beat-down fallback (doc 18 §4 line 213)
    python -m easysynq_api.cli.audit verify-chain         # on-demand tamper check (AC#6b)

``ensure-partitions`` is the manual fallback for the daily ``roll_partitions`` Beat job if the
scheduler was down near a month boundary. ``verify-chain`` re-walks the linked chain and prints the
first broken link (exit code 1 if the chain is broken), so it can gate a backup/restore drill.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..config import get_settings
from ..db.models.organization import Organization
from ..services.audit.checkpoint import load_verify_key, verify_offhost_checkpoint
from ..services.audit.external import (
    ExternalVerificationReport,
    OrganizationVerificationResult,
    VerificationReason,
    WitnessVerificationResult,
    _verify_external,
)
from ..services.audit.partitions import ensure_partitions
from ..services.audit.trust import (
    ExternalCredentials,
    TrustConfigurationError,
    TrustDescriptor,
    load_external_credentials,
    load_trust_descriptor,
)
from ..services.audit.verify import verify_chain

_EXTERNAL_BOOTSTRAP = (
    "import runpy, sys\n"
    "sys.path.insert(0, sys.argv.pop(1))\n"
    'runpy.run_module("easysynq_api.cli._audit_external", run_name="__main__")'
)
_EXTERNAL_FIXED_ENVIRONMENT = {"HOME": "/dev/null", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
_EXTERNAL_CREDENTIAL_KEYS = frozenset(
    {
        "DATABASE_URL",
        "AUDIT_SINK_READ_ACCESS_KEY",
        "AUDIT_SINK_READ_SECRET_KEY",
    }
)
_EXTERNAL_ENVIRONMENT_KEYS = frozenset((*_EXTERNAL_CREDENTIAL_KEYS, *_EXTERNAL_FIXED_ENVIRONMENT))


async def _ensure_partitions() -> list[str]:
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            return await ensure_partitions(session)
    finally:
        await engine.dispose()


async def _verify_chain() -> tuple[bool, int, int, list[tuple[int, str]]]:
    """Verify every org's chain (the chain is per-org). Aggregates the result."""
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    # Attest the signed checkpoint too when the verify (public) key is available to this process;
    # else the chain is walked only (a checkpoint/signature break folds into ``breaks``).
    verify_key = load_verify_key()
    verified, checked, pending = True, 0, 0
    breaks: list[tuple[int, str]] = []
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                result = await verify_chain(session, org_id, verify_key=verify_key)
                verified = verified and result.verified
                checked += result.checked
                pending += result.pending
                breaks.extend((b.at_id, b.reason) for b in result.breaks)
        return verified, checked, pending, breaks
    finally:
        await engine.dispose()


async def _verify_offhost() -> tuple[bool, int, list[tuple[str, list[str]]]]:
    """The INDEPENDENT off-host read-back (doc 12 §4.4) — run this OUT-OF-BAND from a separate host
    with the read creds + the public key to attest that the off-host anchor still matches the live
    chain. Self-contained: it RE-WALKS the chain (recomputing hashes from the audit payloads) AND
    verifies the in-DB + off-host signed checkpoints, so an earlier row edited while its hash
    columns are left intact is still caught — not just an off-host↔latest_id hash compare. Returns
    (ok, sinks_read, [(org_id, reasons)])."""
    verify_key = load_verify_key()
    if verify_key is None:
        return (
            False,
            0,
            [("", ["no verify (public) key available — cannot attest the off-host copy"])],
        )
    engine = create_async_engine(get_settings().database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    ok, read = True, 0
    org_reasons: list[tuple[str, list[str]]] = []
    try:
        async with sessionmaker() as session:
            org_ids = (await session.execute(select(Organization.id))).scalars().all()
            for org_id in org_ids:
                walk = await verify_chain(session, org_id, verify_key=verify_key)
                res = await verify_offhost_checkpoint(session, org_id, verify_key=verify_key)
                read += res.sinks_read
                ok = ok and res.verified and walk.verified
                reasons = list(res.reasons)
                reasons.extend(f"chain break at id={b.at_id}: {b.reason}" for b in walk.breaks)
                if reasons:
                    org_reasons.append((str(org_id), reasons))
        return ok, read, org_reasons
    finally:
        await engine.dispose()


def _external_failure_report(
    descriptor: TrustDescriptor | None, *, code: str, message: str
) -> ExternalVerificationReport:
    incomplete = VerificationReason(
        "CHECK_INCOMPLETE", "required external verification was not attempted"
    )
    organizations: tuple[OrganizationVerificationResult, ...] = ()
    if descriptor is not None:
        organizations = tuple(
            OrganizationVerificationResult(
                org_id=organization.org_id,
                present=None,
                verified=False,
                checked=0,
                pending=0,
                local_checkpoint=None,
                break_count=0,
                breaks=(),
                breaks_omitted=0,
                witnesses=tuple(
                    WitnessVerificationResult(
                        witness_id=witness.witness_id,
                        attempted=False,
                        status="incomplete",
                        verified=False,
                        sinks_read=0,
                        read_failed=False,
                        attest_failures=0,
                        comparison_unavailable=False,
                        reasons=(incomplete,),
                        reasons_omitted=0,
                    )
                    for witness in organization.witnesses
                ),
                reasons=(incomplete,),
                reasons_omitted=0,
            )
            for organization in descriptor.organizations
        )
    return ExternalVerificationReport(
        descriptor_id=None if descriptor is None else descriptor.descriptor_id,
        descriptor_sha256=None if descriptor is None else descriptor.sha256,
        verified=False,
        checked=0,
        pending=0,
        sinks_read=0,
        unenrolled_orgs_present=None,
        organizations=organizations,
        reasons=(VerificationReason(code, message),),
        reasons_omitted=0,
    )


def _emit_external_report(
    report: ExternalVerificationReport, *, configuration: bool = False
) -> int:
    print(json.dumps(report.to_dict(), separators=(",", ":")))
    if configuration:
        return 2
    return 0 if report.verified else 1


def _run_external(descriptor_path: Path, environ: Mapping[str, str]) -> int:
    """Revalidate and run the strict verifier inside its controlled worker process."""
    try:
        descriptor = load_trust_descriptor(descriptor_path)
    except (TrustConfigurationError, ValueError):
        return _emit_external_report(
            _external_failure_report(
                None,
                code="CONFIG_INVALID",
                message="external verifier configuration is invalid",
            ),
            configuration=True,
        )
    try:
        credentials = load_external_credentials(environ)
    except (TrustConfigurationError, ValueError):
        return _emit_external_report(
            _external_failure_report(
                descriptor,
                code="CONFIG_INVALID",
                message="external verifier configuration is invalid",
            ),
            configuration=True,
        )
    try:
        report = asyncio.run(_verify_external(descriptor, credentials))
    except Exception:  # noqa: BLE001 - never expose raw runtime/credential errors
        report = _external_failure_report(
            descriptor,
            code="CHECK_INCOMPLETE",
            message="external verifier did not complete",
        )
    return _emit_external_report(report)


def _external_input_snapshot(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        name: environ.get(name, "")
        for name in (
            "DATABASE_URL",
            "AUDIT_SINK_READ_ACCESS_KEY",
            "AUDIT_SINK_READ_SECRET_KEY",
        )
    }


def _exec_external(
    descriptor_path: Path,
    credentials: ExternalCredentials,
    *,
    original_database_url: str,
) -> None:
    """Replace this process with the strict worker under an exact fresh environment."""
    environment = {
        "DATABASE_URL": original_database_url,
        "AUDIT_SINK_READ_ACCESS_KEY": credentials.access_key,
        "AUDIT_SINK_READ_SECRET_KEY": credentials.secret_key,
        **_EXTERNAL_FIXED_ENVIRONMENT,
    }
    source_root = str(Path(__file__).resolve().parents[2])
    os.execve(  # noqa: S606 - required shell-free exec with fixed interpreter/argv/fresh env
        sys.executable,
        [
            sys.executable,
            "-I",
            "-B",
            "-u",
            "-c",
            _EXTERNAL_BOOTSTRAP,
            source_root,
            str(descriptor_path),
        ],
        environment,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="easysynq-audit", description="Audit-trail operator CLI.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ensure-partitions", help="create the rolling monthly audit_event partitions")
    sub.add_parser("verify-chain", help="re-walk + verify the hash chain (+ signed checkpoint)")
    offhost_parser = sub.add_parser(
        "verify-offhost", help="independent off-host checkpoint read-back (out-of-band)"
    )
    offhost_parser.add_argument("--trust-descriptor")
    args = parser.parse_args(argv)

    if args.command == "ensure-partitions":
        ensured = asyncio.run(_ensure_partitions())
        print(f"ensured partitions: {', '.join(ensured)}")
        return 0

    if args.command == "verify-offhost":
        if args.trust_descriptor is not None:
            descriptor_path = Path(args.trust_descriptor)
            try:
                descriptor = load_trust_descriptor(descriptor_path)
            except (TrustConfigurationError, ValueError):
                return _emit_external_report(
                    _external_failure_report(
                        None,
                        code="CONFIG_INVALID",
                        message="external verifier configuration is invalid",
                    ),
                    configuration=True,
                )
            try:
                external_inputs = _external_input_snapshot(os.environ)
                credentials = load_external_credentials(external_inputs)
            except (TrustConfigurationError, ValueError):
                return _emit_external_report(
                    _external_failure_report(
                        descriptor,
                        code="CONFIG_INVALID",
                        message="external verifier configuration is invalid",
                    ),
                    configuration=True,
                )
            try:
                _exec_external(
                    descriptor_path,
                    credentials,
                    original_database_url=external_inputs["DATABASE_URL"],
                )
            except Exception:  # noqa: BLE001 - redact exec/runtime detail in parent failure
                return _emit_external_report(
                    _external_failure_report(
                        descriptor,
                        code="CHECK_INCOMPLETE",
                        message="external verifier did not complete",
                    )
                )
            raise AssertionError("os.execve unexpectedly returned")
        ok, read, org_reasons = asyncio.run(_verify_offhost())
        print(f"offhost_verified={ok} sinks_read={read}")
        for org_id, reasons in org_reasons:
            for reason in reasons:
                print(f"  MISMATCH org={org_id}: {reason}")
        return 0 if ok else 1

    verified, checked, pending, breaks = asyncio.run(_verify_chain())
    print(f"verified={verified} checked={checked} pending={pending}")
    for at_id, reason in breaks:
        print(f"  BREAK at id={at_id}: {reason}")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
