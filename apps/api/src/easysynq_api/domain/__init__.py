"""Pure domain logic — no DB, no I/O, no framework. Unit-testable in isolation.

The authorization PDP (``domain.authz``) lives here because it is the load-bearing,
deterministic core that slice S2's acceptance proofs exercise directly (doc 18 §5.2,
register R3). The PEP (``services.authz``) gathers grants from the DB and hands them
to this layer; nothing here imports SQLAlchemy or FastAPI.
"""
