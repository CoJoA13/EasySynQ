"""Batch 13 deployment invariants.

These source-level checks complement ``docker compose config --quiet`` in CI. They pin the security
boundaries that are easy to accidentally erase while editing an overlay: production never publishes
plaintext MinIO, every browser URL is supplied together, and Keycloak's live store is PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from fnmatch import fnmatchcase
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
BROWSER_ONLY_CONTEXT_ROOTS = frozenset(
    {
        "e2e",
        "e2e-live",
        "playwright.config.ts",
        "playwright.live.config.ts",
        "tsconfig.browser.json",
        ".playwright-dist",
        "playwright-report",
        "test-results",
    }
)


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _markdown_section(content: str, heading_text: str) -> str:
    expected_heading = re.compile(rf"^(?P<marks>#{{1,6}})\s+{re.escape(heading_text)}\s*$")
    any_heading = re.compile(r"^(?P<marks>#{1,6})\s+")
    heading_level: int | None = None
    in_fence = False
    retained: list[str] = []

    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        is_fence = stripped.startswith(("```", "~~~"))
        if heading_level is None:
            if not in_fence and (heading := expected_heading.match(stripped)):
                heading_level = len(heading["marks"])
            if is_fence:
                in_fence = not in_fence
            continue

        next_heading = None if in_fence else any_heading.match(stripped)
        if next_heading is not None and len(next_heading["marks"]) <= heading_level:
            break
        retained.append(line)
        if is_fence:
            in_fence = not in_fence

    assert heading_level is not None, f"missing Markdown section: {heading_text}"
    return "".join(retained)


def _assert_fragments_in_order(content: str, fragments: tuple[str, ...]) -> None:
    content = re.sub(r"\s+", " ", content)
    cursor = 0
    for fragment in fragments:
        fragment = re.sub(r"\s+", " ", fragment)
        position = content.find(fragment, cursor)
        assert position >= 0, f"missing or out-of-order instruction: {fragment}"
        cursor = position + len(fragment)


def _assert_supported_first_admin_sequence(content: str, setup_url: str) -> None:
    normalized = re.sub(r"\s+", " ", content)
    normalized_lower = re.sub(r"\s+", " ", content).lower()
    assert "just demo-user" not in normalized_lower
    assert "demo-password-1" not in normalized_lower
    setup_instruction = f"Open {setup_url} without signing in"
    setup_position = normalized.find(setup_instruction)
    assert setup_position >= 0, f"missing public setup instruction: {setup_instruction}"
    before_setup = normalized[:setup_position]
    pre_setup_sign_in = re.compile(r"\b(?:sign[\s-]+in|log[\s-]+in|login)\b", re.IGNORECASE)
    assert pre_setup_sign_in.search(before_setup) is None, (
        "supported first-admin section must not mention sign-in before public /setup"
    )
    _assert_fragments_in_order(
        content,
        (
            "mint-bootstrap",
            setup_instruction,
            "create the first administrator profile",
            "copy the shown-once temporary password",
            "acknowledge the active credential generation",
            "sign in",
            "change the temporary password",
        ),
    )


_SETUP_SHEET_HEREDOC = re.compile(
    r'^\s*cat >"\$SETUP_FILE" <<(?P<delimiter>[A-Z][A-Z0-9_]*)\n'
    r"(?P<body>.*?)\n(?P=delimiter)\n",
    re.MULTILINE | re.DOTALL,
)
_SETUP_SHEET_LINES = (
    "Application URL: https://${HOSTNAME_DEFAULT}/setup",
    "Your one-time setup secret (EasySynQ):",
    "${secret}",
    "Single-use, 24h. Re-mint: easysynq-status --remint",
    "1. Then create the first administrator in /setup with the setup secret.",
    "2. Save the shown-once temporary password and continue to sign in.",
    "3. Then sign in, replace the password, and complete the remaining setup gates.",
)
_POST_READY_HANDOFF_SHA256 = "d4358d2540b30f050cade40d6f63cb1e78e892665f099e5e9f11d89b2d79423f"
_BREAK_GLASS_OR_ORPHAN_RECOVERY_SECTION = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:break[- ]glass\b|orphan(?:ed)?\s+(?:adoption|recovery)\b)",
    re.IGNORECASE,
)
_NEUTRAL_INLINE_CODE_IDENTIFIER = re.compile(
    r"`(?:user\.create|user\.update|permission\.grant|/users/provision)`"
)
_SAFE_NEGATIVE_NORMAL_FLOW_PHRASES = (
    re.compile(
        r"\b(?:do\s+not|does\s+not|don't|never)\s+(?:create|add|make|provision)\b[^.\n]{0,120}"
        r"\bkeycloak\b[^.\n]{0,80}\b(?:user|identity|account)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:do\s+)?not\s+(?:open|visit)\s+keycloak\b(?:\s+(?:or|and)\s+"
        r"(?:copy|paste|enter|supply|provide|handle|ask\s+for)\s+(?:an?\s+)?"
        r"(?:identity\s+)?subjects?\b)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:do\s+not|don't|never)\s+(?:copy|paste|enter|supply|provide|handle|ask\s+for)"
        r"\b[^.\n]{0,120}\b(?:identity\s+)?subjects?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\boperator\s+does\s+not\s+create\b[^.\n]{0,120}\bkeycloak\b"
        r"[^.\n]{0,80}\b(?:or|and)\s+copy\b[^.\n]{0,80}\bsubjects?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:the\s+)?normal(?:\s+install)?\s+path\s+never\s+opens?\s+keycloak\b"
        r"(?:,?\s*(?:handles?|copies?|pastes?|asks?\s+for)\s+(?:an?\s+)?"
        r"(?:identity\s+)?subjects?\b)?",
        re.IGNORECASE,
    ),
)
_PATH_VALUED_EXECUTABLE_AT_COMMAND_POSITION = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+)\s+)*[\"']?"
    r"(?P<path>[^\s\"']*/[^\s\"']+)"
)
_PATH_VALUED_PYTHON_INTERPRETER = re.compile(r"(?:^|/)python(?:3)?$")
_ALLOWED_POST_READY_PYTHON = re.compile(
    r"(?:\bpython(?:3)?\b|/(?:[^/\s\"']+/)*python(?:3)?[\"']?)\s+-m\s+"
    r"easysynq_api\.cli\.(?:setup\s+mint-bootstrap|keycloak_redirect)\b"
)
_NORMAL_FLOW_KEYCLOAK_CREATION = re.compile(
    r"(?<![-\w])(?:create|add|make|provision)\b[^.\n]{0,120}\b(?:"
    r"keycloak(?![-\w])[^.\n]{0,80}\b(?:user|identity|account)\b|"
    r"(?:user|identity|account)\b[^.\n]{0,80}\b(?:in|on|via)\s+keycloak(?![-\w])|"
    r"(?:intended|first)\b[^.\n]{0,60}\b(?:administrator|admin)\b"
    r"[^.\n]{0,60}\b(?:identity|account)\b"
    r")",
    re.IGNORECASE,
)
_NORMAL_FLOW_SUBJECT_HANDOFF = re.compile(
    r"\b(?:copy|paste|enter|supply|provide|handle)\b[^.\n]{0,120}"
    r"\b(?:keycloak\s+)?(?:subject|sub)\b",
    re.IGNORECASE,
)


def _extract_setup_sheet(provisioner: str) -> str:
    matches = list(_SETUP_SHEET_HEREDOC.finditer(provisioner))
    assert len(matches) == 1, "provisioner must write exactly one EASYSYNQ-SETUP.txt heredoc"
    return matches[0]["body"]


def _assert_setup_sheet_is_secret_only(setup_sheet: str) -> None:
    """Keep the hand-off file a narrowly-scoped browser bootstrap secret, never a login record."""
    nonempty_lines = tuple(line.strip() for line in setup_sheet.splitlines() if line.strip())
    assert nonempty_lines == _SETUP_SHEET_LINES, (
        "EASYSYNQ-SETUP.txt may contain only the app URL, one-time setup secret, expiry/remint, "
        "and browser setup steps"
    )

    variable_names = {
        braced or bare
        for braced, bare in re.findall(
            r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
            setup_sheet,
        )
    }
    assert variable_names == {"HOSTNAME_DEFAULT", "secret"}, (
        "EASYSYNQ-SETUP.txt may interpolate only the host URL and one-time setup secret"
    )
    assert "$(" not in setup_sheet, "EASYSYNQ-SETUP.txt must not execute shell substitutions"


def _post_ready_handoff_segment(provisioner: str) -> str:
    ready_marker = '[ "$ok" -eq 1 ] || { log "readyz never went green"; exit 1; }'
    return provisioner[provisioner.index(ready_marker) + len(ready_marker) :]


def _assert_approved_post_ready_handoff(post_ready_handoff: str) -> None:
    """Authoritative review lock; semantic checks below are diagnostic defense-in-depth only."""
    normalized_handoff = post_ready_handoff.replace("\r\n", "\n").replace("\r", "\n")
    actual_sha256 = hashlib.sha256(normalized_handoff.encode()).hexdigest()
    assert actual_sha256 == _POST_READY_HANDOFF_SHA256, (
        "post-ready appliance handoff changed; reviewer must explicitly update the "
        "approved SHA-256: "
        f"{actual_sha256}"
    )


def _post_ready_provision_actions(provisioner: str) -> str:
    return _SETUP_SHEET_HEREDOC.sub("", _post_ready_handoff_segment(provisioner))


def _assert_no_human_identity_actions(post_ready_actions: str) -> None:
    """Diagnostic defense-in-depth; the raw post-ready handoff fingerprint is authoritative."""
    normalized_actions = re.sub(r"\\[ \t]*\r?\n[ \t]*", " ", post_ready_actions)
    forbidden_mechanisms = (
        r"\bkcadm(?:\.sh)?\b[^\n]*\bcreate\s+users?\b",
        r"\bkcadm(?:\.sh)?\b[^\n]*\b(?:set|reset)-password\b",
        r"\b(?:useradd|adduser|usermod|passwd|chpasswd)\b",
        r"\b(?:create|new|add)[-_](?:keycloak[-_])?user\b",
        r"\b(?:bash|sh|python3?)\b[^\n]*(?:create|new)[-_](?:keycloak[-_])?user(?:\.sh)?\b",
        r"\b(?:curl|http|wget)\b[^\n]*(?:/admin/realms/[^\s]*/users|/users\b)",
        r"[\"'](?:username|password|credentials?)[\"']\s*:",
        r"\b(?:--username|--new-password|--password)\b",
        r"\b(?:username|user_name|login_name)\s*=\s*(?!\$?\{?KEYCLOAK_ADMIN\b)",
        r"(?m)^\s*(?!KEYCLOAK_ADMIN(?:_PASSWORD)?\b)[A-Z_]*"
        r"(?:USERNAME|PASSWORD|CREDENTIAL)[A-Z_]*\s*=",
    )
    for pattern in forbidden_mechanisms:
        assert not re.search(pattern, normalized_actions, re.IGNORECASE), (
            "post-ready appliance actions must not create a human identity or set its credential: "
            f"{pattern}"
        )

    for statement in re.split(r"(?:\n|&&|\|\||;)", normalized_actions):
        if not statement.strip():
            continue
        assert not re.search(
            r"(?:^|\s)(?:/bin/)?(?:bash|sh|source)\b|(?:^|\s)\.\s+|"
            r"(?:^|[\s\"'])(?:\$\{?APP_DIR\}?/)?scripts/",
            statement,
        ), f"post-ready appliance actions must not run an unapproved helper: {statement}"
        path_match = _PATH_VALUED_EXECUTABLE_AT_COMMAND_POSITION.search(statement)
        if path_match:
            executable_path = path_match["path"].rstrip("\"'")
            assert _PATH_VALUED_PYTHON_INTERPRETER.search(executable_path) and (
                _ALLOWED_POST_READY_PYTHON.search(statement)
            ), f"post-ready appliance actions must not run an unapproved helper: {statement}"
        for _ in re.finditer(r"\bpython(?:3)?\b", statement):
            assert _ALLOWED_POST_READY_PYTHON.search(statement), (
                f"unapproved Python helper in post-ready appliance actions: {statement}"
            )


def _normal_flow_doc_text(content: str) -> str:
    retained: list[str] = []
    exception_heading_level: int | None = None
    for line in content.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            heading_level = len(heading[1])
            if exception_heading_level is not None and heading_level <= exception_heading_level:
                exception_heading_level = None
            if _BREAK_GLASS_OR_ORPHAN_RECOVERY_SECTION.search(heading[2]):
                exception_heading_level = heading_level
        if exception_heading_level is not None:
            continue
        retained.append(line)
    return "\n".join(retained)


def _strip_tightly_bound_safe_negatives(instruction: str) -> str:
    for safe_phrase in _SAFE_NEGATIVE_NORMAL_FLOW_PHRASES:
        instruction = safe_phrase.sub("", instruction)
    return instruction


def _assert_no_retired_normal_flow_docs(current_docs: dict[str, str]) -> None:
    for path, content in current_docs.items():
        normal_flow = _normal_flow_doc_text(content)
        assert "/setup/bootstrap" not in normal_flow.lower(), (
            f"{path} documents the retired normal-flow bootstrap endpoint"
        )
        for sentence in re.split(r"(?<=[.!?])\s+|\n", normal_flow):
            instruction = _NEUTRAL_INLINE_CODE_IDENTIFIER.sub("", sentence)
            instruction = instruction.replace("**", "")
            instruction = _strip_tightly_bound_safe_negatives(instruction)
            assert not _NORMAL_FLOW_KEYCLOAK_CREATION.search(instruction), (
                f"{path} directs normal installation through Keycloak-user creation"
            )
            assert not _NORMAL_FLOW_SUBJECT_HANDOFF.search(instruction), (
                f"{path} directs normal installation to copy or handle an identity subject"
            )


def _assert_no_protected_dockerignore_reinclusions(
    dockerignore: list[str], protected_roots: frozenset[str]
) -> None:
    for raw_pattern in dockerignore:
        pattern = raw_pattern.strip()
        if not pattern.startswith("!"):
            continue

        reinclusion = pattern.removeprefix("!").lstrip("/").rstrip("/")
        assert ".." not in reinclusion.split("/"), (
            f"parent-relative Docker reinclusion is not allowed beside protected roots: {pattern}"
        )
        while reinclusion.startswith("./"):
            reinclusion = reinclusion[2:]
        assert not any(
            reinclusion == root or reinclusion.startswith(f"{root}/") for root in protected_roots
        ), f"Docker reinclusion reaches a protected browser root: {pattern}"

        if any(character in reinclusion for character in "*?["):
            first_component, separator, _ = reinclusion.partition("/")
            assert separator and not any(
                fnmatchcase(root, first_component) for root in protected_roots
            ), f"Docker wildcard reinclusion can reach a protected browser root: {pattern}"


def test_minio_host_publish_is_dev_only_and_loopback_bound() -> None:
    sizing = _read("infra/compose/compose.s.yml")
    dev = _read("infra/compose/compose.dev.yml")
    production = _read("infra/compose/compose.production.yml")

    assert "S3_PORT" not in sizing
    assert "127.0.0.1:${S3_PORT:-9000}:9000" in dev
    assert "ports: !reset []" in production
    assert '"9443:9443"' in production
    assert ":9000:9000" not in production


def test_minio_init_receives_sink_credentials_and_retention() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    for key in (
        "AUDIT_SINK_ACCESS_KEY",
        "AUDIT_SINK_SECRET_KEY",
        "AUDIT_SINK_READ_ACCESS_KEY",
        "AUDIT_SINK_READ_SECRET_KEY",
        "WORM_RETENTION",
    ):
        assert f"{key}: ${{{key}:-" in compose
    assert "WORM_RETENTION=30d" in template


def test_minio_init_versions_staging_without_unsupported_bucket_cors() -> None:
    init = _read("infra/compose/minio/minio-init.sh")
    compose = _read("infra/compose/compose.yml")

    assert "mc version enable local/staging" in init
    assert "mc version enable local/import-staging" in init
    assert "mc cors set" not in init
    assert "<CORSConfiguration>" not in init
    assert "MINIO_API_CORS_ALLOW_ORIGIN: ${PUBLIC_BASE_URL:-http://localhost}" in compose
    assert "--purge-on-delete" not in init
    assert "lifecycle" not in init.lower()


def _run_minio_init(tmp_path: Path, origin: str) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mc = fake_bin / "mc"
    fake_mc.write_text("#!/bin/sh\nexit 0\n")
    fake_mc.chmod(0o755)
    return subprocess.run(  # noqa: S603 - fixed script with an isolated fake mc
        ["/bin/sh", str(ROOT / "infra/compose/minio/minio-init.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "PUBLIC_BASE_URL": origin,
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://one.test,http://two.test",
        "http://*",
        "http://bad test",
        "http://bad\ntest",
        "http://bad&test",
        "http://bad<test",
        'http://bad"test',
        "ftp://test",
        "http:///path",
        "http://test/path",
        "http://:",
        "http://host:",
        "https://host:not-a-port",
        "http://host:0",
        "http://host:65536",
        "http://host:999999999999999999999",
        "http://bad_host",
        "http://-host.test",
        "http://host-.test",
        "http://host..test",
        "http://.host",
        "http://host.",
        f"http://{'a' * 64}.test",
        "http://" + ".".join(["aa"] * 85),
        "http://::1",
        "http://[::1",
        "http://::1]",
        "http://[]",
        "http://[:::1]",
        "http://[2001:db8::1::1]",
        "http://[2001:db8:0:0:0:0:0:0:1]",
        "http://[1:2:3]",
        "http://[12345::1]",
        "http://[gggg::1]",
        "http://[::ffff:192.0.2.999]",
        "http://[::1]:",
        "http://[::1]:not-a-port",
        "http://[::1]:0",
        "http://[::1]:65536",
        "http://[::1]extra",
    ],
)
def test_minio_init_rejects_non_exact_browser_origins(tmp_path: Path, origin: str) -> None:
    result = _run_minio_init(tmp_path, origin)

    assert result.returncode != 0
    assert "one exact HTTP(S) origin" in result.stderr


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost",
        "http://localhost:9000",
        "https://qms.example.com",
        "https://QMS.example.com:9443",
        "http://[::1]",
        "http://[::1]:9000",
        "https://[2001:db8::1]",
        "https://[2001:DB8:0:1::abcd]:443",
        "http://[::ffff:192.0.2.1]",
    ],
)
def test_minio_init_accepts_valid_browser_origins(tmp_path: Path, origin: str) -> None:
    result = _run_minio_init(tmp_path, origin)

    assert result.returncode == 0, result.stderr


def test_staging_initializer_gates_every_promotion_capable_service() -> None:
    compose = yaml.safe_load(_read("infra/compose/compose.yml"))

    for service_name in ("api", "worker", "beat"):
        depends_on = compose["services"][service_name]["depends_on"]
        assert depends_on["minio-init"] == {"condition": "service_completed_successfully"}


def test_compose_passes_safe_cors_origin_and_default_off_rollback_guard() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    assert "PUBLIC_BASE_URL: ${PUBLIC_BASE_URL:-http://localhost}" in compose
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY: ${EASYSYNQ_COMPATIBILITY_READ_ONLY:-0}" in compose
    assert "EASYSYNQ_COMPATIBILITY_READ_ONLY=0" in template


def test_compatibility_rollback_guard_precedes_every_api_proxy_handle() -> None:
    caddy = _read("infra/compose/caddy/Caddyfile")
    matcher = """@compatibility_rollback_write {
		path /api/*
		method POST PUT PATCH DELETE
		vars {env.EASYSYNQ_COMPATIBILITY_READ_ONLY} 1
	}"""

    assert matcher in caddy
    assert 'respond "Write operations are disabled during compatibility rollback." 503' in caddy
    guard_index = caddy.index("handle @compatibility_rollback_write")
    assert guard_index < caddy.index("handle @sse")
    assert guard_index < caddy.index("handle @public_html")
    assert guard_index < caddy.index("handle @api")


def test_import_source_default_resolves_to_repository_root() -> None:
    compose = _read("infra/compose/compose.yml")
    template = _read(".env.example")

    assert "IMPORT_SOURCE_PATH=../../.import-source" in template
    assert "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro" in compose
    assert "${IMPORT_SOURCE_PATH:-./.import-source}" not in compose


def test_web_image_uses_lockfile_and_excludes_host_artifacts() -> None:
    dockerfile = _read("apps/web/Dockerfile")
    dockerignore = _read("apps/web/.dockerignore").splitlines()

    assert "COPY package.json package-lock.json ./" in dockerfile
    assert "RUN npm ci" in dockerfile
    assert "npm ci ||" not in dockerfile
    assert {"node_modules", "dist", "coverage", ".vite"}.issubset(dockerignore)


def test_web_image_excludes_browser_harness_and_removes_playwright_from_runtime() -> None:
    dockerfile = _read("apps/web/Dockerfile")
    dockerignore = _read("apps/web/.dockerignore").splitlines()

    # These exact app-root patterns are interpreted by Docker before the broad `COPY . .`.
    assert BROWSER_ONLY_CONTEXT_ROOTS.issubset(dockerignore)
    _assert_no_protected_dockerignore_reinclusions(dockerignore, BROWSER_ONLY_CONTEXT_ROOTS)
    assert "COPY . ." in dockerfile

    install_build_cleanup = """RUN npm ci \\
    && npm run build \\
    && npm uninstall --no-save @playwright/test \\
    && npm cache clean --force"""
    assert install_build_cleanup in dockerfile
    assert dockerfile.index("COPY . .") < dockerfile.index(install_build_cleanup)
    assert dockerfile.count("RUN npm ci") == 1
    assert (
        'CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "5173"]' in dockerfile
    )


def test_web_image_invariant_rejects_exact_descendant_and_wildcard_reinclusions() -> None:
    for root in BROWSER_ONLY_CONTEXT_ROOTS:
        for reinclusion in (
            f"!{root}",
            f"!{root}/nested/probe",
            f"!**/{root}",
            f"!{root[:4]}*/nested/probe",
        ):
            with pytest.raises(AssertionError):
                _assert_no_protected_dockerignore_reinclusions(
                    [*BROWSER_ONLY_CONTEXT_ROOTS, reinclusion], BROWSER_ONLY_CONTEXT_ROOTS
                )

    for reinclusion in ("!./e2e", "!././e2e/nested", "!./playwright.config.ts"):
        with pytest.raises(AssertionError):
            _assert_no_protected_dockerignore_reinclusions(
                [*BROWSER_ONLY_CONTEXT_ROOTS, reinclusion], BROWSER_ONLY_CONTEXT_ROOTS
            )

    _assert_no_protected_dockerignore_reinclusions(
        [*BROWSER_ONLY_CONTEXT_ROOTS, "!docs/**/*.md"], BROWSER_ONLY_CONTEXT_ROOTS
    )


def test_first_admin_live_harness_owns_only_its_validated_stack_and_env() -> None:
    harness_path = ROOT / "scripts/test-first-admin-keycloak.sh"
    assert harness_path.exists(), "the live first-administrator runner must be repository-owned"
    harness = harness_path.read_text(encoding="utf-8")

    assert 'ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"' in harness
    assert 'ENV_FILE="$ROOT/.env"' in harness
    assert 'if [ -e "$ENV_FILE" ] || [ -L "$ENV_FILE" ]; then' in harness
    assert "live acceptance refuses an existing .env" in harness
    assert 'PROJECT="easysynq-first-admin-$(openssl rand -hex 6)"' in harness
    assert "^easysynq-first-admin-[a-z0-9]+$" in harness

    compose_definition = harness[
        harness.index("COMPOSE=(") : harness.index(")", harness.index("COMPOSE=("))
    ]
    assert "docker compose" in compose_definition
    assert '-p "$PROJECT"' in compose_definition
    assert '--env-file "$ENV_FILE"' in compose_definition
    assert "infra/compose/compose.yml" in compose_definition
    assert "infra/compose/compose.s.yml" in compose_definition
    assert "infra/compose/compose.dev.yml" in compose_definition
    assert harness.count("docker compose") == 1, (
        "every Compose operation must use the one project-scoped argv"
    )

    trap_offset = harness.index("trap cleanup EXIT INT TERM")
    startup_offset = harness.index('"${COMPOSE[@]}" up -d --build')
    assert trap_offset < startup_offset
    cleanup = harness[harness.index("cleanup() {") : trap_offset]
    assert 'validate_project "$PROJECT"' in cleanup
    assert '[ "$stack_started" -eq 1 ]' in cleanup
    assert '"${COMPOSE[@]}" down -v --remove-orphans --rmi local' in cleanup
    assert cleanup.count('"${COMPOSE[@]}" down') == 1
    assert "docker image" not in cleanup
    assert "docker rmi" not in cleanup
    assert '"${COMPOSE[@]}" logs --no-color --tail 200 api keycloak proxy' in cleanup
    assert '[ "$env_created" -eq 1 ]' in cleanup
    assert '[ "$ENV_FILE" = "$ROOT/.env" ]' in cleanup
    assert 'unlink -- "$ENV_FILE"' in cleanup
    assert 'elif [ -e "$ENV_FILE" ] || [ -L "$ENV_FILE" ]; then' in cleanup

    assert 'EASYSYNQ_ENV_ONLY=1 "$ROOT/scripts/install.sh" s' in harness
    assert 'stack_started=1\n"${COMPOSE[@]}" up -d --build' in harness
    assert 'curl -fsS "$APP_ORIGIN/readyz"' in harness
    assert "easysynq_api.cli.keycloak_redirect" in harness
    assert "easysynq_api.cli.setup import mint_bootstrap" in harness
    assert "npm --prefix apps/web run test:first-admin-live" in harness
    for variable in (
        "EASYSYNQ_LIVE_BASE_URL",
        "EASYSYNQ_LIVE_SETUP_SECRET",
        "EASYSYNQ_LIVE_USERNAME",
        "EASYSYNQ_LIVE_NEW_PASSWORD",
    ):
        assert f'{variable}="${{' in harness

    assert "just down" not in harness
    assert "rm -" not in harness
    assert "rm " not in harness
    assert not re.search(r"\b(?:rm|unlink)\b[^\n]*[?*\[]", harness)
    assert "nohup" not in harness
    assert "set -x" not in harness


def test_first_admin_live_playwright_is_secret_safe_and_single_worker() -> None:
    config_path = ROOT / "apps/web/playwright.live.config.ts"
    spec_path = ROOT / "apps/web/e2e-live/first-admin.spec.ts"
    assert config_path.exists(), "the live Playwright config must be separate from synthetic tests"
    assert spec_path.exists(), "the real first-administrator flow must have a narrow live spec"
    config = config_path.read_text(encoding="utf-8")
    spec = spec_path.read_text(encoding="utf-8")

    assert "process.env.EASYSYNQ_LIVE_BASE_URL" in config
    assert 'throw new Error("EASYSYNQ_LIVE_BASE_URL is required")' in config
    assert 'testDir: "./e2e-live"' in config
    assert "workers: 1" in config
    assert "retries: 0" in config
    assert 'name: "chromium"' in config
    assert 'browserName: "chromium"' in config
    assert 'trace: "off"' in config
    assert 'screenshot: "off"' in config
    assert 'video: "off"' in config
    assert "webServer:" not in config
    assert "firefox" not in config.lower()
    assert "webkit" not in config.lower()

    assert 'test("first administrator completes the required Keycloak password update"' in spec
    assert "const value = process.env[name]" in spec
    for variable in (
        "EASYSYNQ_LIVE_BASE_URL",
        "EASYSYNQ_LIVE_SETUP_SECRET",
        "EASYSYNQ_LIVE_USERNAME",
        "EASYSYNQ_LIVE_NEW_PASSWORD",
    ):
        assert f'requiredEnvironment("{variable}")' in spec
    assert "browser.newContext()" in spec
    assert "getByLabel(/^Setup secret/)" in spec
    assert 'getByRole("heading"' in spec
    assert 'name: "Temporary password — shown once"' in spec
    assert "credential_receipt: string" in spec
    assert "const credentialReceipt = provisioned.credential_receipt" in spec
    assert "credential_receipt: credentialReceipt" in spec
    assert "async function installLiveOriginGuard" in spec
    assert spec.count("await installLiveOriginGuard(") == 2
    assert "url.origin !== liveOrigin" in spec
    assert "live acceptance blocked unexpected external request" in spec
    assert "const sensitiveValues = [" in spec
    assert spec.count("await expectSensitiveValuesNotRetained(") == 3
    assert 'input[name="username"]' in spec
    assert 'input[name="password-new"]' in spec
    assert spec.count('getByRole("button", { name: "Sign In", exact: true })') == 2
    assert spec.count('getByRole("button", { name: "Submit", exact: true })') == 1
    assert 'input[type="submit"]' not in spec
    assert 'getByLabel("Legal name", { exact: true })' in spec
    assert 'input[name="password"]' in spec
    assert spec.count('getByText("Invalid username or password.", { exact: true })') == 1
    assert "#input-error" not in spec
    assert "[role='alert']" not in spec
    assert ".alert-error" not in spec
    assert "console." not in spec
    assert "testInfo.attach" not in spec
    assert "screenshot(" not in spec
    assert "tracing." not in spec
    assert not re.search(r"(?:localStorage|sessionStorage)\.setItem\([^\n]*credentialReceipt", spec)
    assert not re.search(r"(?:goto|waitForURL)\([^\n]*credentialReceipt", spec)


def test_first_admin_live_package_and_typecheck_boundaries_are_isolated() -> None:
    package = json.loads(_read("apps/web/package.json"))
    vite = _read("apps/web/vite.config.ts")
    browser_tsconfig = json.loads(_read("apps/web/tsconfig.browser.json"))
    dockerignore = _read("apps/web/.dockerignore").splitlines()

    assert package["scripts"]["test:first-admin-live"] == (
        "playwright test --config playwright.live.config.ts"
    )
    assert '"e2e-live/**"' in vite
    assert "e2e-live" in browser_tsconfig["include"]
    assert "playwright.live.config.ts" in browser_tsconfig["include"]
    assert {"e2e-live", "playwright.live.config.ts"}.issubset(dockerignore)


def test_production_requires_one_consistent_browser_edge() -> None:
    production = _read("infra/compose/compose.production.yml")
    caddy = _read("infra/compose/caddy/Caddyfile.production")
    installer = _read("scripts/install.sh")
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    compose_helper = _read("infra/appliance/provision/bin/easysynq-compose")
    reconfigure = _read("infra/appliance/provision/bin/easysynq-reconfigure")
    template = _read(".env.example")

    for key in (
        "SITE_ADDRESS",
        "MINIO_SITE_ADDRESS",
        "S3_PUBLIC_ENDPOINT",
        "PUBLIC_BASE_URL",
        "APP_BASE_URL",
        "KEYCLOAK_HOSTNAME",
    ):
        assert f"${{{key}:?" in production, f"production overlay does not require {key}"

    assert "{$MINIO_SITE_ADDRESS}" in caddy
    assert "{$CADDY_TLS_DIRECTIVE}" in caddy
    assert "reverse_proxy minio:9000" in caddy

    assert 'MINIO_ORIGIN="https://${HOST_NAME}:9443"' in installer
    assert "${APP_ORIGIN}/*" in installer
    assert "easysynq-web client" in installer
    assert "easysynq_api.cli.keycloak_redirect" in installer
    assert "easysynq_api.cli.keycloak_redirect" in reconfigure
    assert "redirectUris=[" not in installer
    assert "redirectUris=[" not in reconfigure
    assert "compose.production.yml" in installer
    assert "validate-browser-origins.sh" in installer
    assert "validate-browser-origins.sh" in provisioner
    assert "validate-browser-origins.sh" in compose_helper
    assert "validate-browser-origins.sh" in reconfigure
    assert "validate-dns-name.sh" in installer
    assert "validate-dns-name.sh" in reconfigure
    assert "S3_PUBLIC_ENDPOINT=http://localhost:9000" not in template
    assert "APP_BASE_URL=" in template


def test_appliance_propagates_qr_share_and_deep_link_origins() -> None:
    provision = _read("infra/appliance/provision/easysynq-provision.sh")
    reconfigure = _read("infra/appliance/provision/bin/easysynq-reconfigure")
    compose_helper = _read("infra/appliance/provision/bin/easysynq-compose")

    for key in ("PUBLIC_BASE_URL", "APP_BASE_URL"):
        assert f'set_kv {key} "https://${{HOSTNAME_DEFAULT}}"' in provision
        assert f'set_kv {key} "https://${{HOST}}"' in reconfigure

    # The day-2 helper defines HOST, not HOSTNAME_DEFAULT; this guards the original set -u trap.
    host_updates = reconfigure[reconfigure.index("set_kv SITE_ADDRESS") :]
    assert "HOSTNAME_DEFAULT" not in host_updates

    # Pre-Batch-13 appliance env files are read-only to the helper user. The helper derives these
    # values in-process for the first upgrade instead of failing production-overlay interpolation.
    assert "for key in PUBLIC_BASE_URL APP_BASE_URL KEYCLOAK_HOSTNAME" in compose_helper
    assert "up_needs_keycloak_migration" in compose_helper
    assert 'targets+=("$arg")' in compose_helper
    assert '[ "$target" = "keycloak" ]' in compose_helper


def test_appliance_first_administrator_setup_sheet_uses_in_app_provisioning() -> None:
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    setup_helper = ROOT / "infra/appliance/provision/bin/easysynq-create-user"
    post_ready_handoff = _post_ready_handoff_segment(provisioner)

    _assert_approved_post_ready_handoff(post_ready_handoff)

    setup_sheet = _extract_setup_sheet(provisioner)
    _assert_setup_sheet_is_secret_only(setup_sheet)
    _assert_no_human_identity_actions(_post_ready_provision_actions(provisioner))

    for unsafe_line in (
        "Administrator username: alternate-admin",
        "Temporary password: alternate-password-123",
        "    ${first_admin_password}",
    ):
        with pytest.raises(AssertionError):
            _assert_setup_sheet_is_secret_only(
                "Application URL: https://${HOSTNAME_DEFAULT}/setup\n"
                "Your one-time setup secret (EasySynQ):\n"
                "    ${secret}\n"
                f"{unsafe_line}\n"
            )

    unsafe_actions = {
        "single-line-kcadm-create": "kcadm.sh create users -r easysynq -s username=alternate-admin",
        "single-line-kcadm-password": (
            "kcadm.sh set-password -r easysynq --username alternate-admin --new-password secret"
        ),
        "os-user-create": "useradd alternate-admin",
        "os-password-set": "passwd alternate-admin",
        "retired-helper": "easysynq-create-user alternate-admin",
        "named-keycloak-helper": "bash scripts/new-keycloak-user.sh alternate-admin",
        "credential-payload": (
            'curl -d \'{"username": "alternate-admin", "credentials": [{"value": "secret"}]}\''
        ),
        "multiline-kcadm-create": "kcadm.sh \\\n  create \\\n  users -r easysynq",
        "multiline-curl-credential": (
            "curl \\\n"
            "  https://keycloak.example/admin/realms/easysynq/users/123/reset-password \\\n"
            "  -X PUT"
        ),
        "renamed-helper": "bash scripts/seed-initial-account.sh",
        "direct-renamed-helper": '"$APP_DIR/scripts/seed-initial-account.sh"',
        "relative-direct-helper": "./scripts/seed-initial-account.sh",
        "absolute-direct-helper": "/opt/easysynq/scripts/seed-initial-account.sh",
        "parent-relative-direct-helper": "../seed-initial-account.sh",
        "relative-bin-direct-helper": "./bin/seed-initial-account.sh",
        "absolute-bin-direct-helper": "/opt/easysynq/bin/seed-initial-account.sh",
        "venv-python-arbitrary-module": "/opt/easysynq/.venv/bin/python -m site",
        "mixed-pipeline-python": (
            "/opt/easysynq/.venv/bin/python -m site | "
            "python -m easysynq_api.cli.setup mint-bootstrap"
        ),
        "sudo-helper": "sudo /opt/easysynq/bin/seed-initial-account.sh",
        "env-helper": "env X=y ./bin/seed-initial-account.sh",
        "command-helper": "command /opt/easysynq/bin/seed-initial-account.sh",
    }
    # The semantic parser remains exercised as diagnostic defense-in-depth; the fingerprint below
    # is authoritative because Bash pipelines and modifiers are outside this lightweight parser.
    for unsafe_action in unsafe_actions.values():
        with pytest.raises(AssertionError):
            _assert_approved_post_ready_handoff(f"{post_ready_handoff}\n{unsafe_action}\n")

    _assert_no_human_identity_actions(
        "KEYCLOAK_ADMIN=service-admin\n"
        "KEYCLOAK_ADMIN_PASSWORD=internal-secret\n"
        "kcadm.sh update clients/easysynq-web -s 'redirectUris=[\"https://app.example/*\"]'\n"
        "python -m easysynq_api.cli.keycloak_redirect --origin https://app.example\n"
    )
    _assert_no_human_identity_actions('echo "see ./scripts/seed-initial-account.sh"')
    _assert_no_human_identity_actions('echo "see /opt/easysynq/bin/seed-initial-account.sh"')
    _assert_no_human_identity_actions(
        '"/opt/easysynq/.venv/bin/python" -m easysynq_api.cli.setup mint-bootstrap'
    )

    assert 'install -m 600 -o easysynq -g easysynq /dev/null "$SETUP_FILE"' in provisioner
    assert not setup_helper.exists()


def test_current_install_docs_keep_first_administrator_creation_in_app() -> None:
    current_docs = (
        "docs/runbooks/appliance-install.md",
        "docs/runbooks/install-online.md",
        "docs/runbooks/install-ubuntu-server.md",
        "docs/manuals/installation-guide.md",
        "docs/manuals/administrator-it-manual.md",
        "docs/08-setup-and-onboarding.md",
        "docs/15-api-design.md",
        "docs/dev-workflow.md",
    )

    _assert_no_retired_normal_flow_docs({path: _read(path) for path in current_docs})

    unsafe_normal_flow = {
        "plain-keycloak-user": "Create a Keycloak user before opening EasySynQ.",
        "administrator-identity": "Create or federate the intended administrator identity first.",
        "subject-copy": "Copy the Keycloak subject and paste it into the setup form.",
        "retired-endpoint": "Use POST /setup/bootstrap to create the first administrator.",
        "inline-code-instruction": "Run `create a Keycloak user` before opening EasySynQ.",
        "deceptive-negation": "Do not delay: create a Keycloak user before opening EasySynQ.",
        "line-mentions-break-glass": "This is not break-glass: create a Keycloak user.",
        "normal-heading-next-to-break-glass": (
            "## Normal installation (not break-glass)\n"
            "Create a Keycloak user before opening EasySynQ.\n"
            "## Break-glass recovery\n"
            "Create a Keycloak user only to recover an orphan.\n"
        ),
    }
    rejected_docs: dict[str, bool] = {}
    for name, content in unsafe_normal_flow.items():
        try:
            _assert_no_retired_normal_flow_docs({"docs/current-install.md": content})
        except AssertionError:
            rejected_docs[name] = True
        else:
            rejected_docs[name] = False
    assert all(rejected_docs.values()), (
        f"normal-flow documentation mutations bypassed the guard: {rejected_docs}"
    )

    _assert_no_retired_normal_flow_docs(
        {
            "docs/current-install.md": (
                "## Break-glass and orphan recovery\n"
                "Create a Keycloak user and copy the Keycloak subject.\n"
            )
        }
    )

    setup_and_onboarding = _read("docs/08-setup-and-onboarding.md")
    assert "current setup secret" in setup_and_onboarding
    assert "bootstrap_credential_superseded" in setup_and_onboarding


def test_fresh_linux_first_run_creates_the_administrator_before_dev_fixtures() -> None:
    runbook = _read("docs/runbooks/fresh-linux-setup.md")
    first_run = _markdown_section(runbook, "6. First-run wizard → OPERATIONAL")
    fixture_section = _markdown_section(
        runbook, "7. Post-bootstrap development fixtures (Keycloak persists in PostgreSQL)"
    )

    _assert_supported_first_admin_sequence(first_run, "`http://localhost/setup`")
    assert "only after first-administrator setup is complete" in fixture_section
    assert "just demo-user" in fixture_section
    assert "Demo-Password-1" in fixture_section


def test_installation_guide_first_runs_never_sign_in_with_a_demo_identity() -> None:
    guide = _read("docs/manuals/installation-guide.md")
    production_first_run = _markdown_section(
        guide, "4.3 Create the first administrator in EasySynQ"
    )
    developer_first_run = _markdown_section(guide, "8.1 Create the first administrator")
    developer_fixtures = _markdown_section(
        guide, "8.2 Optional post-bootstrap development fixtures"
    )

    for section, setup_url in (
        (production_first_run, "`https://<host>/setup`"),
        (developer_first_run, "`http://localhost/setup`"),
    ):
        _assert_supported_first_admin_sequence(section, setup_url)

    assert "only after first-administrator setup is complete" in developer_fixtures
    assert "just demo-user" in developer_fixtures
    assert "Demo-Password-1" in developer_fixtures

    valid_sequence = (
        "Run mint-bootstrap. Open `http://localhost/setup` without signing in, then create the "
        "first administrator profile, copy the shown-once temporary password, acknowledge the "
        "active credential generation, sign in, and change the temporary password."
    )
    _assert_supported_first_admin_sequence(valid_sequence, "`http://localhost/setup`")
    unsafe_first_runs = {
        "demo-command": f"Run just demo-user first. {valid_sequence}",
        "fixed-demo-credential": f"Use demo / Demo-Password-1. {valid_sequence}",
        "sign-in-before-setup": f"Sign in first. {valid_sequence}",
        "reviewer-before-please-sign-in": (f"Before setup, please sign in. {valid_sequence}"),
        "reviewer-to-continue-sign-in": f"To continue, sign in. {valid_sequence}",
        "hyphenated-sign-in": f"Complete sign-in first. {valid_sequence}",
        "log-in-before-setup": f"Before setup, log in. {valid_sequence}",
        "login-before-setup": f"Use the login first. {valid_sequence}",
    }
    rejected_mutations: dict[str, bool] = {}
    for name, mutation in unsafe_first_runs.items():
        try:
            _assert_supported_first_admin_sequence(mutation, "`http://localhost/setup`")
        except AssertionError:
            rejected_mutations[name] = True
        else:
            rejected_mutations[name] = False
    assert all(rejected_mutations.values()), (
        f"unsupported first-run mutations bypassed the guard: {rejected_mutations}"
    )


def test_host_setup_exposes_release_administrator_blocker(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from easysynq_api.cli import setup as setup_cli

    command = "release-administrator-blocker --subject <keycloak-subject> [--org CODE]"
    wrapper_help = subprocess.run(  # noqa: S603 - fixed repository-owned executable
        [str(ROOT / "scripts/easysynq"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert command in wrapper_help
    _assert_fragments_in_order(
        wrapper_help,
        (
            "Mint the one-time first-run bootstrap secret",
            "open /setup without signing in",
            "create the administrator profile",
            "copy the temporary password",
            "acknowledge the active credential generation",
            "sign in and change the password",
        ),
    )
    recovery_help = re.sub(r"\s+", " ", wrapper_help[wrapper_help.index(command) :])
    assert "pre-operational" in recovery_help
    assert "remove only the named unrelated System Administrator assignment" in recovery_help
    assert "independent incident/change record" in recovery_help

    with pytest.raises(SystemExit) as help_exit:
        setup_cli.main(["--help"])
    assert help_exit.value.code == 0
    setup_help = capsys.readouterr().out
    assert "release-administrator-blocker" in setup_help


def test_setup_cli_dispatches_release_administrator_blocker_values(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from easysynq_api.cli import setup as setup_cli

    received: list[tuple[str, str]] = []

    def observed_release(subject: str, org_short_code: str) -> str:
        received.append((subject, org_short_code))
        return "stubbed release result"

    monkeypatch.setattr(setup_cli, "release_administrator_blocker", observed_release)

    result = setup_cli.main(
        [
            "release-administrator-blocker",
            "--subject",
            "subject:test-dispatch",
            "--org",
            "RECOVERY",
        ]
    )

    assert result == 0
    assert received == [("subject:test-dispatch", "RECOVERY")]
    assert capsys.readouterr().out.splitlines() == [
        "stubbed release result",
        "Record this host recovery in an independent incident/change record.",
    ]


def test_host_wrapper_forwards_release_administrator_blocker_argv(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_argv = tmp_path / "docker-argv"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$EASYSYNQ_CAPTURE"\n')
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["EASYSYNQ_CAPTURE"] = str(captured_argv)

    subprocess.run(  # noqa: S603 - repository wrapper with a controlled fake docker executable
        [
            str(ROOT / "scripts/easysynq"),
            "setup",
            "release-administrator-blocker",
            "--subject",
            "subject:wrapper-dispatch",
            "--org",
            "RECOVERY",
        ],
        check=True,
        env=env,
    )

    assert captured_argv.read_text().splitlines()[-13:] == [
        "run",
        "--rm",
        "api",
        "uv",
        "run",
        "python",
        "-m",
        "easysynq_api.cli.setup",
        "release-administrator-blocker",
        "--subject",
        "subject:wrapper-dispatch",
        "--org",
        "RECOVERY",
    ]


def test_keycloak_runs_optimized_on_durable_postgres_schema() -> None:
    compose = _read("infra/compose/compose.yml")
    image = _read("infra/compose/keycloak/Dockerfile")
    init = _read("infra/compose/keycloak/keycloak-init.sh")
    migration = _read("scripts/migrate-keycloak-h2.sh")
    justfile = _read("justfile")
    restore_runbook = _read("docs/runbooks/backup-restore.md")
    template = _read(".env.example")

    assert "start-dev" not in compose
    assert 'command: ["start", "--optimized", "--import-realm"]' in compose
    assert "KC_DB: postgres" in compose
    assert "KEYCLOAK_DB_NAME:-${POSTGRES_DB" in compose
    assert "KC_DB_SCHEMA: keycloak" in compose
    assert "KC_DB_USERNAME: easysynq_keycloak" in compose
    assert compose.count("KEYCLOAK_DB_PASSWORD:?set a distinct KEYCLOAK_DB_PASSWORD") == 2
    assert "KEYCLOAK_DB_PASSWORD:-${POSTGRES_PASSWORD" not in compose
    assert "keycloakimport:/opt/keycloak/data/import:ro" in compose
    assert "condition: service_completed_successfully" in compose

    assert "RUN /opt/keycloak/bin/kc.sh build" in image
    assert "CREATE ROLE easysynq_keycloak LOGIN" in init
    assert "CREATE SCHEMA keycloak AUTHORIZATION easysynq_keycloak" in init
    assert "ALTER %s %I.%I OWNER TO easysynq_keycloak" in init
    assert "ALL TABLES IN SCHEMA keycloak" in init
    assert "KEYCLOAK_DB_NAME=" in template
    assert "`KEYCLOAK_DB_NAME`" in restore_runbook
    assert "restored `keycloak` schema objects" in restore_runbook

    # A transition must export users/credential hashes before the legacy container is replaced.
    assert "docker stop --time 60" in migration
    assert "--users realm_file" in migration
    assert ".legacy-h2-export-complete" in migration
    assert "com.docker.compose.volume=keycloakimport" in migration
    assert 'IMPORT_VOLUME="${IMPORT_VOLUME:-${PROJECT}_keycloakimport}"' in migration
    assert (
        'grep -q "\\"users\\"[[:space:]]*:" /migration-export/easysynq-realm.json &&' in migration
    )
    assert "restarting the untouched legacy container" in migration
    assert "name: easysynq-keycloak-import" not in compose
    assert 'legs.realm_export = "present"' in restore_runbook
    assert "<compose-project>_keycloakimport" in restore_runbook
    assert "before the first Keycloak start" in restore_runbook
    assert justfile.count("ensure-keycloak-db-password.sh --env-file .env") == 3
    assert justfile.index("ensure-keycloak-db-password.sh") < justfile.index(
        "migrate-keycloak-h2.sh"
    )


def test_h2_migration_reads_custom_compose_project_from_env_file(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        "# `docker ps` intentionally returns no container IDs.\n"
    )
    fake_docker.chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text('COMPOSE_PROJECT_NAME="customer-qms"\n')
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        [
            "/bin/bash",
            str(ROOT / "scripts/migrate-keycloak-h2.sh"),
            "--env-file",
            str(env_file),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "no legacy container found" in result.stdout
    assert "label=com.docker.compose.project=customer-qms" in docker_log.read_text()


def test_h2_migration_fails_closed_when_container_discovery_errors(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1" = "ps" ]; then\n  printf "daemon unavailable\\n" >&2\n  exit 1\nfi\n'
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        ["/bin/bash", str(ROOT / "scripts/migrate-keycloak-h2.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "could not inspect legacy containers; refusing to continue" in result.stderr
    assert "no legacy container found" not in result.stdout


def test_h2_migration_scopes_import_volume_to_compose_project(tmp_path: Path) -> None:
    docker_log = tmp_path / "docker.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
printf "%s\\n" "$*" >> "$DOCKER_LOG"
if [ "$1" = "ps" ]; then
  printf "legacy-id\\n"
elif [ "$1 $2" = "inspect --format" ] && [ "$3" = "{{.Image}}" ]; then
  printf "sha256:legacy\\n"
elif [ "$1 $2" = "volume inspect" ]; then
  exit 1
fi
"""
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
    }

    result = subprocess.run(  # noqa: S603 - fixed test script and isolated fake PATH
        [
            "/bin/bash",
            str(ROOT / "scripts/migrate-keycloak-h2.sh"),
            "--project",
            "customer-qms",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "verified legacy export already staged" in result.stdout
    create = next(
        line for line in docker_log.read_text().splitlines() if line.startswith("volume create ")
    )
    assert "com.docker.compose.project=customer-qms" in create
    assert "com.docker.compose.volume=keycloakimport" in create
    assert create.endswith(" customer-qms_keycloakimport")


def test_installer_rejects_invalid_dns_labels_before_deployment() -> None:
    for hostname in (
        "qms.-corp.example",
        "qms-.corp.example",
        ".qms.corp.example",
        "qms.corp.example.",
        f"{'a' * 64}.example",
    ):
        result = subprocess.run(  # noqa: S603 - fixed repository installer
            [
                "/bin/bash",
                str(ROOT / "scripts/install.sh"),
                "s",
                "--host",
                hostname,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 2
        assert "must be a valid DNS name" in result.stderr


def test_production_browser_origin_validator_rejects_both_tuple_mismatches(
    tmp_path: Path,
) -> None:
    checker = ROOT / "scripts/validate-browser-origins.sh"
    env_file = tmp_path / ".env"
    values = {
        "SITE_ADDRESS": "https://qms.example.com",
        "PUBLIC_BASE_URL": "https://qms.example.com",
        "APP_BASE_URL": "https://qms.example.com",
        "KEYCLOAK_HOSTNAME": "https://qms.example.com",
        "MINIO_SITE_ADDRESS": "https://qms.example.com:9443",
        "S3_PUBLIC_ENDPOINT": "https://qms.example.com:9443",
    }
    browser_keys = set(values)

    def check(overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        rendered = {**values, **overrides}
        env_file.write_text("".join(f"{key}={value}\n" for key, value in rendered.items()))
        clean_env = {key: value for key, value in os.environ.items() if key not in browser_keys}
        return subprocess.run(  # noqa: S603 - fixed checker against an isolated env file
            ["/bin/bash", str(checker), "--env-file", str(env_file)],
            cwd=ROOT,
            env=clean_env,
            capture_output=True,
            text=True,
            check=False,
        )

    consistent = check({})
    assert consistent.returncode == 0, consistent.stderr

    app_mismatch = check({"APP_BASE_URL": "https://stale.example.com"})
    assert app_mismatch.returncode != 0
    assert "APP_BASE_URL must exactly equal SITE_ADDRESS" in app_mismatch.stderr

    minio_mismatch = check({"S3_PUBLIC_ENDPOINT": "https://stale.example.com:9443"})
    assert minio_mismatch.returncode != 0
    assert "S3_PUBLIC_ENDPOINT must exactly equal MINIO_SITE_ADDRESS" in minio_mismatch.stderr


def test_keycloak_db_password_backfill_is_distinct_persistent_and_idempotent(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_PASSWORD=owner-secret\nKEYCLOAK_DB_PASSWORD=owner-secret\n")
    helper = ROOT / "scripts/ensure-keycloak-db-password.sh"

    env_file.chmod(0o440)
    read_only = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert read_only.returncode != 0
    assert "rerun this first Batch 13 command with sudo" in read_only.stderr
    assert "KEYCLOAK_DB_PASSWORD=owner-secret" in env_file.read_text()

    env_file.chmod(0o640)
    first = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    first_value = next(
        line.split("=", 1)[1]
        for line in env_file.read_text().splitlines()
        if line.startswith("KEYCLOAK_DB_PASSWORD=")
    )
    assert first_value
    assert first_value != "owner-secret"
    assert env_file.stat().st_mode & 0o777 == 0o640

    second = subprocess.run(  # noqa: S603 - fixed helper against an isolated env file
        ["/bin/bash", str(helper), "--env-file", str(env_file)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert f"KEYCLOAK_DB_PASSWORD={first_value}" in env_file.read_text()


def test_dev_keycloak_hostname_tracks_nondefault_http_port(tmp_path: Path) -> None:
    browser_keys = {
        "HTTP_PORT",
        "KEYCLOAK_HOSTNAME",
        "KEYCLOAK_DB_PASSWORD",
    }

    compose_dir = tmp_path / "infra" / "compose"
    compose_dir.mkdir(parents=True)
    for name in ("compose.yml", "compose.s.yml", "compose.dev.yml"):
        shutil.copyfile(ROOT / "infra" / "compose" / name, compose_dir / name)
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env")
    shutil.copyfile(ROOT / ".env.example", tmp_path / ".env.example")

    def render(http_port: str | None) -> str:
        docker = shutil.which("docker")
        assert docker is not None
        env = {key: value for key, value in os.environ.items() if key not in browser_keys}
        env["KEYCLOAK_DB_PASSWORD"] = "keycloak-secret"
        if http_port is not None:
            env["HTTP_PORT"] = http_port
        result = subprocess.run(  # noqa: S603 - resolved Docker binary; no daemon/network
            [
                docker,
                "compose",
                "--env-file",
                str(tmp_path / ".env.example"),
                "-f",
                str(compose_dir / "compose.yml"),
                "-f",
                str(compose_dir / "compose.s.yml"),
                "-f",
                str(compose_dir / "compose.dev.yml"),
                "config",
                "--format",
                "json",
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        rendered = json.loads(result.stdout)
        return rendered["services"]["keycloak"]["environment"]["KC_HOSTNAME"]

    assert render(None) == "http://localhost"
    assert render("8088") == "http://localhost:8088"


def test_dev_overlay_relabels_only_repository_bind_mounts_for_selinux() -> None:
    docker = shutil.which("docker")
    assert docker is not None

    expected_dev_binds = {
        "minio-init": {"/init": "./minio:/init:ro,z"},
        "keycloak-init": {
            "/init/keycloak-init.sh": "./keycloak/keycloak-init.sh:/init/keycloak-init.sh:ro,z",
            "/seed/easysynq-realm.json": (
                "./keycloak/realm-export.json:/seed/easysynq-realm.json:ro,z"
            ),
        },
        "worker": {
            "/srv/import/source": (
                "${IMPORT_SOURCE_PATH:-../../.import-source}:/srv/import/source:ro,z"
            )
        },
        "proxy": {"/etc/caddy/Caddyfile": "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro,z"},
    }
    dev = yaml.safe_load(_read("infra/compose/compose.dev.yml"))
    assert {
        service: {entry.rsplit(":", 2)[1]: entry for entry in config["volumes"]}
        for service, config in dev["services"].items()
        if "volumes" in config
    } == expected_dev_binds

    clean_env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "KEYCLOAK_DB_PASSWORD",
            "SITE_ADDRESS",
            "MINIO_SITE_ADDRESS",
            "S3_PUBLIC_ENDPOINT",
            "PUBLIC_BASE_URL",
            "APP_BASE_URL",
            "KEYCLOAK_HOSTNAME",
        }
    }
    clean_env["KEYCLOAK_DB_PASSWORD"] = "proof-only-keycloak-secret"

    def render(*overlays: str, production: bool = False) -> dict[str, object]:
        env = dict(clean_env)
        if production:
            env.update(
                {
                    "SITE_ADDRESS": "https://qms.example.test",
                    "MINIO_SITE_ADDRESS": "https://qms.example.test:9443",
                    "S3_PUBLIC_ENDPOINT": "https://qms.example.test:9443",
                    "PUBLIC_BASE_URL": "https://qms.example.test",
                    "APP_BASE_URL": "https://qms.example.test",
                    "KEYCLOAK_HOSTNAME": "https://qms.example.test",
                }
            )
        command = [
            docker,
            "compose",
            "--env-file",
            str(ROOT / ".env.example"),
            "-f",
            str(ROOT / "infra/compose/compose.yml"),
        ]
        for overlay in overlays:
            command.extend(["-f", str(ROOT / f"infra/compose/{overlay}")])
        command.extend(["config", "--no-env-resolution", "--format", "json"])
        result = subprocess.run(  # noqa: S603 - resolved Docker binary; config rendering only
            command,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    named_targets = {
        "keycloak-init": {"/import": "keycloakimport"},
        "worker": {
            "/var/lib/easysynq/qms-mirror": "mirror",
            "/run/secrets": "secrets",
            "/var/lib/easysynq/backups": "backup",
        },
        "proxy": {"/data": "caddydata", "/config": "caddyconfig"},
    }
    for sizing in ("compose.s.yml", "compose.m.yml"):
        rendered = render(sizing, "compose.dev.yml")
        services = rendered["services"]
        for service, targets in expected_dev_binds.items():
            volumes = {volume["target"]: volume for volume in services[service]["volumes"]}
            for target in targets:
                assert volumes[target]["type"] == "bind"
                assert volumes[target]["read_only"] is True
                assert volumes[target]["bind"]["selinux"] == "z"
        for service, targets in named_targets.items():
            volumes = {volume["target"]: volume for volume in services[service]["volumes"]}
            assert {
                target: volumes[target]["source"]
                for target in targets
                if volumes[target]["type"] == "volume"
            } == targets

    production_source = _read("infra/compose/compose.production.yml")
    assert ":z" not in production_source
    assert ",z" not in production_source
    for sizing in ("compose.s.yml", "compose.m.yml"):
        production = render(sizing, "compose.production.yml", production=True)
        for service in production["services"].values():
            for volume in service.get("volumes", []):
                if volume["type"] == "bind":
                    assert "selinux" not in volume["bind"]


def test_production_entrypoints_require_compose_2_24_4(tmp_path: Path) -> None:
    checker = ROOT / "scripts/require-compose-version.sh"

    def check(version: str) -> subprocess.CompletedProcess[str]:
        fake_bin = tmp_path / version.replace(".", "_")
        fake_bin.mkdir()
        fake_docker = fake_bin / "docker"
        fake_docker.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
        fake_docker.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        }
        return subprocess.run(  # noqa: S603 - fixed checker and isolated fake PATH
            ["/bin/bash", str(checker)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    unsupported = check("2.24.3")
    assert unsupported.returncode != 0
    assert "2.24.4 or newer is required" in unsupported.stderr

    supported = check("2.24.4")
    assert supported.returncode == 0, supported.stderr

    installer = _read("scripts/install.sh")
    appliance = _read("infra/appliance/provision/bin/easysynq-compose")
    provisioner = _read("infra/appliance/provision/easysynq-provision.sh")
    assert 'bash "$ROOT/scripts/require-compose-version.sh"' in installer
    assert 'bash "$COMPOSE_VERSION_SCRIPT"' in appliance
    assert 'bash "$APP_DIR/scripts/require-compose-version.sh"' in provisioner


def test_appliance_targeted_up_only_migrates_when_keycloak_will_start(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        '#!/bin/sh\nif [ "$1 $2 $3" = "compose version --short" ]; then\n  printf "2.24.4\\n"\nfi\n'
    )
    fake_docker.chmod(0o755)
    migration_log = tmp_path / "migration.log"
    migration_args_log = tmp_path / "migration-args.log"
    fake_migration = tmp_path / "migrate.sh"
    fake_migration.write_text(
        '#!/bin/sh\nprintf "migrated\\n" >> "$MIGRATION_LOG"\n'
        'printf "%s\\n" "$*" >> "$MIGRATION_ARGS_LOG"\n'
    )
    fake_migration.chmod(0o755)
    (tmp_path / ".env").write_text(
        "POSTGRES_PASSWORD=owner-secret\nSITE_ADDRESS=https://easysynq.local\n"
    )
    browser_keys = {
        "SITE_ADDRESS",
        "MINIO_SITE_ADDRESS",
        "S3_PUBLIC_ENDPOINT",
        "PUBLIC_BASE_URL",
        "APP_BASE_URL",
        "KEYCLOAK_HOSTNAME",
    }
    env = {
        **{key: value for key, value in os.environ.items() if key not in browser_keys},
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "EASYSYNQ_APP_DIR": str(tmp_path),
        "EASYSYNQ_MIGRATION_SCRIPT": str(fake_migration),
        "EASYSYNQ_COMPOSE_VERSION_SCRIPT": str(ROOT / "scripts/require-compose-version.sh"),
        "EASYSYNQ_BROWSER_ORIGIN_SCRIPT": str(ROOT / "scripts/validate-browser-origins.sh"),
        "EASYSYNQ_KEYCLOAK_DB_PASSWORD_SCRIPT": str(
            ROOT / "scripts/ensure-keycloak-db-password.sh"
        ),
        "MIGRATION_LOG": str(migration_log),
        "MIGRATION_ARGS_LOG": str(migration_args_log),
    }
    wrapper = ROOT / "infra/appliance/provision/bin/easysynq-compose"

    def run_wrapper(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed repository wrapper and isolated fake PATH
            ["/bin/bash", str(wrapper), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    result = run_wrapper("up", "-d", "api")
    assert result.returncode == 0, result.stderr
    assert not migration_log.exists()
    env_after_backfill = (tmp_path / ".env").read_text()
    assert "KEYCLOAK_DB_PASSWORD=" in env_after_backfill
    assert "KEYCLOAK_DB_PASSWORD=owner-secret" not in env_after_backfill

    result = run_wrapper("up", "-d", "keycloak", "api")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated"]

    result = run_wrapper("up", "--timeout", "60", "-d")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated", "migrated"]

    result = run_wrapper("-p", "customer-qms", "up", "keycloak")
    assert result.returncode == 0, result.stderr
    assert migration_log.read_text().splitlines() == ["migrated", "migrated", "migrated"]
    assert migration_args_log.read_text().splitlines() == [
        "--env-file .env",
        "--env-file .env",
        "--env-file .env --project customer-qms",
    ]
