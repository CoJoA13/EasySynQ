# Install (air-gapped)

For a host/network with **no outbound internet** (D1: data never leaves the org's infra; no
phone-home). The bundle is built on a connected host and transferred offline.

## On a CONNECTED build host

1. **Pin images by digest** (release ceremony — needs Docker + network):
   ```bash
   just images-update      # prints image:tag@sha256:… for each line in infra/images.lock
   ```
   Replace the tag-pinned lines in `infra/images.lock` with the printed `@sha256:` refs and commit
   for the release. A release CI run (`EASYSYNQ_RELEASE=1`) fails if any non-dev image is still a
   floating tag (`test_images_lock_pinned.py`). Floating tags stay legal during normal development.

2. **Build the bundle:**
   ```bash
   just airgap            # docker save of the pinned images → dist/easysynq-airgap.tar (+ .sha256)
   ```
   The application Python wheels (`uv sync --no-dev`) and the built SPA (`npm ci && build`) are
   baked **into the image layers**, so `docker load` yields a fully-installable offline stack — no
   separate wheel/npm store is needed.

## On the AIR-GAPPED target

3. Transfer `easysynq-airgap.tar` (+ `.sha256`) + the repo, then:
   ```bash
   sha256sum -c easysynq-airgap.tar.sha256      # verify transfer integrity
   docker load -i easysynq-airgap.tar
   ```
4. Bring the stack up with the **air-gap overlay** (disables ACME — supply your own/internal TLS):
   ```bash
   docker compose -f infra/compose/compose.yml -f infra/compose/compose.s.yml \
                  -f infra/compose/compose.airgap.yml up -d
   ```
   Set `SITE_ADDRESS` to your domain and provide a cert, or use the internal self-signed issuer
   (`CADDY_TLS_INTERNAL`). Then follow [install-online.md](install-online.md) steps 2–4.

## Assumed network capabilities
No outbound HTTP. The browser reaches Caddy on the published port; everything else is the internal
Docker network. If your org uses a private registry or internal NTP/DNS, document those as the
operator's responsibility — they are not provided by the bundle.
