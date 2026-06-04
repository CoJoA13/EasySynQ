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
# S8b2: the restore-test drill copies blobs INTO this plain (NON-WORM) scratch bucket and tears the
# per-drill prefix down — object-lock can't be retro-added (R37), so the drill never restores into a
# locked bucket. Deliberately NOT --with-lock.
mc mb --ignore-existing local/restore-scratch
# S-ing-1: the ingestion scan content-addresses imported source bytes INTO this plain (NON-WORM)
# staging bucket; only the future commit slice promotes accepted bytes into the WORM vault. Kept
# SEPARATE from the vault check-in `staging` bucket so the import TTL-janitor never collides with a
# vault-bound staged object. Deliberately NOT --with-lock (abandoned imports leave no immutable residue).
mc mb --ignore-existing local/import-staging

# GOVERNANCE default retention keeps R37 fresh-bucket restore + the R27 destroy
# escape hatch buildable. Dev uses a short window so engineers can reset.
RETENTION="${WORM_RETENTION:-30d}"
mc retention set --default GOVERNANCE "$RETENTION" local/documents
mc retention set --default GOVERNANCE "$RETENTION" local/records

# S6 off-host audit-checkpoint anchor (R13/D-8): a SEPARATE object-lock bucket reached with a
# DISTINCT, write-only credential held apart from the vault root, so the same operator cannot
# silently control both the live chain and its off-host anchor. The bucket is on the same host in
# dev — the tamper_evidence_attested soft-gate stays FALSE until an operator points it off-host.
mc mb --with-lock --ignore-existing local/audit-checkpoints
mc retention set --default GOVERNANCE "$RETENTION" local/audit-checkpoints

AUDIT_SINK_KEY="${AUDIT_SINK_ACCESS_KEY:-audit-sink}"
AUDIT_SINK_SECRET="${AUDIT_SINK_SECRET_KEY:-audit-sink-secret-change-me}"
mc admin user add local "$AUDIT_SINK_KEY" "$AUDIT_SINK_SECRET" || true
cat > /tmp/audit-sink-writeonly.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::audit-checkpoints", "arn:aws:s3:::audit-checkpoints/*"]
    }
  ]
}
EOF
mc admin policy create local audit-sink-writeonly /tmp/audit-sink-writeonly.json || true
mc admin policy attach local audit-sink-writeonly --user "$AUDIT_SINK_KEY" || true

echo "minio-init: buckets ready (documents, records [WORM/${RETENTION}], renditions, staging, restore-scratch, import-staging, audit-checkpoints)"
