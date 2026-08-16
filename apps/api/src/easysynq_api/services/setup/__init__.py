"""First-run setup wizard (slice S8a, doc 08).

The setup spine: secret-authorized, active-credential-bound first-administrator provisioning, the
org profile, and the finalize transition that flips the ``setup_state`` one-way latch to
``OPERATIONAL``. The HTTP latch lives in ``main.py``; setup gates register in
:data:`~easysynq_api.services.setup.service.GATES`.
"""

from __future__ import annotations

from .administrator import (
    FirstAdministratorProfile,
    FirstAdministratorProvisioned,
    acknowledge_first_administrator,
    provision_first_administrator,
)
from .bootstrap import mint_secret, verify_secret
from .service import (
    GATES,
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
    "FirstAdministratorProfile",
    "FirstAdministratorProvisioned",
    "acknowledge_first_administrator",
    "configure_auth",
    "configure_backup",
    "finalize_setup",
    "get_setup_detail",
    "get_setup_state",
    "mint_secret",
    "provision_first_administrator",
    "set_org_profile",
    "trigger_restore_test",
    "verify_secret",
    "verify_storage",
]
