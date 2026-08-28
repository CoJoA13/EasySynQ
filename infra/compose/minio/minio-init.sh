#!/bin/sh
# Create the vault buckets with object-lock (WORM) and a GOVERNANCE retention
# default (D-7). The `local` alias is provided via MC_HOST_local in compose.
set -eu

# Bounded wait. api, worker and beat all gate on this one-shot completing, so an unbounded loop
# turns a wrong S3 credential into a whole-stack hang whose only symptom is this message repeating
# forever. Fail loudly instead, and surface mc's own error so the cause is in the logs (audit U43).
echo "minio-init: waiting for MinIO..."
MINIO_WAIT_SECONDS="${MINIO_WAIT_SECONDS:-120}"
deadline=$(( $(date +%s) + MINIO_WAIT_SECONDS ))
until mc ls local >/dev/null 2>&1; do
	if [ "$(date +%s)" -ge "$deadline" ]; then
		echo "minio-init: MinIO not reachable after ${MINIO_WAIT_SECONDS}s. Last error:" >&2
		mc ls local >&2 2>&1 || true
		echo "minio-init: check S3_ACCESS_KEY/S3_SECRET_KEY match the minio service's root creds." >&2
		exit 1
	fi
	sleep 2
done

# Object-lock MUST be enabled at bucket creation (--with-lock); it cannot be added later.
mc mb --with-lock --ignore-existing local/documents
mc mb --with-lock --ignore-existing local/records
mc mb --ignore-existing local/renditions   # derived, rebuildable — not WORM
mc mb --ignore-existing local/staging       # transient import staging (v1)
mc version enable local/staging

# Community MinIO supports one cluster-wide browser origin, not per-bucket CORS. This controls only
# browser response access; S3 IAM and presigned-request authorization remain the access boundary.
: "${PUBLIC_BASE_URL:?PUBLIC_BASE_URL is required}"

reject_public_base_url() {
	echo "minio-init: PUBLIC_BASE_URL must be one exact HTTP(S) origin" >&2
	exit 1
}

case "$PUBLIC_BASE_URL" in
	http://?*|https://?*) ;;
	*) reject_public_base_url ;;
esac
ORIGIN_AUTHORITY="${PUBLIC_BASE_URL#*://}"
case "$ORIGIN_AUTHORITY" in
	""|*'/'*|*'?'*|*'#'*|*'@'*) reject_public_base_url ;;
esac
case "$PUBLIC_BASE_URL" in
	*[[:space:]]*|*','*|*'*'*|*'<'*|*'>'*|*'&'*|*'"'*|*"'"*) reject_public_base_url ;;
esac

validate_origin_port() {
	VALIDATE_PORT="$1"
	case "$VALIDATE_PORT" in
		""|*[!0-9]*) reject_public_base_url ;;
	esac
	[ "${#VALIDATE_PORT}" -le 5 ] || reject_public_base_url
	if [ "$VALIDATE_PORT" -lt 1 ] || [ "$VALIDATE_PORT" -gt 65535 ]; then
		reject_public_base_url
	fi
}

validate_ipv4_tail() {
	IPV4_VALUE="$1"
	case "$IPV4_VALUE" in
		""|.*|*.|*..*|*[!0-9.]*) return 1 ;;
	esac
	IPV4_OLD_IFS="$IFS"
	IFS=.
	set -- $IPV4_VALUE
	IFS="$IPV4_OLD_IFS"
	[ "$#" -eq 4 ] || return 1
	for IPV4_OCTET do
		case "$IPV4_OCTET" in
			0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]) ;;
			*) return 1 ;;
		esac
		[ "$IPV4_OCTET" -le 255 ] || return 1
	done
}

count_ipv6_sequence() {
	IPV6_SEQUENCE="$1"
	IPV6_ALLOW_IPV4_TAIL="$2"
	IPV6_SEQUENCE_COUNT=0
	[ -n "$IPV6_SEQUENCE" ] || return 0
	case "$IPV6_SEQUENCE" in
		:*|*:|*::*) return 1 ;;
	esac
	IPV6_OLD_IFS="$IFS"
	IFS=:
	set -- $IPV6_SEQUENCE
	IFS="$IPV6_OLD_IFS"
	IPV6_GROUP_INDEX=0
	IPV6_GROUP_TOTAL="$#"
	for IPV6_GROUP do
		IPV6_GROUP_INDEX=$((IPV6_GROUP_INDEX + 1))
		case "$IPV6_GROUP" in
			*.*)
				[ "$IPV6_ALLOW_IPV4_TAIL" -eq 1 ] || return 1
				[ "$IPV6_GROUP_INDEX" -eq "$IPV6_GROUP_TOTAL" ] || return 1
				validate_ipv4_tail "$IPV6_GROUP" || return 1
				IPV6_SEQUENCE_COUNT=$((IPV6_SEQUENCE_COUNT + 2))
				;;
			*)
				case "$IPV6_GROUP" in
					""|*[!0-9A-Fa-f]*|?????*) return 1 ;;
				esac
				IPV6_SEQUENCE_COUNT=$((IPV6_SEQUENCE_COUNT + 1))
				;;
		esac
	done
}

validate_ipv6_literal() {
	IPV6_VALUE="$1"
	case "$IPV6_VALUE" in
		""|*[!0-9A-Fa-f:.]*) return 1 ;;
	esac
	case "$IPV6_VALUE" in
		*::*)
			IPV6_PREFIX="${IPV6_VALUE%%::*}"
			IPV6_SUFFIX="${IPV6_VALUE#*::}"
			case "$IPV6_SUFFIX" in
				*::*) return 1 ;;
			esac
			count_ipv6_sequence "$IPV6_PREFIX" 0 || return 1
			IPV6_PREFIX_COUNT="$IPV6_SEQUENCE_COUNT"
			count_ipv6_sequence "$IPV6_SUFFIX" 1 || return 1
			IPV6_TOTAL_COUNT=$((IPV6_PREFIX_COUNT + IPV6_SEQUENCE_COUNT))
			[ "$IPV6_TOTAL_COUNT" -lt 8 ] || return 1
			;;
		*)
			count_ipv6_sequence "$IPV6_VALUE" 1 || return 1
			[ "$IPV6_SEQUENCE_COUNT" -eq 8 ] || return 1
			;;
	esac
}

case "$ORIGIN_AUTHORITY" in
	\[*\])
		IPV6_LITERAL="${ORIGIN_AUTHORITY#\[}"
		IPV6_LITERAL="${IPV6_LITERAL%\]}"
		validate_ipv6_literal "$IPV6_LITERAL" || reject_public_base_url
		;;
	\[*\]:*)
		IPV6_LITERAL="${ORIGIN_AUTHORITY#\[}"
		IPV6_LITERAL="${IPV6_LITERAL%%\]*}"
		IPV6_PORT="${ORIGIN_AUTHORITY#*\]}"
		case "$IPV6_PORT" in
			:*) IPV6_PORT="${IPV6_PORT#:}" ;;
			*) reject_public_base_url ;;
		esac
		validate_ipv6_literal "$IPV6_LITERAL" || reject_public_base_url
		validate_origin_port "$IPV6_PORT"
		;;
	*'['*|*']'*) reject_public_base_url ;;
	*)
		ORIGIN_HOST="$ORIGIN_AUTHORITY"
		ORIGIN_HAS_PORT=0
		ORIGIN_PORT=""
		case "$ORIGIN_AUTHORITY" in
			*:*)
				ORIGIN_HAS_PORT=1
				ORIGIN_HOST="${ORIGIN_AUTHORITY%:*}"
				ORIGIN_PORT="${ORIGIN_AUTHORITY##*:}"
				;;
		esac

		case "$ORIGIN_HOST" in
			""|.*|*.|*[!A-Za-z0-9.-]*) reject_public_base_url ;;
		esac
		[ "${#ORIGIN_HOST}" -le 253 ] || reject_public_base_url

		HOST_REMAINDER="$ORIGIN_HOST"
		while [ -n "$HOST_REMAINDER" ]; do
			case "$HOST_REMAINDER" in
				*.*)
					HOST_LABEL="${HOST_REMAINDER%%.*}"
					HOST_REMAINDER="${HOST_REMAINDER#*.}"
					;;
				*)
					HOST_LABEL="$HOST_REMAINDER"
					HOST_REMAINDER=""
					;;
			esac
			case "$HOST_LABEL" in
				""|-*|*-) reject_public_base_url ;;
			esac
			[ "${#HOST_LABEL}" -le 63 ] || reject_public_base_url
		done

		if [ "$ORIGIN_HAS_PORT" -eq 1 ]; then
			validate_origin_port "$ORIGIN_PORT"
		fi
		;;
esac
# S8b2: the restore-test drill copies blobs INTO this plain (NON-WORM) scratch bucket and tears the
# per-drill prefix down — object-lock can't be retro-added (R37), so the drill never restores into a
# locked bucket. Deliberately NOT --with-lock.
mc mb --ignore-existing local/restore-scratch
# S-ing-1: the ingestion scan content-addresses imported source bytes INTO this plain (NON-WORM)
# staging bucket; only the future commit slice promotes accepted bytes into the WORM vault. Kept
# SEPARATE from the vault check-in `staging` bucket so the import TTL-janitor never collides with a
# vault-bound staged object. Deliberately NOT --with-lock (abandoned imports leave no immutable residue).
mc mb --ignore-existing local/import-staging
mc version enable local/import-staging

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

# Batch 7 (doc 12 §4.4): a SEPARATE READ-only credential for the INDEPENDENT off-host checkpoint
# read-back verifier — distinct from the write-only sink cred above (which has NO GetObject), so the
# verifier is a custody-separated witness rather than the write path re-reading itself.
AUDIT_SINK_READ_KEY="${AUDIT_SINK_READ_ACCESS_KEY:-audit-sink-read}"
AUDIT_SINK_READ_SECRET="${AUDIT_SINK_READ_SECRET_KEY:-audit-sink-read-secret-change-me}"
mc admin user add local "$AUDIT_SINK_READ_KEY" "$AUDIT_SINK_READ_SECRET" || true
cat > /tmp/audit-sink-readonly.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::audit-checkpoints", "arn:aws:s3:::audit-checkpoints/*"]
    }
  ]
}
EOF
mc admin policy create local audit-sink-readonly /tmp/audit-sink-readonly.json || true
mc admin policy attach local audit-sink-readonly --user "$AUDIT_SINK_READ_KEY" || true

echo "minio-init: buckets ready (documents, records [WORM/${RETENTION}], renditions, staging, restore-scratch, import-staging, audit-checkpoints)"
