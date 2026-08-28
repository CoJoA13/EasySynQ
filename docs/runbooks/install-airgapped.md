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
   Paste the printed refs into `infra/images.lock`, keeping the service column, and commit for the
   release. The recipe retries and **exits non-zero** if any image is unresolved rather than
   printing a partial list — never paste output from a failed run.

   ⚠ **Docker Hub rate-limits anonymous manifest requests.** In practice a second run minutes after
   a successful one returned `429 Too Many Requests` for six of nine images. Run `docker login`
   before the ceremony, or wait for the window to reset. Check it before tagging:

   ```bash
   just release-check
   ```

   CI runs the same guard on any `v*` tag (the `release-gate` job), so a tagged release cannot ship
   a floating third-party tag. Floating tags stay legal during normal development. The three images
   built from this repository are identified by their image id in the bundle manifest rather than a
   registry digest — they are never pushed, so they have none.

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
   sha256sum -c easysynq-airgap.tar.sha256      # the tarball survived transfer intact
   docker load -i easysynq-airgap.tar
   bash scripts/verify-bundle.sh easysynq-airgap.tar.manifest.txt
   ```

   The three checks answer different questions. The checksum proves the *tarball* is intact; the
   images' `org.opencontainers.image.revision` label proves they were built from the checkout you
   are installing from; only `verify-bundle.sh` proves the images now loaded are the ones **this**
   bundle carried — an older tarball built from the same commit loads the same tags and satisfies
   the other two. It compares each built image's id, which is stable across `docker save`/`load`.
4. Run the production installer in offline mode:

   ```bash
   ./scripts/install.sh s --host qms.corp.example --tls internal --offline \
       --bundle-manifest easysynq-airgap.tar.manifest.txt
   ```

   `--bundle-manifest` re-runs the bundle verification as part of the install, so a partially
   replaced image set cannot slip past between the manual check and the start. Without it the
   installer says so rather than staying silent.

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

## Container users

The application containers run unprivileged — the api image as `easysynq` (uid/gid **10001**), the
web image as `node` (uid **1000**). A fresh install needs no action: Docker seeds a newly created
named volume with the ownership of the image directory behind it.

Two things are specific to an air-gapped host:

- If you are reusing volumes created by an earlier root-running build, chown them with an image the
  bundle actually contains — `alpine` is not in it:

  ```bash
  TAG=$(bash scripts/app-images.sh --tag)
  for v in easysynq_mirror easysynq_secrets easysynq_backup; do
    docker run --rm --user 0 -v "$v":/v "easysynq/api:$TAG" chown -R 10001:10001 /v
  done
  ```

  Never `docker compose down -v` — it removes every volume in the project, including `pgdata` and
  `miniodata`. See the volume table in [install-online.md](install-online.md#container-users-and-selinux).

- `IMPORT_SOURCE_PATH` must be readable and traversable by uid 10001. A directory the worker cannot
  enter is skipped, so the import reports fewer files rather than an error.

## Assumed network capabilities
No outbound HTTP. The browser reaches Caddy on 443 (app/Keycloak) and 9443 (presigned S3); everything
else is the internal Docker network. If your org uses a private registry or internal NTP/DNS,
document those as the operator's responsibility — they are not provided by the bundle.
