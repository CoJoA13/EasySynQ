# Install (air-gapped)

For a host/network with **no outbound internet** (D1: data never leaves the org's infra; no
phone-home). The bundle is built on a connected host and transferred offline.

The bundle carries **both halves of the stack**: the pinned third-party images from
`infra/images.lock` *and* the three images this repository builds — `easysynq/api` (which backs
`migrate`/`api`/`worker`/`beat`), `easysynq/web`, and `easysynq/keycloak`. Building those on the
target is impossible offline (they need PyPI, npm, and the PostgreSQL apt repo), so the connected
build host does it once and ships the layers.

Transfer the **same checkout** you built from. Both hosts derive the image tag from the repo's
`VERSION` file, so the target resolves the refs the bundle loaded without anyone typing a tag.

`VERSION` alone cannot tell two checkouts apart — it is a static release string, not a build
identity. So `airgap-bundle.sh` stamps the build's git revision onto each image it builds
(`org.opencontainers.image.revision`) and records it in the manifest, and `install.sh --offline`
refuses to start when the loaded images were built from a different revision than the checkout it
is running from. On a checkout with no git metadata it reports the built revision and continues,
since it has nothing to compare against.

## On a CONNECTED build host

1. **Pin images by digest** (release ceremony — needs Docker + network):
   ```bash
   just images-update      # prints image:tag@sha256:… for each line in infra/images.lock
   ```
   Replace the tag-pinned lines in `infra/images.lock` with the printed `@sha256:` refs and commit
   for the release. `test_images_lock_pinned.py` fails on any non-dev image still carrying a
   floating tag, but it SKIPS unless `EASYSYNQ_RELEASE=1` is set — and no workflow sets it, so run
   it by hand as part of the ceremony:

   ```bash
   EASYSYNQ_RELEASE=1 uv run --directory apps/api pytest tests/unit/test_images_lock_pinned.py
   ```

   Floating tags stay legal during normal development. The three images built from this repository
   are identified by the `VERSION` tag rather than a digest, so this pins the third-party half only.

2. **Build the bundle:**
   ```bash
   just airgap            # build the app images, pull the pinned set → dist/easysynq-airgap.tar
   ```
   This builds `easysynq/{api,web,keycloak}` from this checkout, pulls every locked image, and
   `docker save`s the lot. Expect several GB and a long first run — the Python wheels
   (`uv sync --no-dev`) and the built SPA (`npm ci && build`) are baked into the image layers, so no
   separate wheel or npm store is needed.

   Three files land in `dist/`: the `.tar`, a `.sha256` for transfer integrity, and a
   `.manifest.txt` naming every image inside. A digest-pinned image is fetched by digest and saved
   under the plain `name:tag` that Compose resolves — without that, `docker load` would land it
   untagged and the offline `up` would fall back to a network pull.

## On the AIR-GAPPED target

3. Transfer `easysynq-airgap.tar` (+ `.sha256`) and the repo checkout, then:
   ```bash
   sha256sum -c easysynq-airgap.tar.sha256      # verify transfer integrity
   docker load -i easysynq-airgap.tar
   ```
4. Run the production installer in offline mode:

   ```bash
   ./scripts/install.sh s --host qms.corp.example --tls internal --offline
   ```

   `--offline` stacks `compose.offline.yml` (every service becomes `pull_policy: never`) and starts
   the stack with `up --no-build`. Both halves are needed: for a service that has a `build:` stanza,
   `--no-build` does not suppress the fetch, it converts it into a *pull*. Before Compose runs at all, the
   installer inspects every image in the resolved composition and lists any that are not loaded —
   a partially transferred bundle names what is missing instead of failing deep in the startup
   order. `--offline` requires `--tls internal`; ACME cannot reach Let's Encrypt from here.

   Create the DNS record first. The installer sets the app/Keycloak origin to
   `https://qms.corp.example`, the dedicated S3 origin to `https://qms.corp.example:9443`, and uses
   `compose.production.yml`; plaintext MinIO `:9000` is never published. The internal CA requires a
   hostname and must be distributed to workstations. Then follow
   [install-online.md](install-online.md) from the CA-distribution step onward.

   For a hand-authored Compose invocation, stack `compose.airgap.yml` (internal-CA TLS) **and**
   `compose.offline.yml` **and** `compose.production.yml`, pass `up --no-build`, and set all
   required browser variables shown in `.env.example` plus `EASYSYNQ_IMAGE_TAG` (the value printed
   by `scripts/app-images.sh --tag`). The production overlay deliberately fails Compose
   interpolation when one is missing.

## Assumed network capabilities
No outbound HTTP. The browser reaches Caddy on 443 (app/Keycloak) and 9443 (presigned S3); everything
else is the internal Docker network. If your org uses a private registry or internal NTP/DNS,
document those as the operator's responsibility — they are not provided by the bundle.
