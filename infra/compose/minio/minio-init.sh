#!/bin/sh
# Create the vault buckets with object-lock (WORM) and a GOVERNANCE retention
# default (D-7). The `local` alias is provided via MC_HOST_local in compose.
set -eu

echo "minio-init: waiting for MinIO..."
until mc ls local >/dev/null 2>&1; do
	sleep 2
done

# Object-lock MUST be enabled at bucket creation (--with-lock); it cannot be added later.
mc mb --with-lock --ignore-existing local/documents
mc mb --with-lock --ignore-existing local/records
mc mb --ignore-existing local/renditions   # derived, rebuildable — not WORM
mc mb --ignore-existing local/staging       # transient import staging (v1)

# GOVERNANCE default retention keeps R37 fresh-bucket restore + the R27 destroy
# escape hatch buildable. Dev uses a short window so engineers can reset.
RETENTION="${WORM_RETENTION:-30d}"
mc retention set --default GOVERNANCE "$RETENTION" local/documents
mc retention set --default GOVERNANCE "$RETENTION" local/records

echo "minio-init: buckets ready (documents, records [WORM/${RETENTION}], renditions, staging)"
