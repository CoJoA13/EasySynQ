"""The ingestion use-case layer (slices S-ing-1/2/3, doc 09): run lifecycle + the scan / extract /
classify / dedup / propose worker bodies.

The ingestion engine introduces ONLY the transient ``import_*`` staging layer — it writes nothing to
the vault (commit is S-ing-5). The public surface the API + Celery tasks bind to is re-exported.
"""

from .classify import run_classify
from .dedup import run_dedup
from .extract import run_extract
from .propose import run_propose
from .service import (
    cancel_import_run,
    create_import_run,
    get_import_run,
    list_import_file_detail,
    list_import_files,
    list_import_runs,
    reap_stalled_runs,
    run_scan,
)

__all__ = [
    "cancel_import_run",
    "create_import_run",
    "get_import_run",
    "list_import_file_detail",
    "list_import_files",
    "list_import_runs",
    "reap_stalled_runs",
    "run_classify",
    "run_dedup",
    "run_extract",
    "run_propose",
    "run_scan",
]
