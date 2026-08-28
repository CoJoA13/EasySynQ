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
        for name, spec in _compose_services().items()
        if "build" in spec and "image" not in spec
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
    compose_repos = {
        _split_ref(str(spec["image"]))[0]
        for spec in _compose_services().values()
        if "build" in spec and "image" in spec
    }
    helper_repos = {_split_ref(ref)[0] for ref in _app_images()}
    assert compose_repos == helper_repos, (
        f"compose.yml builds {sorted(compose_repos)} but scripts/app-images.sh names "
        f"{sorted(helper_repos)} — the bundle would miss one"
    )


def test_built_images_share_one_interpolated_tag() -> None:
    """A hard-coded tag on one service would drift from the tag the bundle saved."""
    tags = {
        _split_ref(str(spec["image"]))[1]
        for spec in _compose_services().values()
        if "build" in spec and "image" in spec
    }
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
    for dockerfile, image in (
        ("apps/api/Dockerfile", "easysynq/api"),
        ("apps/web/Dockerfile", "easysynq/web"),
        ("infra/compose/keycloak/Dockerfile", "easysynq/keycloak"),
    ):
        assert f'docker build -f "$ROOT/{dockerfile}" -t "{image}:$TAG"' in bundle, (
            f"the bundle does not build {image} — the target would have to compile it offline"
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


# --- 4. the offline install neither pulls nor builds ------------------------------------------


def test_offline_overlay_forbids_pulling_every_pulled_service() -> None:
    """Exhaustive by construction: a new pulled service must be added to the overlay."""
    overlay = yaml.safe_load(_read("infra/compose/compose.offline.yml"))["services"]
    pulled = {
        name
        for name, keys in _merged_service_keys().items()
        if "image" in keys and "build" not in keys
    }
    assert set(overlay) == pulled, (
        f"compose.offline.yml covers {sorted(overlay)} but the pulled services are {sorted(pulled)}"
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
    pin = install.index('set_kv EASYSYNQ_IMAGE_TAG "$(bash "$ROOT/scripts/app-images.sh" --tag)"')
    env_only_exit = install.index('if [ "$ENV_ONLY" = "1" ]; then')
    assert pin < env_only_exit, "EASYSYNQ_ENV_ONLY=1 would exit before the tag is written"


def test_appliance_provisioner_pins_the_image_tag_on_a_reprovision() -> None:
    """The provisioner only runs install.sh when .env is ABSENT.

    A re-provision over an existing .env would otherwise leave EASYSYNQ_IMAGE_TAG unset and keep
    building the `dev` fallback images instead of this release's.
    """
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    assert (
        'set_kv EASYSYNQ_IMAGE_TAG "$(bash "$APP_DIR/scripts/app-images.sh" --tag)"' in provisioner
    )


# --- the runbook must describe what the scripts actually do -----------------------------------


def test_airgap_runbook_documents_the_offline_install() -> None:
    runbook = _read("docs/runbooks/install-airgapped.md")
    assert "--offline" in runbook
    assert "easysynq/api" in runbook, "the runbook must say the bundle carries the built images"
    assert "VERSION" in runbook, "the operator must know both hosts need the same checkout"
