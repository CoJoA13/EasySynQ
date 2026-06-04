"""The ingestion use-case layer (slice S-ing-1, doc 09): run lifecycle + the scan/inventory worker.

S-ing-1 introduces ONLY the transient ``import_*`` staging layer — it writes nothing to the vault.
The public surface the API + Celery task bind to is re-exported here."""

from .service import (
    cancel_import_run,
    create_import_run,
    get_import_run,
    list_import_files,
    list_import_runs,
    reap_stalled_scans,
    run_scan,
)

__all__ = [
    "cancel_import_run",
    "create_import_run",
    "get_import_run",
    "list_import_files",
    "list_import_runs",
    "reap_stalled_scans",
    "run_scan",
]
