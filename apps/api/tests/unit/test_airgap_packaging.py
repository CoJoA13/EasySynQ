"""C13 — the air-gap bundle must yield an installable offline stack.

The bundle used to save only the third-party images from ``infra/images.lock``. Every application
service is a ``build:`` service, so on the air-gapped target Compose had to BUILD them — which
needs PyPI, npm, and the PostgreSQL apt repo. ``docker load`` therefore produced a stack that could
not start, while the runbook promised the opposite.

The packaging model these tests pin has four load-bearing parts:

1. every ``build:`` service also carries an ``image:`` name, so a loaded image satisfies Compose;
2. ``scripts/app-images.sh`` is the single source of that name set, tagged from ``VERSION`` so the
   build host and the target derive identical refs from the same checkout;
3. ``scripts/airgap-bundle.sh`` builds those images and saves them alongside the pulled set,
   re-tagging a digest-pinned ref to the plain ``name:tag`` Compose resolves (a digest pull lands
   the image UNTAGGED, which would silently send the offline ``up`` back to the network);
4. ``install.sh --offline`` neither pulls nor builds, and names any missing image up front.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "infra" / "compose" / "compose.yml").exists():
            return parent
    raise AssertionError("repository root not found above the test directory")


ROOT = _repo_root()
BASH = shutil.which("bash") or "/bin/bash"
SHA256SUM = shutil.which("sha256sum") or "/usr/bin/sha256sum"
BUILT_TAG_EXPR = "${EASYSYNQ_IMAGE_TAG:-dev}"


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _compose_services() -> dict[str, dict[str, object]]:
    model = yaml.safe_load(_read("infra/compose/compose.yml"))
    return {name: spec for name, spec in model["services"].items() if isinstance(spec, dict)}


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose's own tags (production uses ``ports: !reset []``)."""


_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _merged_service_keys() -> dict[str, set[str]]:
    """Which keys each service carries across the base file and every sizing/mode overlay.

    Overlays only tune the base today, but a service introduced by an overlay alone would still be
    started — and must still be covered by the offline overlay.
    """
    merged: dict[str, set[str]] = {}
    for path in sorted((ROOT / "infra" / "compose").glob("compose*.yml")):
        if path.name == "compose.offline.yml":
            continue
        model = yaml.load(path.read_text(), Loader=_ComposeLoader) or {}  # noqa: S506
        for name, spec in (model.get("services") or {}).items():
            if isinstance(spec, dict):
                merged.setdefault(name, set()).update(spec)
    return merged


def _split_ref(ref: str) -> tuple[str, str]:
    """Split ``repository:tag``.

    Tolerates a ``${VAR:-default}`` tag whose default value itself contains a colon.
    """
    index = re.sub(r"\$\{[^}]*\}", lambda m: "_" * len(m.group()), ref).rindex(":")
    return ref[:index], ref[index + 1 :]


def _built_image_refs() -> set[str]:
    """Every `image:` ref on a service that also carries `build:`, across all compose files."""
    refs: set[str] = set()
    for path in sorted((ROOT / "infra" / "compose").glob("compose*.yml")):
        if path.name == "compose.offline.yml":
            continue
        model = yaml.load(path.read_text(), Loader=_ComposeLoader) or {}  # noqa: S506
        for spec in (model.get("services") or {}).values():
            if isinstance(spec, dict) and "build" in spec and "image" in spec:
                refs.add(str(spec["image"]))
    return refs


def _app_images(*args: str) -> list[str]:
    result = subprocess.run(  # noqa: S603 - repository-owned script, no external input
        [BASH, str(ROOT / "scripts" / "app-images.sh"), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


# --- 1. every built service is nameable ------------------------------------------------------


def test_every_built_service_carries_an_image_name() -> None:
    """A build: service with no image: cannot be saved into the bundle or matched on the target."""
    unnamed = [
        name
        for name, keys in _merged_service_keys().items()
        if "build" in keys and "image" not in keys
    ]
    assert not unnamed, (
        f"these built services have no image: name, so the air-gap bundle cannot carry them "
        f"(C13): {unnamed}"
    )


def test_built_images_are_exactly_the_app_images_helper_set() -> None:
    """compose.yml and scripts/app-images.sh must name the same repositories.

    A seventh built service pointing at a NEW repository would be silently absent from the bundle —
    the original C13 failure, recurring.
    """
    compose_repos = {_split_ref(ref)[0] for ref in _built_image_refs()}
    helper_repos = {_split_ref(ref)[0] for ref in _app_images()}
    assert compose_repos == helper_repos, (
        f"compose.yml builds {sorted(compose_repos)} but scripts/app-images.sh names "
        f"{sorted(helper_repos)} — the bundle would miss one"
    )


def test_built_images_share_one_interpolated_tag() -> None:
    """A hard-coded tag on one service would drift from the tag the bundle saved."""
    tags = {_split_ref(ref)[1] for ref in _built_image_refs()}
    assert tags == {BUILT_TAG_EXPR}, f"built services must all use {BUILT_TAG_EXPR}, found {tags}"


# --- 2. the tag is derived, not typed --------------------------------------------------------


def test_app_image_tag_is_the_repo_version() -> None:
    """Both hosts read VERSION, which is what lets the target resolve the loaded refs."""
    assert _app_images("--tag") == [_read("VERSION").strip()]


def test_app_images_are_repository_tag_pairs() -> None:
    tag = _read("VERSION").strip()
    assert _app_images() == [f"easysynq/{name}:{tag}" for name in ("api", "web", "keycloak")]


@pytest.mark.parametrize("bad_version", ["", "1.0 beta", "-leading-dash", "a" * 200])
def test_app_images_refuses_a_version_that_is_not_a_docker_tag(
    tmp_path: Path, bad_version: str
) -> None:
    """Fail on the build host, not inside `docker save` after a multi-GB build."""
    version_file = tmp_path / "VERSION"
    version_file.write_text(bad_version)
    result = subprocess.run(  # noqa: S603 - repository-owned script, no external input
        [BASH, str(ROOT / "scripts" / "app-images.sh")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "EASYSYNQ_VERSION_FILE": str(version_file)},
    )
    assert result.returncode != 0, f"accepted an invalid tag: {bad_version!r}"
    assert result.stdout.strip() == ""


# --- 3. the bundle carries both halves -------------------------------------------------------


def test_bundle_builds_every_application_image() -> None:
    bundle = _read("scripts/airgap-bundle.sh")
    # The CONTEXT is part of the contract: building the api image with the apps/api directory as
    # its context (rather than the repo root) ships a broken image that still matches a -f/-t pin.
    for dockerfile, image, context in (
        ("apps/api/Dockerfile", "easysynq/api", '"$ROOT"'),
        ("apps/web/Dockerfile", "easysynq/web", '"$ROOT/apps/web"'),
        (
            "infra/compose/keycloak/Dockerfile",
            "easysynq/keycloak",
            '"$ROOT/infra/compose/keycloak"',
        ),
    ):
        fragment = f'-f "$ROOT/{dockerfile}" -t "{image}:$TAG"'
        assert fragment in bundle, (
            f"the bundle does not build {image} — the target would have to compile it offline"
        )
        after = bundle.split(fragment, 1)[1]
        assert after.split("docker build", 1)[0].find(context) != -1, (
            f"{image} is not built with context {context}"
        )


def test_bundle_saves_the_application_images_with_the_pulled_set() -> None:
    bundle = _read("scripts/airgap-bundle.sh")
    assert 'SAVE=("${APP_IMAGES[@]}")' in bundle
    assert 'docker save "${SAVE[@]}"' in bundle
    assert "${LOCKED[@]}" in bundle


def test_bundle_retags_a_digest_pinned_ref_to_the_compose_tag() -> None:
    """`docker pull name:tag@sha256:…` lands the image UNTAGGED.

    Saving that ref would make `docker load` produce an image Compose cannot resolve, so the
    offline `up` would fall back to a network pull — on a release build only, since the digest pin
    is applied by the release ceremony.
    """
    bundle = _read("scripts/airgap-bundle.sh")
    assert 'tagged="${ref%@sha256:*}"' in bundle
    assert 'docker tag "$ref" "$tagged"' in bundle
    assert 'SAVE+=("$tagged")' in bundle, "the bundle must save the retagged ref, not the digest"


# --- 3b. run the whole bundle script against a controlled fake docker -------------------------

_FAKE_DOCKER = """#!/usr/bin/env bash
# Records its argv and, for `save -o FILE`, materialises FILE so the script can hash it.
set -euo pipefail
printf '%s\\n' "$*" >> "$DOCKER_CALL_LOG"
if [ "${1:-}" = "save" ]; then
  out=""
  while [ $# -gt 0 ]; do
    if [ "$1" = "-o" ]; then out="$2"; fi
    shift
  done
  printf 'fake-bundle-bytes\\n' > "$out"
fi
exit 0
"""


def _run_bundle(tmp_path: Path, images_lock: Path | None = None) -> tuple[Path, list[str]]:
    """Run scripts/airgap-bundle.sh end to end with docker stubbed out."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    call_log = tmp_path / "docker-calls.txt"
    call_log.touch()

    out = tmp_path / "dist" / "easysynq-airgap.tar"
    result = subprocess.run(  # noqa: S603 - repository-owned script, no external input
        [BASH, str(ROOT / "scripts" / "airgap-bundle.sh"), str(out)],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "DOCKER_CALL_LOG": str(call_log),
            **({"EASYSYNQ_IMAGES_LOCK": str(images_lock)} if images_lock else {}),
        },
    )
    assert result.returncode == 0, result.stderr
    return out, call_log.read_text().splitlines()


def test_bundle_sidecar_verifies_on_a_host_that_never_had_the_build_path(tmp_path: Path) -> None:
    """`sha256sum "$OUT"` records the BUILD HOST's absolute path.

    On the air-gapped target that path does not exist, so the very first step of the offline
    install — the transfer-integrity check — reports "FAILED open or read". It also stamps the
    builder's home directory into a shipped artifact.
    """
    out, _ = _run_bundle(tmp_path)
    sidecar = out.with_suffix(".tar.sha256").read_text().strip()
    assert sidecar.split()[1] == out.name, (
        f"the sidecar names {sidecar.split()[1]!r}; only a bare filename verifies on the target"
    )

    # Prove it: move the bundle somewhere the build path does not exist and check it there.
    target = tmp_path / "target"
    target.mkdir()
    (target / out.name).write_bytes(out.read_bytes())
    (target / f"{out.name}.sha256").write_text(f"{sidecar}\n")
    verified = subprocess.run(  # noqa: S603 - fixed coreutils check on a temp file
        [SHA256SUM, "-c", f"{out.name}.sha256"],
        cwd=target,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr


def test_bundle_run_builds_saves_and_manifests_the_application_images(tmp_path: Path) -> None:
    out, calls = _run_bundle(tmp_path)
    tag = _read("VERSION").strip()

    built = [c for c in calls if c.startswith("build ")]
    assert len(built) == 3, f"expected three application builds, got {built}"
    for image in (f"easysynq/{n}:{tag}" for n in ("api", "web", "keycloak")):
        assert any(f"-t {image}" in c for c in built), f"{image} was not built"

    saved = next(c for c in calls if c.startswith("save "))
    for image in (f"easysynq/{n}:{tag}" for n in ("api", "web", "keycloak")):
        assert image in saved, f"{image} was not saved into the bundle"
    assert "postgres:16" in saved, "the pulled set must be saved alongside the built images"

    manifest = out.with_suffix(".tar.manifest.txt").read_text()
    assert f"easysynq/api:{tag}" in manifest
    assert "postgres:16" in manifest


def _install_tree(tmp_path: Path) -> Path:
    """A throwaway copy of just what install.sh reads, so the real repo .env is never touched."""
    tree = tmp_path / "repo"
    (tree / "scripts").mkdir(parents=True)
    for name in (
        "install.sh",
        "app-images.sh",
        "validate-dns-name.sh",
        "ensure-keycloak-db-password.sh",
    ):
        shutil.copy2(ROOT / "scripts" / name, tree / "scripts" / name)
    shutil.copy2(ROOT / "VERSION", tree / "VERSION")
    shutil.copy2(ROOT / ".env.example", tree / ".env.example")
    return tree


def _run_install_env_only(
    tmp_path: Path, tree: Path | None = None, expect_success: bool = True
) -> object:
    tree = tree or _install_tree(tmp_path)
    result = subprocess.run(  # noqa: S603 - repository-owned script on a throwaway copy
        [BASH, str(tree / "scripts" / "install.sh"), "s"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "EASYSYNQ_ENV_ONLY": "1"},
    )
    if expect_success:
        assert result.returncode == 0, result.stderr
        return tree / ".env"
    return result


def test_bundle_stamps_the_build_revision_on_every_built_image(tmp_path: Path) -> None:
    """VERSION is a static release string; it cannot tell two checkouts apart.

    Without a build identity, a bundle built from commit A satisfies the pre-flight on a checkout
    at commit B — the target runs A's application code against B's migrations and configuration,
    silently.
    """
    out, calls = _run_bundle(tmp_path)
    built = [c for c in calls if c.startswith("build ")]
    assert built
    for call in built:
        assert "--label org.opencontainers.image.revision=" in call, (
            f"an unlabelled image cannot be matched to its checkout: {call}"
        )
    assert "# built from revision " in out.with_suffix(".tar.manifest.txt").read_text()


def test_bundle_saves_a_digest_pinned_ref_under_its_plain_tag(tmp_path: Path) -> None:
    """The release ceremony rewrites images.lock to `name:tag@sha256:…`.

    `docker pull name:tag@sha256:…` lands the image UNTAGGED, so saving that ref would produce a
    tarball whose loaded images Compose cannot resolve — sending the offline `up` back to the
    network on release builds only. images.lock ships tag-pinned, so nothing else exercises this.
    """
    lock = tmp_path / "images.lock"
    digest = "sha256:" + "1" * 64
    lock.write_text(f"# service image\npostgres postgres:16@{digest}\n")
    _, calls = _run_bundle(tmp_path, images_lock=lock)

    assert any(c == f"pull --quiet postgres:16@{digest}" for c in calls), (
        "the pull must use the DIGEST — that is the immutability guarantee"
    )
    assert f"tag postgres:16@{digest} postgres:16" in calls, "the digest ref was never re-tagged"
    saved = next(c for c in calls if c.startswith("save "))
    assert "postgres:16 " in f"{saved} ", "the plain tag Compose resolves was not saved"
    assert digest not in saved, "saving the digest ref lands an UNTAGGED image on the target"


def test_installer_env_only_writes_the_derived_tag(tmp_path: Path) -> None:
    """Behavioural: the appliance's EASYSYNQ_ENV_ONLY path must leave a usable tag in .env."""
    env_file = _run_install_env_only(tmp_path)
    tag = _read("VERSION").strip()
    assert f"EASYSYNQ_IMAGE_TAG={tag}" in env_file.read_text()


def test_installer_env_only_aborts_rather_than_writing_an_empty_tag(tmp_path: Path) -> None:
    """`set_kv KEY "$(cmd)"` swallows cmd's failure; the empty tag then resolves to `dev`."""
    tree = _install_tree(tmp_path)
    (tree / "VERSION").write_text("not a valid tag")
    result = _run_install_env_only(tmp_path, tree=tree, expect_success=False)
    assert result.returncode != 0, "a bad VERSION must abort, not write an empty tag"
    env_text = (tree / ".env").read_text() if (tree / ".env").exists() else ""
    assert "EASYSYNQ_IMAGE_TAG=\n" not in env_text
    assert "EASYSYNQ_IMAGE_TAG=" not in env_text.replace("EASYSYNQ_IMAGE_TAG=dev", "")


# --- 4. the offline install neither pulls nor builds ------------------------------------------


def test_offline_overlay_forbids_pulling_every_service_including_the_built_ones() -> None:
    """`--no-build` does NOT suppress a fetch for a `build:` service — it converts it into a PULL.

    Measured on Compose v5: with the image absent, `up --no-build` emits
    "Image easysynq/api:0.1.0 Pulling" and reaches for docker.io. That is a hang on an air-gapped
    host, and `docker.io/easysynq/*` is a registrable user namespace this project does not own.
    Only `pull_policy: never` closes it, and it leaves an ordinary online `up` still building.
    """
    overlay = yaml.safe_load(_read("infra/compose/compose.offline.yml"))["services"]
    startable = set(_merged_service_keys())
    assert set(overlay) == startable, (
        f"compose.offline.yml covers {sorted(overlay)} but the services are {sorted(startable)}"
    )
    assert all(spec["pull_policy"] == "never" for spec in overlay.values())


def test_installer_offline_neither_pulls_nor_builds() -> None:
    install = _read("scripts/install.sh")
    assert "-f infra/compose/compose.offline.yml" in install
    assert "UP=(up -d --no-build)" in install, (
        "pull_policy cannot stop a BUILD; --no-build is the other half"
    )
    # The online path keeps building; only the offline branch must not.
    assert "UP=(up -d --build)" in install


def test_installer_offline_requires_internal_tls() -> None:
    """ACME cannot reach Let's Encrypt from an air-gapped host."""
    install = _read("scripts/install.sh")
    assert re.search(
        r'if \[ "\$OFFLINE" = "1" \] && \[ "\$TLS_MODE" != "internal" \]; then', install
    )


def test_installer_names_every_missing_image_before_compose_runs() -> None:
    """A partially transferred bundle must say what is missing, not fail deep in startup."""
    install = _read("scripts/install.sh")
    assert '"${COMPOSE[@]}" config --images' in install, (
        "the pre-flight must derive its list from the resolved model, not a hand-maintained one"
    )
    assert 'docker image inspect "$image"' in install
    assert 'MISSING+=("$image")' in install
    # The api image backs four services; reporting it four times reads like four problems.
    assert "config --images | sort -u" in install


def test_installer_pins_the_image_tag_before_the_env_only_exit() -> None:
    """The appliance provisioner runs Compose itself after EASYSYNQ_ENV_ONLY=1 returns."""
    install = _read("scripts/install.sh")
    pin = install.index('set_kv EASYSYNQ_IMAGE_TAG "$IMAGE_TAG"')
    env_only_exit = install.index('if [ "$ENV_ONLY" = "1" ]; then')
    assert pin < env_only_exit, "EASYSYNQ_ENV_ONLY=1 would exit before the tag is written"


def test_the_image_tag_is_assigned_before_use_so_a_failure_aborts() -> None:
    """`set_kv KEY "$(cmd)"` does NOT propagate cmd's exit status — `set -e` never fires.

    A failing app-images.sh would then write an empty tag, Compose would resolve the `dev` default,
    and the offline pre-flight would report a tag that appears nowhere in the bundle.
    """
    install = _read("scripts/install.sh")
    assert 'IMAGE_TAG="$(bash "$ROOT/scripts/app-images.sh" --tag)"' in install
    assert 'set_kv EASYSYNQ_IMAGE_TAG "$(bash' not in install

    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    assert 'image_tag="$(bash "$APP_DIR/scripts/app-images.sh" --tag)"' in provisioner
    assert 'set_kv EASYSYNQ_IMAGE_TAG "$(bash' not in provisioner


def test_appliance_provisioner_pins_the_image_tag_on_a_reprovision() -> None:
    """The provisioner only runs install.sh when .env is ABSENT.

    A re-provision over an existing .env would otherwise leave EASYSYNQ_IMAGE_TAG unset and keep
    building the `dev` fallback images instead of this release's.
    """
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    assert 'set_kv EASYSYNQ_IMAGE_TAG "$image_tag"' in provisioner


# --- the runbook must describe what the scripts actually do -----------------------------------


def test_airgap_runbook_documents_the_offline_install() -> None:
    runbook = _read("docs/runbooks/install-airgapped.md")
    assert "--offline" in runbook
    assert "easysynq/api" in runbook, "the runbook must say the bundle carries the built images"
    # The identity claim must describe the MECHANISM that enforces it. A bare mention of VERSION
    # cannot tell whether the sentence around it is true — VERSION is a static release string and
    # by itself proves nothing about which checkout a bundle came from.
    assert "org.opencontainers.image.revision" in runbook, (
        "the runbook must name the build stamp that actually detects a mismatched checkout"
    )
    assert "converts it into a *pull*" in runbook, (
        "the runbook must say why --no-build alone is not enough"
    )
