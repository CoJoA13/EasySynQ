"""Translate a SQLAlchemy URL into the libpq connection params ``pg_dump``/``pg_restore``/psycopg
need (slice S8b2).

The drill runs the PG client binaries + a psycopg connection as the **owner** role
(``settings.sync_dsn`` = ``postgresql+psycopg://easysynq:…@postgres:5432/easysynq``). We pass the
connection via **environment variables** (PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE) rather than a
URL on the command line, so the password never lands in ``argv`` (visible in ``ps``).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit


def _parts(sa_url: str) -> dict[str, str]:
    u = urlsplit(sa_url)
    return {
        "host": u.hostname or "",
        "port": str(u.port) if u.port else "",
        "user": unquote(u.username) if u.username else "",
        "password": unquote(u.password) if u.password else "",
        "dbname": u.path.lstrip("/"),
    }


def database_name(sa_url: str) -> str:
    """The database name in ``sa_url``."""
    return _parts(sa_url)["dbname"]


def libpq_env(sa_url: str, *, dbname: str | None = None) -> dict[str, str]:
    """PG* env vars for a ``pg_dump``/``pg_restore`` subprocess (override the DB with ``dbname``).
    Omits empty values so a unix-socket / peer-auth deploy still works."""
    p = _parts(sa_url)
    env = {
        "PGHOST": p["host"],
        "PGPORT": p["port"],
        "PGUSER": p["user"],
        "PGPASSWORD": p["password"],
        "PGDATABASE": dbname if dbname is not None else p["dbname"],
        "PGCONNECT_TIMEOUT": "10",
    }
    return {k: v for k, v in env.items() if v}


def conn_kwargs(sa_url: str, *, dbname: str | None = None) -> dict[str, Any]:
    """Libpq kwargs for ``psycopg.connect(**kwargs)`` (override the DB with ``dbname``). Typed
    ``Any`` so the values flow into ``connect``'s ``**kwargs`` (libpq takes the string port)."""
    p = _parts(sa_url)
    kwargs = {
        "host": p["host"],
        "port": p["port"],
        "user": p["user"],
        "password": p["password"],
        "dbname": dbname if dbname is not None else p["dbname"],
        "connect_timeout": "10",
    }
    return {k: v for k, v in kwargs.items() if v}
