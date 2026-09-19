"""Audit U33-U36, U40, U43, U44 — infrastructure and operator-tooling invariants.

These are source-level pins for properties CI cannot otherwise observe: CI builds nothing from the
Dockerfiles and never runs a container without a network, so a regression here ships green.

The load-bearing one is ``UV_NO_SYNC``. ``uv run`` syncs the environment on every invocation and
resolves the DEV dependency group the image deliberately did not install, so every container start
reached PyPI. Verified against an image built from ``main``: ``uv run alembic --version`` under
``--network none`` fails with a DNS error, which means an air-gapped stack died on ``migrate``
before anything else ran.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
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


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _instructions(dockerfile: str) -> list[str]:
    """The Dockerfile's actual instruction lines.

    A substring check cannot tell ``USER easysynq`` from ``# USER easysynq`` — commenting the
    directive out would leave the container running as root and still satisfy the assertion.
    """
    return [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


# --- U33: the application containers must not run as root ------------------------------------


def test_api_image_runs_unprivileged_and_owns_its_volume_mount_points() -> None:
    dockerfile = _read("apps/api/Dockerfile")
    assert "USER easysynq" in _instructions(dockerfile)
    # Pre-creating the mount points is what makes a FRESH named volume writable without an
    # operator step — Docker seeds a new volume from the image directory behind it.
    for path in (
        "/var/lib/easysynq/qms-mirror",
        "/var/lib/easysynq/backups",
        "/run/secrets",
    ):
        assert path in dockerfile, f"{path} is a written volume mount and must be pre-owned"
    assert "install -d -o easysynq -g easysynq" in dockerfile
    # NON-recursive on purpose. Only /app itself must be writable (Celery Beat's schedule file
    # lands in the CWD); recursing rewrote the 159 MB venv into a duplicated layer, and these
    # images ship in the air-gap bundle. UV_NO_SYNC means nothing writes into the venv at runtime.
    assert "chown easysynq:easysynq /app" in dockerfile
    assert "chown -R easysynq:easysynq /app" not in dockerfile


def test_web_image_runs_unprivileged() -> None:
    assert "USER node" in _instructions(_read("apps/web/Dockerfile"))


_CLEAR_SETUID = "&& find / -xdev -perm /6000 -type f -exec chmod a-s {} +"
_APP_SERVICES = ("migrate", "api", "worker", "beat", "web")


@pytest.mark.parametrize("dockerfile", ["apps/api/Dockerfile", "apps/web/Dockerfile"])
def test_images_clear_setuid_and_setgid_bits(dockerfile: str) -> None:
    """The base's setuid tools (mount, su, passwd, ...) are the privilege-gain path (#4)."""
    instructions = _instructions(_read(dockerfile))
    assert _CLEAR_SETUID in instructions
    # Before USER: an unprivileged `find` could not clear root-owned bits and would pass silently.
    assert instructions.index(_CLEAR_SETUID) < next(
        index for index, line in enumerate(instructions) if line.startswith("USER ")
    )


def test_app_services_drop_capabilities_and_forbid_privilege_gain() -> None:
    services = yaml.safe_load(_read("infra/compose/compose.yml"))["services"]
    for name in _APP_SERVICES:
        assert services[name]["cap_drop"] == ["ALL"], name
        assert services[name]["security_opt"] == ["no-new-privileges:true"], name
    # Caddy binds 80/443 and keeps its capability; dropping ALL there breaks the edge.
    assert "cap_drop" not in services["proxy"]


def test_the_api_user_has_a_fixed_uid() -> None:
    """A floating uid would not match the ownership a previously-seeded volume carries."""
    dockerfile = _read("apps/api/Dockerfile")
    assert "ARG APP_UID=10001" in dockerfile
    assert "ARG APP_GID=10001" in dockerfile
    assert "--uid ${APP_UID}" in dockerfile


# --- the offline-start regression this slice found --------------------------------------------


def test_uv_run_never_syncs_at_container_start() -> None:
    """Without this the container reaches PyPI on every start — fatal on an air-gapped host.

    ``uv run`` syncs before executing, and it resolves the dev group the image did not install
    (``--no-dev``), so ``migrate``/``api``/``worker``/``beat`` all needed the network to boot.
    """
    dockerfile = _read("apps/api/Dockerfile")
    assert "UV_NO_SYNC=1" in dockerfile, (
        "uv run syncs on every invocation; without UV_NO_SYNC an offline start fails on migrate"
    )
    assert "UV_FROZEN=1" in dockerfile
    assert "UV_NO_CACHE=1" in dockerfile


def test_the_api_image_leaves_forwarded_headers_to_the_application() -> None:
    """Two proxy-trust mechanisms stacked silently re-open what one of them closes.

    uvicorn installs ``ProxyHeadersMiddleware`` unconditionally and gunicorn hands it a default
    ``forwarded_allow_ips`` of ``127.0.0.1,::1``. Measured against the pinned uvicorn: with that
    default, a loopback caller sending ``X-Forwarded-For`` has ``scope["client"]`` rewritten to a
    value it chose, *before* any application code runs — so the ``TRUSTED_PROXY_CIDRS`` check
    would be evaluating the caller's own claim as though it were the socket peer. An empty list
    disables the rewrite, leaving ``services/common/client_ip.py`` the only place that decides
    whose forwarded chain is believed.

    The CMD is parsed rather than substring-matched: a commented-out flag reads identically.
    """
    dockerfile = _read("apps/api/Dockerfile")
    joined = dockerfile.replace("\\\n", " ")
    cmd = next(
        (line for line in _instructions(joined) if line.startswith("CMD ")),
        None,
    )
    assert cmd is not None, "the api image has no CMD instruction"
    argv = json.loads(cmd[len("CMD ") :])
    assert "--forwarded-allow-ips" in argv, (
        "gunicorn defaults to trusting loopback's X-Forwarded-For; pin the flag off"
    )
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "", (
        "an empty allow-list is what disables uvicorn's rewrite"
    )


@pytest.mark.parametrize(
    "path",
    [".env.example", "infra/compose/compose.yml", "apps/api/Dockerfile", "justfile"],
)
def test_nothing_reintroduces_the_uvicorn_forwarded_trust(path: str) -> None:
    # The api service loads the repo-root .env, so this variable anywhere in the shipped
    # configuration would re-enable the rewrite the CMD above turns off.
    assert "FORWARDED_ALLOW_IPS" not in _read(path)


def test_the_dev_server_disables_the_same_rewrite_as_the_image() -> None:
    """`just api-dev` runs bare uvicorn, whose default DOES trust loopback's X-Forwarded-For.

    Measured through the real middleware: a local caller sending the header has `request.client`
    rewritten to an address of its choosing before `TRUSTED_PROXY_CIDRS` is consulted, which makes
    every ip_allow grant satisfiable and every audit attribution forgeable on a developer machine.
    The image pins the flag off; the recipe that bypasses the image has to pin it too.
    """
    lines = _read("justfile").splitlines()
    start = lines.index("api-dev:")
    body = [
        line.strip()
        for line in lines[start + 1 :]
        # stop at the next recipe (an unindented, non-blank line)
        if line.startswith((" ", "\t")) or not line.strip()
    ]
    end = next(
        (i for i, line in enumerate(lines[start + 1 :]) if line and not line[0].isspace()), None
    )
    if end is not None:
        body = [line.strip() for line in lines[start + 1 : start + 1 + end]]
    # The prose explaining the flag contains the flag, so a substring check over the whole recipe
    # passes against a command that no longer carries it. Only the executed lines count.
    command = " ".join(line for line in body if line and not line.startswith("#"))
    assert "uvicorn" in command, "the api-dev recipe no longer launches uvicorn"
    assert "--forwarded-allow-ips" in command, (
        "just api-dev runs uvicorn directly and must disable its proxy-header rewrite"
    )


def test_the_edge_does_not_preserve_a_caller_supplied_forwarded_chain_by_default() -> None:
    """Caddy REPLACES X-Forwarded-For unless a peer is named in `trusted_proxies`.

    Measured against caddy:2 (v2.11.4): with no `trusted_proxies`, a caller-sent header is
    discarded and the API receives only the address Caddy observed; naming a peer switches Caddy
    to appending, which preserves whatever that peer sent. So a non-empty default here would hand
    every caller a way to prepend an address the API might then believe. The value is
    operator-supplied and empty in the template, and this pins the empty default.
    """
    caddyfile = _read("infra/compose/caddy/Caddyfile")
    assert "{$CADDY_TRUSTED_PROXIES}" in caddyfile, (
        "the upstream-load-balancer remedy documented in .env.example needs this directive"
    )
    assert "\nCADDY_TRUSTED_PROXIES=\n" in _read(".env.example"), (
        "Caddy must replace the forwarded chain unless an operator names an upstream edge"
    )
    # The placeholder is substituted from the CONTAINER's environment, and a value in .env reaches
    # Compose for interpolation only. Without this passthrough the directive always expands to
    # empty and an operator who configured both documented halves would still have every request
    # attributed to the proxy — the exact silent failure, reached by following the documentation.
    assert "CADDY_TRUSTED_PROXIES: ${CADDY_TRUSTED_PROXIES:-}" in _read("infra/compose/compose.yml")


# --- U34: the image installs exactly what the lockfile pins ------------------------------------


def test_image_dependencies_come_from_the_lockfile_only() -> None:
    dockerfile = _read("apps/api/Dockerfile")
    assert "uv sync --no-dev --no-install-project" not in dockerfile
    assert dockerfile.count("uv sync --locked") == 2, "both sync layers must be --locked"
    # A globbed `uv.lock*` silently tolerates a MISSING lockfile and resolves fresh. Inspect the
    # COPY directives rather than the raw text — the Dockerfile comment quotes the glob it warns
    # against, and a substring check would match that instead of the instruction.
    copies = [ln for ln in dockerfile.splitlines() if ln.startswith("COPY ")]
    assert not [ln for ln in copies if "uv.lock*" in ln], f"globbed lockfile COPY: {copies}"
    assert "COPY apps/api/pyproject.toml apps/api/uv.lock ./" in copies


# --- U35: SELinux labels on the config bind mounts, but never on the customer's tree ------------


@pytest.mark.parametrize(
    ("compose_file", "mount"),
    [
        ("infra/compose/compose.yml", "./minio:/init:ro,z"),
        ("infra/compose/compose.yml", "./keycloak/keycloak-init.sh:/init/keycloak-init.sh:ro,z"),
        (
            "infra/compose/compose.yml",
            "./keycloak/realm-export.json:/seed/easysynq-realm.json:ro,z",
        ),
        ("infra/compose/compose.yml", "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro,z"),
        (
            "infra/compose/compose.production.yml",
            "./caddy/Caddyfile.production:/etc/caddy/Caddyfile:ro,z",
        ),
        (
            "infra/compose/compose.production.yml",
            "./caddy/Caddyfile:/etc/caddy/Caddyfile.base:ro,z",
        ),
    ],
)
def test_shipped_config_bind_mounts_carry_the_selinux_label(compose_file: str, mount: str) -> None:
    """Unlabelled, these fail with permission-denied on an SELinux-labelling runtime."""
    assert mount in _read(compose_file)


def test_the_customer_document_tree_is_never_relabelled() -> None:
    """`,z` relabels RECURSIVELY. Repo config is ours to relabel; the org's file store is not."""
    compose = _read("infra/compose/compose.yml")
    assert "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro\n" in compose
    assert "/srv/import/source:ro,z" not in compose
    assert "container_file_t" in _read("docs/runbooks/install-online.md"), (
        "the operator must be told how to label their own tree"
    )


# --- U36: the shipped template must not aim production mail at a dev-only container -------------


def test_env_template_ships_no_smtp_transport() -> None:
    """An empty SMTP_HOST is the app's "no deliverable transport" state.

    ``services/notifications/delivery.py::smtp_transport_configured`` reads it, and the drain then
    suppresses cleanly instead of queueing mail at a host that will never answer.
    """
    env_example = _read(".env.example")
    assert "\nSMTP_HOST=\n" in env_example, "SMTP_HOST must ship empty, not pointed at mailpit"
    assert "\nSMTP_HOST=mailpit" not in env_example


# --- U43/U44: no unbounded wait, no swallowed credential failure --------------------------------


def test_minio_init_gives_up_instead_of_hanging_the_stack(tmp_path: Path) -> None:
    """Behavioural: run the real script against an `mc` that never succeeds.

    api, worker and beat all gate on this one-shot via service_completed_successfully, so an
    unbounded `until` loop turns a wrong S3 credential into a whole-stack hang whose only symptom
    is "waiting for MinIO..." forever. Asserting the variable exists is not enough — the bound has
    to actually fire.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mc = fake_bin / "mc"
    fake_mc.write_text("#!/bin/sh\necho 'mc: Unable to initialize: Access Denied.' >&2\nexit 1\n")
    fake_mc.chmod(0o755)

    started = time.monotonic()
    result = subprocess.run(  # noqa: S603 - fixed repository script with an isolated fake mc
        ["/bin/sh", str(ROOT / "infra/compose/minio/minio-init.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PUBLIC_BASE_URL": "https://qms.example.test",
            "MINIO_WAIT_SECONDS": "4",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    elapsed = time.monotonic() - started

    assert result.returncode != 0, "an unreachable MinIO must fail the one-shot, not hang it"
    assert elapsed < 30, f"the wait was not bounded (took {elapsed:.1f}s)"
    assert "not reachable" in result.stderr
    # The operator needs mc's own words, or the cause is invisible in `docker compose logs`.
    assert "Access Denied" in result.stderr
    assert "S3_ACCESS_KEY" in result.stderr


def test_seed_personas_fails_loudly_when_a_password_cannot_be_set() -> None:
    """A swallowed failure leaves the account enabled but unusable, with no clue at login time."""
    script = _read("scripts/seed-personas.sh")
    assert (
        'kc set-password -r easysynq --username "$u" --new-password "Demo-Password-1" 2>&1'
        in script
    )
    assert '--new-password "Demo-Password-1" >/dev/null 2>&1 || true' not in script


def test_the_minio_wait_budget_is_reachable_from_env() -> None:
    """A hard bound an operator cannot raise turns a slow-but-healthy MinIO into a failed install.

    The service declares an explicit ``environment:`` map and no ``env_file``, so nothing reaches
    it from .env unless it is listed there.
    """
    compose = _read("infra/compose/compose.yml")
    assert "MINIO_WAIT_SECONDS: ${MINIO_WAIT_SECONDS:-120}" in compose


def test_the_mailpit_hint_is_an_uncommentable_assignment() -> None:
    """Prose in a .env file is silently ignored by Compose.

    "Uncomment the mailpit line" has to point at something that becomes a real assignment, or a
    developer follows it and dev mail stays suppressed with no signal.
    """
    env_example = _read(".env.example")
    assert "\n#SMTP_HOST=mailpit" in env_example


# --- U40: the CLI docstring must describe the CLI, not the host wrapper -------------------------


def test_grant_role_docstring_names_the_flag_its_parser_requires() -> None:
    """The module's parser requires --subject; only the host wrapper takes it positionally."""
    module = _read("apps/api/src/easysynq_api/cli/grant_role.py")
    assert 'parser.add_argument("--subject", required=True' in module
    assert "python -m easysynq_api.cli.grant_role --subject" in module
    assert "./scripts/easysynq grant-role" in module


# --- an opt-in RUNTIME proof (every other pin above is a source string) -------------------------


@pytest.mark.skipif(
    os.getenv("EASYSYNQ_IMAGE_PROOF") != "1",
    reason="builds the api image; opt in with EASYSYNQ_IMAGE_PROOF=1 (the release-gate precedent)",
)
def test_the_built_api_image_is_unprivileged_and_starts_offline() -> None:
    """The source pins above cannot see a base-image change that reintroduces root.

    This builds the real image and exercises the two properties that actually matter: it runs as
    uid 10001, and `uv run` — the entry path for migrate/api/worker/beat — works with NO network.
    """
    docker = shutil.which("docker")
    assert docker is not None, "EASYSYNQ_IMAGE_PROOF=1 needs Docker"
    tag = "easysynq-image-proof/api:test"
    build = subprocess.run(  # noqa: S603 - resolved binary, repository Dockerfile
        [docker, "build", "-q", "-f", "apps/api/Dockerfile", "-t", tag, "."],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    try:
        image_id = build.stdout.strip()
        assert image_id.startswith("sha256:") and len(image_id) == 71, build.stdout
        assert all(character in "0123456789abcdef" for character in image_id[7:]), build.stdout
        uid = subprocess.run(  # noqa: S603 - resolved binary, image built above
            [docker, "run", "--rm", tag, "id", "-u"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert uid.stdout.strip() == "10001"

        # The sentinel proves `find` completed; without it an empty listing could mean it never ran.
        privileged = subprocess.run(  # noqa: S603 - resolved binary, image built above
            [
                docker,
                "run",
                "--rm",
                "--network",
                "none",
                tag,
                "sh",
                "-c",
                "find / -xdev -perm /6000 -type f 2>/dev/null; echo FIND_DONE",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert privileged.stdout.split() == ["FIND_DONE"], privileged.stdout

        offline = subprocess.run(  # noqa: S603 - resolved binary, image built above
            [docker, "run", "--rm", "--network", "none", tag, "uv", "run", "alembic", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert offline.returncode == 0, (
            f"uv run needs the network at container start — an air-gapped stack dies on "
            f"`migrate` before anything else runs:\n{offline.stderr}"
        )

        stdlib_bytecode_script = "\n".join(
            (
                "import importlib.util, os, pathlib, sys, sysconfig",
                "assert os.getuid() == 10001",
                "assert sys.flags.optimize == 0 and __debug__ and sys.dont_write_bytecode",
                "stdlib = pathlib.Path(sysconfig.get_path('stdlib')).resolve()",
                "expected = pathlib.Path('/usr/local/lib/python3.12')",
                "assert stdlib == expected and stdlib.is_dir()",
                "excluded = {'site-packages', 'dist-packages'}",
                "sources = []",
                "for parent, directories, filenames in os.walk(stdlib):",
                "    directories[:] = sorted(name for name in directories if name not in excluded)",
                "    sources.extend(",
                "        pathlib.Path(parent) / name for name in sorted(filenames)",
                "        if name.endswith('.py')",
                "    )",
                "assert 0 < len(sources) < 1000",
                "errors = []",
                "for source in sources:",
                "    relative = source.relative_to(stdlib).as_posix()",
                "    cache = pathlib.Path(importlib.util.cache_from_source(str(source)))",
                "    if not cache.is_file():",
                "        errors.append('missing:' + relative)",
                "        continue",
                "    if not os.access(cache, os.R_OK):",
                "        errors.append('unreadable:' + relative)",
                "        continue",
                "    if os.access(cache, os.W_OK):",
                "        errors.append('writable:' + relative)",
                "        continue",
                "    try:",
                "        header = cache.read_bytes()[:16]",
                "        source_hash = importlib.util.source_hash(source.read_bytes())",
                "    except OSError:",
                "        errors.append('unreadable:' + relative)",
                "        continue",
                "    if header[:4] != importlib.util.MAGIC_NUMBER:",
                "        errors.append('magic:' + relative)",
                "    elif len(header) != 16 or int.from_bytes(header[4:8], 'little') != 3:",
                "        errors.append('checked-hash:' + relative)",
                "    elif header[8:16] != source_hash:",
                "        errors.append('source-hash:' + relative)",
                "assert not errors, ','.join(errors[:10])",
                "print(f'STDLIB_BYTECODE_OK files={len(sources)}')",
            )
        )
        stdlib_bytecode = subprocess.run(  # noqa: S603 - same immutable image
            [
                docker,
                "run",
                "--rm",
                "--read-only",
                "--network",
                "none",
                "--user",
                "10001",
                image_id,
                "/app/.venv/bin/python",
                "-I",
                "-B",
                "-c",
                stdlib_bytecode_script,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert stdlib_bytecode.returncode == 0, stdlib_bytecode.stderr
        bytecode_output = stdlib_bytecode.stdout.strip()
        bytecode_prefix = "STDLIB_BYTECODE_OK files="
        assert bytecode_output.startswith(bytecode_prefix), bytecode_output
        bytecode_count = bytecode_output.removeprefix(bytecode_prefix)
        assert bytecode_count.isdecimal() and 0 < int(bytecode_count) < 1000
        print(f"STDLIB_BYTECODE_IMAGE_PROOF_OK files={bytecode_count}")

        codec_script = "\n".join(
            (
                "import hashlib, importlib.util, json, os, sys",
                "from uuid import UUID",
                "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey",
                "from easysynq_api.services.audit import checkpoint_v2",
                "fixture = json.load(sys.stdin)",
                "assert os.getuid() == 10001",
                "assert importlib.util.find_spec('pytest') is None",
                "assert importlib.util.find_spec('minio') is None",
                "assert len(fixture['vectors']) == 5",
                "assert len(fixture['key_admissibility']) == 31",
                "for vector in fixture['vectors']:",
                "    key = Ed25519PublicKey.from_public_bytes(",
                "        bytes.fromhex(vector['public_key_hex'])",
                "    )",
                "    checkpoint = vector['checkpoint']",
                "    result = checkpoint_v2.verify_envelope(",
                "        bytes.fromhex(vector['canonical_envelope_hex']),",
                "        public_key=key,",
                "        org_id=UUID(checkpoint['org_id']),",
                "        stream_id=UUID(checkpoint['stream_id']),",
                "    )",
                "    assert result.canonical_checkpoint == (",
                "        bytes.fromhex(vector['canonical_checkpoint_hex'])",
                "    )",
                "    assert result.canonical_envelope == (",
                "        bytes.fromhex(vector['canonical_envelope_hex'])",
                "    )",
                "    assert result.anchor_hash == vector['anchor_hash']",
                "for case in fixture['key_admissibility']:",
                "    raw = bytes.fromhex(case['public_key_hex'])",
                "    try:",
                "        key = Ed25519PublicKey.from_public_bytes(raw)",
                "    except ValueError:",
                "        assert not case['admissible']",
                "        continue",
                "    try:",
                "        key_id = checkpoint_v2.public_key_id(key)",
                "    except checkpoint_v2.CheckpointV2Error:",
                "        assert not case['admissible']",
                "    else:",
                "        assert case['admissible']",
                "        assert key_id == 'ed25519-sha256:' + hashlib.sha256(raw).hexdigest()",
                "rfc_raw = bytes.fromhex(fixture['rfc8032_public_key_hex'])",
                "checkpoint_v2.public_key_id(Ed25519PublicKey.from_public_bytes(rfc_raw))",
                "identity = fixture['identity_transition']",
                "checkpoint = identity['envelope']['checkpoint']",
                "key = Ed25519PublicKey.from_public_bytes(",
                "    bytes.fromhex(identity['public_key_hex'])",
                ")",
                "try:",
                "    checkpoint_v2.verify_envelope(",
                "        bytes.fromhex(identity['canonical_envelope_hex']),",
                "        public_key=key,",
                "        org_id=UUID(checkpoint['org_id']),",
                "        stream_id=UUID(checkpoint['stream_id']),",
                "    )",
                "except checkpoint_v2.CheckpointV2Error:",
                "    pass",
                "else:",
                "    raise AssertionError('identity transition accepted')",
                "print('AUDIT_CHECKPOINT_V2_CODEC_OK vectors=5 key_cases=31')",
            )
        )
        codec = subprocess.run(  # noqa: S603 - resolved binary, same image built above
            [
                docker,
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                image_id,
                "uv",
                "run",
                "python",
                "-c",
                codec_script,
            ],
            input=_read("apps/api/tests/fixtures/audit_checkpoint_v2_vectors.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        assert codec.returncode == 0, codec.stderr
        assert codec.stdout.strip() == "AUDIT_CHECKPOINT_V2_CODEC_OK vectors=5 key_cases=31"
        print(f"AUDIT_CHECKPOINT_V2_IMAGE_PROOF_OK image_id={image_id} vectors=5 key_cases=31")
        lineage_script = "\n".join(
            (
                "import importlib.util, json, os, sys",
                "from uuid import UUID",
                "from easysynq_api.services.audit import checkpoint_v2, lineage",
                "fixture = json.load(sys.stdin)",
                "assert os.getuid() == 10001",
                "assert importlib.util.find_spec('pytest') is None",
                "assert importlib.util.find_spec('minio') is None",
                "expected = {",
                "    'valid_rotation': ('consistent', set()),",
                "    'old_key_successor': ('failed', {'KEY_EPOCH_VIOLATION'}),",
                "    'unknown_key_successor': ('incomplete', {'UNKNOWN_KEY'}),",
                "    'fork': ('failed', {'LINEAGE_FORK'}),",
                "}",
                "unproved = (",
                "    'bootstrap-contents', 'legacy-bridge-coverage',",
                "    'witness-collection-completeness', 'witness-custody',",
                "    'audit-chain-comparison', 'freshness', 'operational-key-activation',",
                ")",
                "checked = set()",
                "for scenario in fixture['scenarios']:",
                "    name = scenario['name']",
                "    if name not in expected:",
                "        continue",
                "    source = scenario['enrollment']",
                "    bootstrap = source['bootstrap']",
                "    boundary = bootstrap['audit_boundary']",
                "    required = source['required_checkpoint']",
                "    enrollment = lineage.StreamEnrollment(",
                "        UUID(source['org_id']), UUID(source['stream_id']),",
                "        lineage.BootstrapPin(",
                "            bootstrap['commitment_hash'], bootstrap['initial_key_id'],",
                "            bytes.fromhex(bootstrap['initial_public_key_hex']),",
                "            bootstrap['initial_key_epoch'],",
                "            None if boundary is None else lineage.AuditHead(",
                "                boundary['latest_id'], boundary['latest_row_hash']),",
                "        ),",
                "        None if required is None else lineage.RequiredCheckpointPin(",
                "            required['anchor_hash'], required['sequence']),",
                "    )",
                "    observations = tuple(",
                "        lineage.EnvelopeObservation(",
                "            item['source_id'], item['object_key'], item['version_id'],",
                "            bytes.fromhex(fixture['vectors'][item['vector']]['body_hex']),",
                "        ) for item in reversed(scenario['observations'])",
                "    )",
                "    for observation in observations:",
                "        route = checkpoint_v2.inspect_envelope_route(observation.body)",
                "        assert route.org_id == enrollment.org_id",
                "        assert route.stream_id == enrollment.stream_id",
                "    result = lineage.evaluate_lineage(",
                "        enrollment, observations,",
                "        limits=lineage.LineageLimits(4096, 16 * 1024 * 1024, 32),",
                "    )",
                "    status, codes = expected[name]",
                "    assert result.status == status == scenario['expected_status']",
                "    assert {issue.code for issue in result.issues} == codes",
                "    assert codes == set(scenario['expected_codes'])",
                "    assert result.scope == 'supplied-v2-graph'",
                "    assert result.bootstrap_assurance == 'external-pin-only'",
                "    assert result.unproved_checks == unproved",
                "    assert result.required_checkpoint_relation == 'not-provided'",
                "    if status != 'consistent':",
                "        assert result.ordered_envelopes == result.key_history == ()",
                "        assert result.tip_hash is result.tip_sequence is None",
                "    else:",
                "        assert result.tip_sequence == 3",
                "        assert tuple(epoch.key_epoch for epoch in result.key_history) == (0, 1)",
                "    checked.add(name)",
                "assert checked == set(expected)",
                "print('AUDIT_LINEAGE_RUNTIME_OK cases=4')",
            )
        )
        lineage_result = subprocess.run(  # noqa: S603 - resolved binary, same immutable image
            [
                docker,
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                image_id,
                "uv",
                "run",
                "python",
                "-c",
                lineage_script,
            ],
            input=_read("apps/api/tests/fixtures/audit_lineage_vectors.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        assert lineage_result.returncode == 0, lineage_result.stderr
        assert lineage_result.stdout.strip() == "AUDIT_LINEAGE_RUNTIME_OK cases=4"
        print(f"AUDIT_LINEAGE_IMAGE_PROOF_OK image_id={image_id} cases=4")
        bridge_script = """
import importlib.util, json, os, sys
from uuid import UUID
from easysynq_api.services.audit import bootstrap_bridge as bridge, lineage
from easysynq_api.services.audit import bootstrap_bridge_codec, legacy_checkpoint_compat
assert os.getuid() == 10001
assert importlib.util.find_spec('pytest') is None
assert importlib.util.find_spec('minio') is None
for name in sys.modules:
    assert not name.startswith(('sqlalchemy', 'easysynq_api.settings'))
    assert name not in (
        'easysynq_api.core.config', 'easysynq_api.services.audit.checkpoint',
        'easysynq_api.services.audit.trust', 'easysynq_api.services.audit.sink',
        'easysynq_api.services.audit.external',
    )
fixture = json.load(sys.stdin)
expected = {
    'consistent': ('consistent', set()),
    'unlisted_old': ('failed', {'UNLISTED_AUTHENTIC_LEGACY'}),
    'missing_page': ('incomplete', {'PAGE_MISSING'}),
    'same_locator_transport_conflict': (
        'failed', {'IMMUTABLE_LOCATOR_CONFLICT', 'LEGACY_BODY_COMMITMENT_MISMATCH'}),
    'offset_compatible': ('consistent', set()),
}
unproved = (
    'operational-legacy-history-completeness', 'witness-collection-completeness',
    'witness-custody', 'audit-chain-comparison', 'v2-lineage-consistency',
    'freshness', 'rollback-memory-continuity', 'operational-key-activation',
)
checked = set()
for scenario in fixture['scenarios']:
    name = scenario['name']
    if name not in expected:
        continue
    package = fixture['packages'][scenario['package']]
    source = package['enrollment']
    pin = source['bootstrap']
    head = pin['audit_boundary']
    stream = lineage.StreamEnrollment(
        UUID(source['org_id']), UUID(source['stream_id']),
        lineage.BootstrapPin(
            pin['commitment_hash'], pin['initial_key_id'],
            bytes.fromhex(pin['initial_public_key_hex']), pin['initial_key_epoch'],
            lineage.AuditHead(head['latest_id'], head['latest_row_hash']),
        ), None,
    )
    enrollment = bridge.BridgeEnrollment(
        stream,
        tuple(bridge.BridgeWitnessPin(UUID(w['witness_id']), w['namespace_hash'])
              for w in source['witnesses']),
        tuple(bridge.LegacyPublicMaterial(k['key_id'], bytes.fromhex(k['public_key_hex']))
              for k in source['legacy_keys']),
    )
    pages = tuple(bridge.BridgePageObservation(bytes.fromhex(p['body_hex']))
                  for index, p in enumerate(package['pages'])
                  if index not in scenario.get('omit_pages', []))
    observations = tuple(bridge.LegacyBodyObservation(
        UUID(item['witness_id']), item['object_key'], item['version_id'],
        bytes.fromhex(fixture['legacy_vectors'][item['vector']]['body_hex']),
    ) for item in package['observations'] + scenario.get('append_observations', []))
    result = bridge.evaluate_bootstrap_bridge(
        enrollment, bytes.fromhex(package['root_body_hex']), pages, observations,
        limits=bridge.BridgeLimits(4096, 16 * 1024 * 1024, 32),
    )
    status, codes = expected[name]
    assert result.status == status == scenario['expected_status']
    assert {issue.code for issue in result.issues} == codes == set(scenario['expected_codes'])
    assert result.scope == 'supplied-legacy-bootstrap-package'
    assert result.unproved_checks == unproved
    if status == 'consistent':
        assert result.usable_bootstrap_pin is stream.bootstrap
        assert len(result.witness_summaries) == 2
    else:
        assert result.usable_bootstrap_pin is None
        assert result.established_checks == result.witness_summaries == ()
    if name == 'same_locator_transport_conflict':
        assert (result.failed_issues, result.incomplete_issues) == (2, 0)
    if name == 'consistent':
        usable = result.usable_bootstrap_pin
        v2_stream = lineage.StreamEnrollment(stream.org_id, stream.stream_id, usable, None)
        v2 = tuple(lineage.EnvelopeObservation(
            'synthetic', 'v2-' + str(index), 'version', bytes.fromhex(vector['body_hex']),
        ) for index, vector in enumerate(fixture['v2_composition']))
        graph = lineage.evaluate_lineage(
            v2_stream, v2, limits=lineage.LineageLimits(4096, 16 * 1024 * 1024, 32),
        )
        assert graph.status == 'consistent' and graph.tip_sequence == 3
        assert graph.scope == 'supplied-v2-graph'
        assert graph.bootstrap_assurance == 'external-pin-only'
        assert graph.unproved_checks == (
            'bootstrap-contents', 'legacy-bridge-coverage',
            'witness-collection-completeness', 'witness-custody',
            'audit-chain-comparison', 'freshness', 'operational-key-activation',
        )
        assert tuple(key.key_epoch for key in graph.key_history) == (0, 1)
        checked.add('composition')
    checked.add(name)
assert checked == set(expected) | {'composition'}
print('AUDIT_BOOTSTRAP_BRIDGE_RUNTIME_OK cases=6')
"""
        bridge_result = subprocess.run(  # noqa: S603 - same immutable image, literal script
            [
                docker,
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--user",
                "10001",
                image_id,
                "uv",
                "run",
                "python",
                "-c",
                bridge_script,
            ],
            input=_read("apps/api/tests/fixtures/audit_bootstrap_bridge_vectors.json"),
            capture_output=True,
            text=True,
            check=False,
        )
        assert bridge_result.returncode == 0, bridge_result.stderr
        assert bridge_result.stdout.strip() == "AUDIT_BOOTSTRAP_BRIDGE_RUNTIME_OK cases=6"
        print(f"AUDIT_BOOTSTRAP_BRIDGE_IMAGE_PROOF_OK image_id={image_id} cases=6")
    finally:
        subprocess.run(  # noqa: S603 - resolved binary, image built above
            [docker, "rmi", "-f", tag], capture_output=True, check=False
        )


# --- the non-root worker must not silently shrink an import baseline --------------------------


def test_an_unreadable_source_directory_is_recorded_not_dropped(tmp_path: Path) -> None:
    """``os.walk`` ignores a directory it cannot list, dropping its WHOLE subtree silently.

    That became reachable when the worker stopped running as root (U33): it reads the import mount
    as uid 10001, so a directory owned by someone else with mode 0750 is invisible. Without this,
    a run completes, reports a smaller file count, and hands back an incomplete controlled-document
    baseline with no error anywhere — the worst possible failure mode for a QMS import.
    """
    from easysynq_api.services.ingestion.source import FilesystemSourceProvider

    root = tmp_path / "src"
    (root / "readable").mkdir(parents=True)
    (root / "readable" / "a.txt").write_text("a")
    blocked = root / "blocked"
    blocked.mkdir()
    (blocked / "hidden.txt").write_text("secret")
    blocked.chmod(0o000)
    try:
        metas = [m for chunk in FilesystemSourceProvider(root).walk(batch_size=10) for m in chunk]
    finally:
        blocked.chmod(0o755)  # so tmp_path cleanup can remove it

    assert "readable/a.txt" in {m.rel_path for m in metas}
    unreadable = [m for m in metas if m.error and m.error.startswith("unreadable_dir:")]
    assert unreadable, (
        f"the unreadable directory was silently dropped: {[m.rel_path for m in metas]}"
    )
    assert unreadable[0].rel_path == "blocked"
