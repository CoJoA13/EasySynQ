"""First-run setup wizard (slice S8a, doc 08).

The setup *spine*: the bootstrap-of-trust (an operator-minted single-use secret → the first
``System Administrator``), the org profile, and the finalize transition that flips the
``setup_state`` one-way latch to ``OPERATIONAL``. The HTTP latch lives in ``main.py`` (a 423
middleware); the deferred gates (G-B WORM-verify, G-C/AC#5 restore-drill, G-D auth) register into
the same :data:`~easysynq_api.services.setup.service.GATES` list in S8b/S8c.
"""

from __future__ import annotations

from .bootstrap import mint_secret, verify_secret
from .service import (
    GATES,
    bootstrap_admin,
    configure_auth,
    configure_backup,
    finalize_setup,
    get_setup_detail,
    get_setup_state,
    set_org_profile,
    trigger_restore_test,
    verify_storage,
)

__all__ = [
    "GATES",
    "bootstrap_admin",
    "configure_auth",
    "configure_backup",
    "finalize_setup",
    "get_setup_detail",
    "get_setup_state",
    "mint_secret",
    "set_org_profile",
    "trigger_restore_test",
    "verify_secret",
    "verify_storage",
]
