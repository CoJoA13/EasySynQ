"""S11 security-header scan (doc 12 §6.1, doc 18 §8 'security-header scan').

CI does not run Caddy, so the honest CI-level assurance is a static assertion over the committed
Caddyfile: the TLS floor + the site-wide headers + the strict static CSP (default-src 'self',
object-src 'none', frame-ancestors 'none', base-uri 'self') are present and correct. A live
``curl -sI`` + ``openssl s_client -tls1_1``-refusal check is the operator's release-time proof (the
install runbook). A regression/typo here fails the ``api`` job's ``pytest -m unit``.
"""

from __future__ import annotations

from pathlib import Path


def _caddyfile() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "infra" / "compose" / "caddy" / "Caddyfile"
        if candidate.exists():
            return candidate.read_text()
    raise AssertionError("Caddyfile not found above the test directory")


def test_site_headers_and_tls_posture_present() -> None:
    text = _caddyfile()
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in text
    assert 'X-Content-Type-Options "nosniff"' in text
    # doc 12 §6.1 reconciliation: strict-origin-when-cross-origin (was no-referrer)
    assert 'Referrer-Policy "strict-origin-when-cross-origin"' in text
    assert "Permissions-Policy" in text
    assert "-Server" in text
    # TLS: Caddy's DEFAULT is a TLS 1.2 floor + modern AEAD ciphers — an explicit `tls{protocols}`
    # block is INVALID on the plain-HTTP :80 dev listener (caddy validate refuses it), so it is not
    # set; the air-gap internal issuer is wired via the CADDY_TLS_DIRECTIVE placeholder.
    assert "{$CADDY_TLS_DIRECTIVE}" in text
    assert "tls" in text.lower() and "TLS 1.2 floor" in text  # the documented default posture


def test_strict_static_csp_present_with_required_directives() -> None:
    text = _caddyfile()
    assert "Content-Security-Policy" in text
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
        "form-action 'self'",
    ):
        assert directive in text, f"CSP missing: {directive}"
    # script-src must NOT permit unsafe-inline / unsafe-eval (the Vite build has no inline scripts);
    # 'unsafe-inline' is allowed for STYLE only (Mantine runtime <style>), never for script.
    assert "'unsafe-eval'" not in text
    assert "script-src 'self' 'unsafe-inline'" not in text
    assert "script-src 'self';" in text  # script-src is strictly 'self'
    assert "style-src 'self' 'unsafe-inline'" in text  # the documented style-only fallback
