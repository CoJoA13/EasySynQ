"""Append one EasySynQ SPA redirect URI without overwriting operator-managed callbacks.

Runs inside the API container, where Keycloak's internal Admin REST URL and credentials come from
the normal environment:

    python -m easysynq_api.cli.keycloak_redirect --redirect-uri https://qms.example.com/*
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ..config import get_settings
from ..services.keycloak_admin import KeycloakAdminError, ensure_client_redirect_uri


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keycloak-redirect",
        description="Append an easysynq-web redirect URI while preserving existing callbacks.",
    )
    parser.add_argument("--redirect-uri", required=True)
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        changed = ensure_client_redirect_uri(
            base_url=settings.keycloak_admin_url,
            realm="easysynq",
            client_id="easysynq-web",
            redirect_uri=args.redirect_uri,
            admin_user=settings.keycloak_admin_user,
            admin_password=settings.keycloak_admin_password,
        )
    except KeycloakAdminError as exc:
        parser.exit(1, f"keycloak-redirect: {exc}\n")
    outcome = "added" if changed else "already present"
    print(f"keycloak-redirect: {args.redirect_uri} {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
