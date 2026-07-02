#!/usr/bin/env bash
# Build the EasySynQ Hyper-V appliance: an Ubuntu-cloud-image VHDX + a cloud-init seed ISO.
#
#   infra/appliance/build-appliance.sh [--out DIR] [--qcow2-only]
#
# Outputs (in --out, default infra/appliance/dist/):
#   EasySynQ-appliance.vhdx   the OS disk (dynamic VHDX; Install-EasySynQ.ps1 resizes it)
#   EasySynQ-seed.iso         cloud-init NoCloud seed (cidata) carrying the pinned repo bundle
#   Install-EasySynQ.ps1      copied alongside for a one-folder hand-off
#
# Host requirements: bash, curl, qemu-img, git, and `uv` (for the pycdlib seed builder).
# No root needed. The VHDX is the UNMODIFIED upstream cloud image converted to VHDX —
# all provisioning happens at first boot from the seed (auditable in this directory).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/dist"
QCOW2_ONLY=0
UBUNTU_SERIES="noble" # 24.04 LTS
IMG_URL="https://cloud-images.ubuntu.com/${UBUNTU_SERIES}/current/${UBUNTU_SERIES}-server-cloudimg-amd64.img"
CACHE="$HERE/.cache"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --qcow2-only) QCOW2_ONLY=1; shift ;; # skip VHDX conversion (QEMU boot-test path)
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

for t in curl qemu-img git; do
  command -v "$t" >/dev/null || { echo "missing tool: $t" >&2; exit 1; }
done
UV="${UV:-$HOME/.local/bin/uv}"
command -v "$UV" >/dev/null || UV=uv
command -v "$UV" >/dev/null || { echo "missing tool: uv" >&2; exit 1; }

mkdir -p "$OUT" "$CACHE"

# 1. Fetch (and cache) the upstream cloud image + verify its published checksum.
BASE_IMG="$CACHE/${UBUNTU_SERIES}-server-cloudimg-amd64.img"
if [ ! -f "$BASE_IMG" ]; then
  echo "appliance: downloading Ubuntu ${UBUNTU_SERIES} cloud image..."
  curl -fL --retry 3 -o "$BASE_IMG.part" "$IMG_URL"
  mv "$BASE_IMG.part" "$BASE_IMG"
fi
echo "appliance: verifying image checksum against the published SHA256SUMS..."
pushd "$CACHE" >/dev/null
curl -fsL --retry 3 -o SHA256SUMS "https://cloud-images.ubuntu.com/${UBUNTU_SERIES}/current/SHA256SUMS"
grep " *${UBUNTU_SERIES}-server-cloudimg-amd64.img\$" SHA256SUMS | sha256sum -c - \
  || { echo "appliance: checksum MISMATCH — refusing to build (delete $CACHE and retry)" >&2; exit 1; }
popd >/dev/null

# 2. Snapshot the repo at the current commit into the seed bundle (no creds needed in the VM).
# git archive ships HEAD, NOT the working tree — a dirty tree would stamp a clean sha over an
# artifact that silently lacks the uncommitted changes. Refuse unless explicitly overridden.
if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ] && [ "${ALLOW_DIRTY:-0}" != "1" ]; then
  echo "appliance: working tree is DIRTY — commit first (the bundle ships HEAD), or ALLOW_DIRTY=1" >&2
  exit 1
fi
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "appliance: bundling repo @ $GIT_SHA..."
git -C "$REPO_ROOT" archive --format=tar.gz -o "$OUT/easysynq-repo.tar.gz" HEAD

# 3. Bundle the provisioning payload (first-boot script + helpers + systemd unit).
tar -C "$HERE" -czf "$OUT/easysynq-provision.tar.gz" provision

# 4. Build the NoCloud seed ISO (volume label `cidata`): user-data + meta-data + the two bundles.
echo "appliance: building seed ISO..."
"$UV" run --quiet --with pycdlib python3 "$HERE/seed/make-seed-iso.py" \
  --user-data "$HERE/seed/user-data.yaml" \
  --meta-data "$HERE/seed/meta-data.yaml" \
  --extra "$OUT/easysynq-repo.tar.gz" \
  --extra "$OUT/easysynq-provision.tar.gz" \
  --version "$GIT_SHA" \
  --out "$OUT/EasySynQ-seed.iso"
rm -f "$OUT/easysynq-repo.tar.gz" "$OUT/easysynq-provision.tar.gz"

# 5. OS disk: copy the pristine image; convert to dynamic VHDX for Hyper-V (skip for QEMU tests).
cp -f "$BASE_IMG" "$OUT/EasySynQ-appliance.qcow2"
if [ "$QCOW2_ONLY" -eq 0 ]; then
  echo "appliance: converting to VHDX..."
  qemu-img convert -O vhdx -o subformat=dynamic "$OUT/EasySynQ-appliance.qcow2" \
    "$OUT/EasySynQ-appliance.vhdx"
  rm -f "$OUT/EasySynQ-appliance.qcow2"
fi

cp -f "$HERE/Install-EasySynQ.ps1" "$OUT/"
echo "appliance: done → $OUT"
ls -lh "$OUT"
