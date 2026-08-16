"""Operator bootstrap: mint a one-time setup secret for the first-run wizard (slice S8a, doc 08 §4).

The first install step. Generates a high-entropy single-use secret, stores its **salted hash** + TTL
on ``system_config`` (the plaintext is shown ONCE here, never persisted), and prints it. The
operator opens ``/setup`` in the browser and uses it to create the first System Administrator.
``grant-role`` remains the break-glass path.

Run it inside the api container (where the DB is reachable):

    easysynq setup mint-bootstrap [--ttl-hours 24]

Uses a sync engine on the owner DSN — a one-shot script, not coupled to the app's event loop.
While setup remains ``UNINITIALIZED``, re-running replaces only the secret proof and expiry so an
expired pending administrator claim can be recovered. Advanced setup refuses reminting.
"""

from __future__ import annotations

import argparse
import datetime
from collections.abc import Sequence

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models.organization import Organization
from ..db.models.system_config import SetupState, SystemConfig
from ..services.setup.bootstrap import mint_secret


def mint_bootstrap(org_short_code: str = "DEFAULT", ttl_hours: int = 24) -> str:
    """Mint + persist a fresh bootstrap secret; return the plaintext secret (shown once)."""
    engine = create_engine(get_settings().sync_dsn)
    try:
        with Session(engine) as session:
            org = session.scalar(
                select(Organization).where(Organization.short_code == org_short_code)
            )
            if org is None:
                raise SystemExit(f"no organization with short_code={org_short_code!r}")
            cfg = session.get(SystemConfig, org.id)
            if cfg is None:
                raise SystemExit("system_config is not initialized")
            if cfg.setup_state is not SetupState.UNINITIALIZED:
                raise SystemExit("bootstrap can only be minted while setup is UNINITIALIZED")
            secret, stored_hash = mint_secret()
            cfg.bootstrap_secret_hash = stored_hash
            cfg.bootstrap_expires_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
                hours=ttl_hours
            )
            session.commit()
            return secret
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup", description="Mint the one-time first-run bootstrap secret (slice S8a)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    mint = sub.add_parser("mint-bootstrap", help="mint + print a one-time bootstrap secret")
    mint.add_argument("--org", default="DEFAULT", help="organization short_code (default DEFAULT)")
    mint.add_argument(
        "--ttl-hours", type=int, default=24, help="secret validity in hours (default 24)"
    )
    args = parser.parse_args(argv)

    if args.command == "mint-bootstrap":
        secret = mint_bootstrap(args.org, args.ttl_hours)
        print("Bootstrap secret minted (valid for", args.ttl_hours, "hours, single-use).")
        print("Open /setup and create the first administrator with this secret:")
        print()
        print(f"    {secret}")
        print()
        print("This is shown ONCE and is not stored in plaintext. Re-run to mint a new one.")
        return 0
    return 2  # pragma: no cover - argparse 'required=True' makes this unreachable


if __name__ == "__main__":
    raise SystemExit(main())
