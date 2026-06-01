"""EasySynQ API — the controlled-vault QMS backend.

Slice S0 establishes the app factory, config, structured logging, the RFC 9457
problem model, the health surface, and the async DB session. Subsequent slices
add auth (S1), the PDP/PEP authorization engine (S2), the vault and lifecycle
(S3/S4), signatures (S5), the audit trail (S6), and the read-only mirror (S7).
"""

__version__ = "0.1.0"
